"""One command for the data pipeline: ingest → normalize → dbt build (models + tests) → detect anomalies.

    python -m eurogrid.pipeline run                   # incremental: from the day after the newest price to yesterday
    python -m eurogrid.pipeline run --start 2026-10-01 --end 2026-10-31
    python -m eurogrid.pipeline status                # recent runs and their stages

Every run and stage is recorded in the ingestion ledger (``pipeline_run`` / ``pipeline_stage`` in
``data/metadata/ingestion_runs.sqlite3``) with status, timing and details; Data Operations shows them.

Failure rules:

* a stage that fails stops the run; later stages are recorded as ``not_run`` (dbt never builds on a failed load);
* ingestion that loses some requests but not all is ``partial``: what was fetched is still normalized and the
  coverage matrix shows the gap, rather than silently pretending the window is complete;
* any failing dbt model or test fails the run, so detection never runs on data that broke a quality test;
* an up-to-date database makes ingestion ``skipped`` and the run still rebuilds nothing it does not need.

Stop the API while the pipeline normalizes: DuckDB allows one writer at a time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import time
import traceback
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import duckdb

from eurogrid.markets import MARKETS, Market

log = logging.getLogger(__name__)
UTC = timezone.utc
STAGES = ("ingest", "normalize", "dbt", "detect")
DEFAULT_BACKFILL_DAYS = 30
MAX_WINDOW_DAYS = 366


@dataclass
class PipelineConfig:
    database: Path = Path("data/eurogrid.duckdb")
    data_dir: Path = Path("data")
    project_dir: Path = Path("eurogrid_analytics")
    start: date | None = None
    end: date | None = None
    wait_seconds: float = 31.0          # Energy-Charts is rate limited
    detect: bool = True
    markets: tuple[Market, ...] = MARKETS
    today: date | None = None           # injectable for tests

    @property
    def ledger(self) -> Path:
        return self.data_dir / "metadata" / "ingestion_runs.sqlite3"


@dataclass
class RunContext:
    config: PipelineConfig
    window: tuple[date, date] | None = None
    results: dict[str, dict[str, Any]] = field(default_factory=dict)


Stage = Callable[[RunContext], dict[str, Any]]


# ------------------------------------------------------------------ the window
def newest_price_date(database: Path) -> date | None:
    if not database.exists():
        return None
    try:
        with duckdb.connect(str(database), read_only=True) as db:
            value = db.execute("""SELECT CAST(max(timezone('UTC', interval_start_utc)) AS DATE)
                FROM fct_market_price""").fetchone()[0]
    except duckdb.Error:
        return None
    return value


def incremental_window(database: Path, today: date) -> tuple[date, date] | None:
    """From the day after the newest stored price to yesterday (complete days only); None if up to date."""
    end = today - timedelta(days=1)
    newest = newest_price_date(database)
    start = newest + timedelta(days=1) if newest else end - timedelta(days=DEFAULT_BACKFILL_DAYS - 1)
    if start > end:
        return None
    return max(start, end - timedelta(days=MAX_WINDOW_DAYS - 1)), end


# -------------------------------------------------------------------- stages
def ingest_stage(context: RunContext) -> dict[str, Any]:
    from eurogrid.cli import ingest_generation, ingest_price, ingest_weather
    from eurogrid.ingestion import IngestionStore

    config = context.config
    if context.window is None:
        return {"status": "skipped", "reason": f"up to date through {newest_price_date(config.database)}"}
    start, end = (day.isoformat() for day in context.window)
    store = IngestionStore(config.data_dir)
    requests, failures, rows, cache_hits = 0, [], 0, 0
    for index, market in enumerate(config.markets):
        calls: list[tuple[str, Callable[[], Any]]] = []
        if market.bidding_zone:
            calls.append((f"{market.bidding_zone} prices", lambda m=market: ingest_price(store, m.bidding_zone, start, end)))
        calls.append((f"{market.country_code} public power", lambda m=market: ingest_generation(store, m.country_code, start, end)))
        if market.latitude is not None and market.longitude is not None:
            calls.append((f"{market.reference_city} weather", lambda m=market: ingest_weather(
                store, m.latitude, m.longitude, start, end, country_code=m.country_code)))
        for label, call in calls:
            requests += 1
            try:
                result = call()
                rows += result.rows
                cache_hits += int(result.cache_hit)
                log.info("Ingested %s %s..%s: %s rows%s", label, start, end, result.rows,
                         " (cache)" if result.cache_hit else "")
            except Exception as exc:   # recorded per request; the ledger has the full error
                failures.append(f"{label}: {type(exc).__name__}: {exc}"[:300])
                log.warning("Ingestion failed for %s: %s", label, exc)
        if index < len(config.markets) - 1 and config.wait_seconds:
            time.sleep(config.wait_seconds)
    status = "failed" if failures and len(failures) == requests else "partial" if failures else "success"
    return {"status": status, "window": [start, end], "requests": requests, "rows_received": rows,
            "cache_hits": cache_hits, "failures": failures}


def normalize_stage(context: RunContext) -> dict[str, Any]:
    from eurogrid.normalize import Normalizer

    config = context.config
    normalizer = Normalizer(config.database)
    try:
        counts = normalizer.load_directory(config.data_dir / "raw", config.ledger)
    finally:
        normalizer.close()
    problems = counts["unreadable"] + counts["unsupported"]
    return {"status": "success", **counts, **({"warning": f"{problems} raw file(s) skipped"} if problems else {})}


def dbt_stage(context: RunContext) -> dict[str, Any]:
    from eurogrid.chaos import run_dbt

    config = context.config
    if context.results.get("normalize", {}).get("files") == 0 and context.results.get("ingest", {}).get("status") == "skipped":
        return {"status": "skipped", "reason": "no new data since the last run"}
    result = run_dbt(config.database, config.data_dir / "metadata" / "dbt_pipeline", config.project_dir,
                     config.project_dir / "target", freshness=True)
    if not result.get("ran"):
        return {"status": "failed", "reason": result.get("reason")}
    failing = [item["name"] for item in result.get("results", []) if item["status"] in {"fail", "error"}]
    stale = [item["source"] for item in result.get("freshness", []) if item["status"] in {"warn", "error"}]
    status = "failed" if failing or result["exit_code"] else "partial" if stale else "success"
    return {"status": status, "counts": result.get("counts"), "failing": failing, "exit_code": result["exit_code"],
            "freshness": {item["source"]: item["status"] for item in result.get("freshness", [])},
            **({"reason": f"sources older than the freshness limit: {', '.join(stale)}"} if stale else {})}


def detect_stage(context: RunContext) -> dict[str, Any]:
    from eurogrid.investigation import CaseStore, detect_price_anomalies
    from eurogrid.investigation_tools import InvestigationTools

    config = context.config
    if not config.detect:
        return {"status": "skipped", "reason": "detection disabled"}
    if context.window is None:
        return {"status": "skipped", "reason": "no new window"}
    tools = InvestigationTools(config.database)
    store = CaseStore(config.data_dir / "metadata" / "investigations.sqlite")
    found = {}
    for zone in sorted(tools.zones()):
        events = detect_price_anomalies(tools, zone, *context.window)
        store.save_events(events)
        found[zone] = len(events)
    return {"status": "success", "window": [day.isoformat() for day in context.window], "events": found}


DEFAULT_STAGES: dict[str, Stage] = {"ingest": ingest_stage, "normalize": normalize_stage, "dbt": dbt_stage,
                                    "detect": detect_stage}


# --------------------------------------------------------------------- ledger
class PipelineStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS pipeline_run (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                    finished_at TEXT, status TEXT NOT NULL, window_start TEXT, window_end TEXT);
                CREATE TABLE IF NOT EXISTS pipeline_stage (run_id TEXT NOT NULL, stage TEXT NOT NULL,
                    status TEXT NOT NULL, started_at TEXT, finished_at TEXT, duration_s REAL, details TEXT,
                    error TEXT, PRIMARY KEY (run_id, stage));""")

    def start(self, run_id: str, started: str, window: tuple[date, date] | None) -> None:
        bounds = (window[0].isoformat(), window[1].isoformat()) if window else (None, None)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO pipeline_run VALUES (?,?,?,?,?,?)", (run_id, started, None, "running", *bounds))

    def stage(self, run_id: str, name: str, status: str, started: str | None, finished: str | None,
              duration: float | None, details: dict[str, Any] | None, error: str | None) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO pipeline_stage VALUES (?,?,?,?,?,?,?,?)",
                       (run_id, name, status, started, finished, duration, json.dumps(details or {}, default=str), error))

    def finish(self, run_id: str, status: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE pipeline_run SET finished_at=?, status=? WHERE run_id=?",
                       (datetime.now(UTC).isoformat(), status, run_id))

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='pipeline_run'").fetchone():
                return []
            runs = db.execute("""SELECT run_id, started_at, finished_at, status, window_start, window_end
                FROM pipeline_run ORDER BY started_at DESC LIMIT ?""", [limit]).fetchall()
            stages = db.execute("SELECT run_id, stage, status, duration_s, details, error FROM pipeline_stage").fetchall()
        by_run: dict[str, list[dict[str, Any]]] = {}
        for run_id, stage, status, duration, details, error in stages:
            by_run.setdefault(run_id, []).append({"stage": stage, "status": status, "duration_s": duration,
                                                  "details": json.loads(details or "{}"), "error": error})
        order = {name: index for index, name in enumerate(STAGES)}
        return [{"run_id": run_id, "started_at": started, "finished_at": finished, "status": status,
                 "window": [start, end] if start else None,
                 "stages": sorted(by_run.get(run_id, []), key=lambda item: order.get(item["stage"], 99))}
                for run_id, started, finished, status, start, end in runs]


# ------------------------------------------------------------------------ run
def run_pipeline(config: PipelineConfig, stages: dict[str, Stage] | None = None) -> dict[str, Any]:
    stages = stages or DEFAULT_STAGES
    today = config.today or datetime.now(UTC).date()
    if config.start and config.end:
        if config.start > config.end or (config.end - config.start).days + 1 > MAX_WINDOW_DAYS:
            raise ValueError(f"choose start <= end and at most {MAX_WINDOW_DAYS} days")
        window = (config.start, config.end)
    else:
        window = incremental_window(config.database, today)
    context = RunContext(config, window)
    started = datetime.now(UTC).isoformat()
    run_id = "pl_" + hashlib.sha256(started.encode()).hexdigest()[:12]
    store = PipelineStore(config.ledger)
    store.start(run_id, started, window)
    log.info("Pipeline %s started; window %s", run_id, window or "none (up to date)")
    run_status, stopped = "success", False
    for name in STAGES:
        if stopped:
            store.stage(run_id, name, "not_run", None, None, None, {"reason": "an earlier stage failed"}, None)
            continue
        stage_started, clock = datetime.now(UTC).isoformat(), time.perf_counter()
        error = None
        try:
            details = stages[name](context)
        except Exception as exc:
            details = {"status": "failed"}
            error = f"{type(exc).__name__}: {exc}\n" + traceback.format_exc(limit=3)
            log.exception("Stage %s failed", name)
        status = details.pop("status", "success")
        context.results[name] = {"status": status, **details}
        store.stage(run_id, name, status, stage_started, datetime.now(UTC).isoformat(),
                    round(time.perf_counter() - clock, 3), details, error)
        log.info("Stage %s: %s (%.1f s)", name, status, time.perf_counter() - clock)
        if status == "failed":
            run_status, stopped = "failed", True
        elif status == "partial" and run_status == "success":
            run_status = "partial"
    store.finish(run_id, run_status)
    log.info("Pipeline %s finished: %s", run_id, run_status)
    return {"run_id": run_id, "status": run_status, "window": window, "stages": context.results}


def main(argv: list[str] | None = None) -> int:
    from eurogrid.logging_setup import configure_logging

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    configure_logging()
    parser = argparse.ArgumentParser(description="EuroGrid data pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="ingest, normalize, dbt build and detect")
    run.add_argument("--start", type=date.fromisoformat)
    run.add_argument("--end", type=date.fromisoformat)
    run.add_argument("--no-detect", action="store_true")
    run.add_argument("--wait-seconds", type=float, default=31.0)
    run.add_argument("--database", type=Path, default=Path("data/eurogrid.duckdb"))
    run.add_argument("--data-dir", type=Path, default=Path("data"))
    status = commands.add_parser("status", help="show recent pipeline runs")
    status.add_argument("--data-dir", type=Path, default=Path("data"))
    status.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    if args.command == "status":
        for item in PipelineStore(args.data_dir / "metadata" / "ingestion_runs.sqlite3").runs(args.limit):
            print(f"{item['run_id']}  {item['started_at'][:19]}  {item['status']:<8} window {item['window']}")
            for stage in item["stages"]:
                print(f"    {stage['stage']:<10} {stage['status']:<8} {stage['duration_s'] or 0:7.1f} s  "
                      f"{json.dumps(stage['details'], default=str)[:110]}")
        return 0
    if (args.start is None) != (args.end is None):
        parser.error("give both --start and --end, or neither for an incremental run")
    result = run_pipeline(PipelineConfig(database=args.database, data_dir=args.data_dir, start=args.start,
                                         end=args.end, wait_seconds=args.wait_seconds, detect=not args.no_detect))
    print(json.dumps({"run_id": result["run_id"], "status": result["status"],
                      "stages": {name: info["status"] for name, info in result["stages"].items()}}, indent=2))
    return 0 if result["status"] in {"success", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

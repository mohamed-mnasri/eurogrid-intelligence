"""Phase 11 chaos mode: test-only failure injection (handbook 13.2).

A chaos run never touches the production database. It:

1. copies a small slice of the real source tables (``dim_geography``, ``fct_market_price``,
   ``fct_generation``, ``fct_weather``) around one saved anomaly into ``data/chaos/<run_id>/clean.duckdb``
   and ``faulty.duckdb`` (production is attached read-only),
2. injects one fault into the faulty copy only:

   * ``missing_feed``   – the event country's generation rows disappear for the event's local day(s);
   * ``malformed_feed`` – the event zone's price rows for those days are duplicated and half of them get an
     invalid status (``corrupt``),

3. runs the real dbt project (``dbt build``: models + tests) against each copy with a throwaway profile,
4. re-runs the data-quality gate and the investigation of the same event on each copy,
5. records what changed: dbt test failures, downstream models affected (from dbt lineage), the quality
   gate's domain statuses, and whether the report refused unsupported claims and named the missing evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import duckdb

from eurogrid.investigation import CaseStore, investigate
from eurogrid.investigation_tools import InvestigationTools
from eurogrid.operations_service import OperationsService

UTC = timezone.utc
CAUSAL = ("caused", "because", "due to", "led to", "proves", "proven", "proof")
NON_GENERATION_TYPES = ("load", "cross_border_electricity_trading", "renewable_share_of_load",
                        "renewable_share_of_generation", "residual_load", "hydro_pumped_storage_consumption",
                        "battery_consumption", "hydro_pumped_storage", "battery")
FAULTS: dict[str, dict[str, str]] = {
    "missing_feed": {"label": "Missing generation feed", "source_table": "fct_generation", "domain": "generation",
                     "description": "All generation rows for the event country disappear for the event's local day(s), "
                                    "as if the public-power feed had returned nothing."},
    "malformed_feed": {"label": "Malformed price feed", "source_table": "fct_market_price", "domain": "price",
                       "description": "The event zone's price rows for the event's local day(s) arrive twice, and half "
                                      "of them carry an invalid status value ('corrupt')."},
}
# Which application outputs read each Gold model (for "affected downstream outputs").
OUTPUTS_BY_MODEL = {
    "gold_market_price": ["Market Overview prices", "Market Analytics", "anomaly detection", "price-baseline tool",
                          "household dynamic tariff and event replay"],
    "gold_price_hourly_baseline": ["usual-price line on Market Overview"],
    "gold_market_coverage": ["country selector and coverage dates"],
    "gold_generation_mix": ["generation charts", "generation-mix investigation tool"],
    "gold_demand": ["grid-use charts", "demand investigation tool"],
    "gold_cross_border_flow": ["cross-border investigation tool"],
    "gold_weather": ["weather table", "weather-context tool"],
    "energy_market_hourly": ["aligned hourly mart"],
}
DbtRunner = Callable[[Path, Path], dict[str, Any]]


@lru_cache(maxsize=8)
def _has_duckdb_adapter(executable: str) -> bool:
    """A dbt on the machine may be for another warehouse; only use one that has the DuckDB adapter."""
    try:
        completed = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=60,
                                   env={**os.environ, "DO_NOT_TRACK": "1"})
    except (OSError, subprocess.SubprocessError):
        return False
    return "duckdb" in (completed.stdout + completed.stderr).lower()


def dbt_executable() -> Path | None:
    """The dbt to use: EUROGRID_DBT_EXECUTABLE, then the project's .venv-dbt, then dbt on PATH.

    Any candidate without the DuckDB adapter is ignored, so a global dbt for another warehouse is never used.
    """
    configured = os.environ.get("EUROGRID_DBT_EXECUTABLE")
    candidates = [Path(configured)] if configured else [Path(".venv-dbt/Scripts/dbt.exe"), Path(".venv-dbt/bin/dbt")]
    if not configured and (located := shutil.which("dbt")):
        candidates.append(Path(located))
    return next((path for path in candidates if path.exists() and _has_duckdb_adapter(str(path.resolve()))), None)


def run_dbt(database: Path, workdir: Path, project_dir: Path = Path("eurogrid_analytics")) -> dict[str, Any]:
    """`dbt build` against ``database`` with a throwaway profile; artifacts go to ``workdir``, never target/."""
    executable = dbt_executable()
    if executable is None:
        return {"ran": False, "reason": "dbt executable not found (set EUROGRID_DBT_EXECUTABLE or create .venv-dbt)"}
    workdir.mkdir(parents=True, exist_ok=True)
    profile = (f"eurogrid_analytics:\n  target: chaos\n  outputs:\n    chaos:\n      type: duckdb\n"
               f"      path: '{database.resolve().as_posix()}'\n      threads: 1\n")
    (workdir / "profiles.yml").write_text(profile, encoding="utf-8")
    target = workdir / "target"
    command = [str(executable), "build", "--project-dir", str(project_dir.resolve()), "--profiles-dir", str(workdir.resolve()),
               "--target", "chaos", "--target-path", str(target.resolve()), "--log-path", str((workdir / "logs").resolve())]
    env = {**os.environ, "DO_NOT_TRACK": "1", "DBT_SEND_ANONYMOUS_USAGE_STATS": "False"}
    completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=600)
    if not (target / "run_results.json").exists():
        return {"ran": False, "reason": f"dbt did not produce results (exit {completed.returncode}): "
                                        f"{(completed.stdout + completed.stderr)[-600:]}"}
    summary = OperationsService(database, workdir, workdir).dbt()   # reads workdir/target artifacts
    return {"ran": True, "exit_code": completed.returncode, **summary}


def event_days(event: dict[str, Any], timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    first = datetime.fromisoformat(event["start_utc"]).astimezone(zone).date()
    last = (datetime.fromisoformat(event["end_utc"]) - timedelta(seconds=1)).astimezone(zone).date()
    return (datetime.combine(first, time.min, tzinfo=zone).astimezone(UTC),
            datetime.combine(last + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC))


def zone_info(production: Path, zone: str) -> tuple[str, str]:
    """Country and source timezone of a bidding zone, from the source tables chaos mode copies."""
    with duckdb.connect(str(production), read_only=True) as db:
        row = db.execute("""SELECT lower(min(g.country_code)), min(p.source_timezone) FROM dim_geography g
            JOIN fct_market_price p ON p.geography_id = g.geography_id WHERE g.bidding_zone = ?""", [zone]).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"No source price data for bidding zone {zone}")
    return row[0], row[1] or "UTC"


def build_slice(production: Path, target: Path, start: datetime, end: datetime) -> dict[str, int]:
    """Copy the source tables for [start, end) from production (read-only) into a new database."""
    if target.exists():
        target.unlink()
    counts = {}
    with duckdb.connect(str(target)) as db:
        db.execute(f"ATTACH '{production.resolve().as_posix()}' AS prod (READ_ONLY)")
        db.execute("CREATE TABLE dim_geography AS SELECT * FROM prod.main.dim_geography")
        for table in ("fct_market_price", "fct_generation", "fct_weather"):
            db.execute(f"""CREATE TABLE {table} AS SELECT * FROM prod.main.{table}
                WHERE interval_start_utc >= CAST(? AS TIMESTAMPTZ) AND interval_start_utc < CAST(? AS TIMESTAMPTZ)""",
                       [start.isoformat(), end.isoformat()])
            counts[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        db.execute("DETACH prod")
    return counts


def inject(database: Path, fault: str, zone: str, country: str, start: datetime, end: datetime) -> int:
    """Apply one fault to the faulty copy; returns the number of rows changed."""
    window = "interval_start_utc >= CAST(? AS TIMESTAMPTZ) AND interval_start_utc < CAST(? AS TIMESTAMPTZ)"
    bounds = [start.isoformat(), end.isoformat()]
    with duckdb.connect(str(database)) as db:
        if fault == "missing_feed":
            excluded = ",".join(f"'{name}'" for name in NON_GENERATION_TYPES)
            geos = "SELECT geography_id FROM dim_geography WHERE lower(coalesce(country_code, code)) = ?"
            changed = db.execute(f"""SELECT count(*) FROM fct_generation WHERE geography_id IN ({geos})
                AND production_type NOT IN ({excluded}) AND {window}""", [country, *bounds]).fetchone()[0]
            db.execute(f"""DELETE FROM fct_generation WHERE geography_id IN ({geos})
                AND production_type NOT IN ({excluded}) AND {window}""", [country, *bounds])
            return changed
        if fault == "malformed_feed":
            geos = "SELECT geography_id FROM dim_geography WHERE bidding_zone = ?"
            changed = db.execute(f"SELECT count(*) FROM fct_market_price WHERE geography_id IN ({geos}) AND {window}",
                                 [zone, *bounds]).fetchone()[0]
            db.execute(f"INSERT INTO fct_market_price SELECT * FROM fct_market_price WHERE geography_id IN ({geos}) AND {window}",
                       [zone, *bounds])
            db.execute(f"""UPDATE fct_market_price SET data_status = 'corrupt' WHERE geography_id IN ({geos}) AND {window}
                AND CAST(floor(epoch(interval_start_utc) / 3600) AS BIGINT) % 2 = 0""", [zone, *bounds])
            return changed * 2
    raise ValueError(f"Unknown fault {fault!r}; choose one of {sorted(FAULTS)}")


def assess(event: dict[str, Any], database: Path, workdir: Path, domain: str,
           agent: Callable[[dict[str, Any], InvestigationTools, CaseStore, Path], dict[str, Any]] | None) -> dict[str, Any]:
    """Quality gate + investigation(s) of the same event on one copy."""
    tools = InvestigationTools(database)
    start, end = datetime.fromisoformat(event["start_utc"]), datetime.fromisoformat(event["end_utc"])
    quality = tools.get_data_quality_status(event["geography"], start, end)
    store = CaseStore(workdir / "cases.sqlite")
    store.save_events([_EventShim(event)])
    reports = {"rule-based": investigate(event, tools).model_dump()}
    if agent is not None:
        reports["agent"] = agent(event, tools, store, workdir / "reports")
    checks = {}
    for name, report in reports.items():
        statements = [item["statement"] for key in ("observations", "coinciding_relationships",
                                                     "plausible_contributing_factors") for item in report[key]]
        missing = " ".join(report["missing_evidence"]).lower()
        checks[name] = {"report_id": report["report_id"], "status": report["status"],
                        "missing_evidence": report["missing_evidence"],
                        "finding_counts": {"observations": len(report["observations"]),
                                           "coinciding": len(report["coinciding_relationships"]),
                                           "plausible": len(report["plausible_contributing_factors"])},
                        "names_missing_domain": domain in missing,
                        "causal_statements": [text for text in statements if any(word in text.lower() for word in CAUSAL)],
                        "proven_causes": len(report["proven_causes"])}
    return {"quality": {name: info["status"] for name, info in quality.summary["domains"].items()},
            "quality_detail": quality.summary["domains"], "reports": checks}


class _EventShim:
    """Lets a stored event dict be saved into a chaos-local case store."""

    def __init__(self, event: dict[str, Any]) -> None:
        self.__dict__.update(event)

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items()}


def run_chaos(fault: str, event: dict[str, Any], production: Path, chaos_root: Path,
              dbt_runner: DbtRunner | None = run_dbt,
              agent: Callable[[dict[str, Any], InvestigationTools, CaseStore, Path], dict[str, Any]] | None = None,
              ) -> dict[str, Any]:
    if fault not in FAULTS:
        raise ValueError(f"Unknown fault {fault!r}; choose one of {sorted(FAULTS)}")
    spec = FAULTS[fault]
    zone = event["geography"]
    country, timezone_name = zone_info(production, zone)
    day_start, day_end = event_days(event, timezone_name)
    slice_start, slice_end = day_start - timedelta(days=36), day_end + timedelta(days=1)
    created = datetime.now(UTC)
    run_id = "chs_" + hashlib.sha256(f"{fault}|{event['anomaly_id']}|{created.isoformat()}".encode()).hexdigest()[:16]
    workdir = chaos_root / run_id
    workdir.mkdir(parents=True, exist_ok=True)

    sides: dict[str, dict[str, Any]] = {}
    for side in ("clean", "faulty"):
        database = workdir / f"{side}.duckdb"
        rows = build_slice(production, database, slice_start, slice_end)
        changed = inject(database, fault, zone, country, day_start, day_end) if side == "faulty" else 0
        dbt = dbt_runner(database, workdir / f"dbt_{side}") if dbt_runner else {"ran": False, "reason": "dbt disabled"}
        if not dbt.get("ran"):
            # Without dbt the Gold views do not exist; the run cannot assess the copy honestly.
            sides[side] = {"rows": rows, "rows_changed": changed, "dbt": dbt, "assessment": None}
            continue
        sides[side] = {"rows": rows, "rows_changed": changed, "dbt": dbt,
                       "assessment": assess(event, database, workdir / side, spec["domain"], agent)}

    def failing(side: str) -> list[dict[str, Any]]:
        return [item for item in sides[side]["dbt"].get("results", []) if item["status"] in {"fail", "error"}]

    lineage = {item["name"]: item for item in sides["faulty"]["dbt"].get("lineage", [])}
    downstream = lineage.get(spec["source_table"], {}).get("downstream", [])
    failed_models = sorted({item["model"] for item in failing("faulty") if item.get("model")})
    outputs = sorted({output for model in downstream if model in OUTPUTS_BY_MODEL for output in OUTPUTS_BY_MODEL[model]})
    verdicts = {}
    faulty = sides["faulty"].get("assessment")
    if faulty:
        for name, report in faulty["reports"].items():
            verdicts[name] = {
                "not_complete": report["status"] != "complete",
                "names_missing_evidence": report["names_missing_domain"],
                "no_causal_claims": not report["causal_statements"],
                "no_proven_causes": report["proven_causes"] == 0,
            }
    result = {
        "run_id": run_id, "created_at": created.isoformat(), "fault": fault, **spec,
        "event": {key: event[key] for key in ("anomaly_id", "geography", "direction", "start_utc", "end_utc",
                                              "observed_value", "baseline_median", "max_abs_score")},
        "fault_window": {"start_utc": day_start.isoformat(), "end_utc": day_end.isoformat()},
        "slice": {"start_utc": slice_start.isoformat(), "end_utc": slice_end.isoformat(),
                  "note": "Copied from production read-only; production was not modified."},
        "clean": sides["clean"], "faulty": sides["faulty"],
        "detected_by_dbt": bool(failing("faulty")) and not failing("clean"),
        "failing_tests": [item["name"] for item in failing("faulty")],
        "models_with_failing_tests": failed_models,
        "downstream_models": downstream, "affected_outputs": outputs,
        "report_checks": verdicts,
        "workdir": str(workdir),
    }
    (workdir / "chaos_report.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


class ChaosStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS chaos_run (run_id TEXT PRIMARY KEY, fault TEXT NOT NULL,
                anomaly_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL)""")

    def save(self, result: dict[str, Any]) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO chaos_run VALUES (?,?,?,?,?)",
                       (result["run_id"], result["fault"], result["event"]["anomaly_id"], result["created_at"],
                        json.dumps(result, default=str)))

    def get(self, run_id: str) -> dict[str, Any] | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT payload FROM chaos_run WHERE run_id=?", [run_id]).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT payload FROM chaos_run ORDER BY created_at DESC LIMIT ?", [limit]).fetchall()
        items = []
        for (payload,) in rows:
            data = json.loads(payload)
            items.append({"run_id": data["run_id"], "fault": data["fault"], "label": data["label"],
                          "anomaly_id": data["event"]["anomaly_id"], "created_at": data["created_at"],
                          "detected_by_dbt": data["detected_by_dbt"], "report_checks": data["report_checks"]})
        return items


def main(argv: list[str] | None = None) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run a test-only chaos experiment on a copy of the data")
    parser.add_argument("--fault", choices=sorted(FAULTS), required=True)
    parser.add_argument("--anomaly-id", required=True)
    parser.add_argument("--database", type=Path, default=Path("data/eurogrid.duckdb"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    event = CaseStore(args.data_dir / "metadata" / "investigations.sqlite").get_event(args.anomaly_id)
    if event is None:
        parser.error(f"unknown anomaly {args.anomaly_id}")
    result = run_chaos(args.fault, event, args.database, args.data_dir / "chaos")
    ChaosStore(args.data_dir / "metadata" / "chaos.sqlite").save(result)
    print(json.dumps({key: result[key] for key in ("run_id", "label", "detected_by_dbt", "failing_tests",
                                                   "downstream_models", "affected_outputs", "report_checks")}, indent=2))
    for side in ("clean", "faulty"):
        assessment = result[side]["assessment"] or {}
        print(side, "quality:", assessment.get("quality"), "| rows changed:", result[side]["rows_changed"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

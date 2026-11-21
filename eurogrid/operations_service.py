"""Data Operations service (handbook 2.5): connector health, dbt tests, freshness and lineage.

Everything here is read-only. It reports what the pipeline actually recorded:

* the ingestion ledger (``data/metadata/ingestion_runs.sqlite3``) written by Phase 2,
* dbt's own artifacts (``target/run_results.json`` and ``target/manifest.json``),
* row counts, latest timestamps, missing hours and validity per country and domain in the Gold models.

Statuses follow handbook 13.1: complete, partial, stale, missing. They are coverage
indicators, not probabilities that an analysis is correct.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from eurogrid.service_errors import UnavailableError
from eurogrid.source_register import SOURCES

UTC = timezone.utc
STALE_AFTER_DAYS = 7          # a domain whose latest interval is older than this is "stale"
COMPLETE_VALID_RATIO = 0.95   # below this share of valid rows (or present hours) a domain is "partial"

# domain -> (Gold model, geography expression). Rows without a country (e.g. a weather point that is not
# assigned to one) are reported under their coordinates instead of being dropped.
GOLD_DOMAINS: dict[str, tuple[str, str]] = {
    "price": ("gold_market_price", "coalesce(country_code, bidding_zone)"),
    "generation": ("gold_generation_mix", "coalesce(country_code, geography_code)"),
    "load": ("gold_demand", "coalesce(country_code, geography_code)"),
    "cross_border_flows": ("gold_cross_border_flow", "coalesce(country_code, geography_code)"),
    "weather": ("gold_weather", "coalesce(country_code, 'point ' || CAST(round(latitude, 3) AS VARCHAR) || ', ' || CAST(round(longitude, 3) AS VARCHAR))"),
}


def domain_status(rows: int, valid_rows: int, present_hours: int, expected_hours: int,
                  last_interval: datetime | None, reference: datetime) -> str:
    """Classify one domain for one geography."""
    if rows == 0 or valid_rows == 0 or last_interval is None:
        return "missing"
    if (reference - last_interval).days > STALE_AFTER_DAYS:
        return "stale"
    if valid_rows / rows < COMPLETE_VALID_RATIO or (expected_hours and present_hours / expected_hours < COMPLETE_VALID_RATIO):
        return "partial"
    return "complete"


class OperationsService:
    def __init__(self, database_path: Path, data_dir: Path, dbt_dir: Path) -> None:
        self.database_path = database_path
        self.ledger_path = data_dir / "metadata" / "ingestion_runs.sqlite3"
        self.dbt_dir = dbt_dir

    # ------------------------------------------------------------- connectors
    def ingestion(self, recent: int = 25) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return {"connectors": [], "recent_runs": [], "note": "No ingestion runs recorded yet."}
        with closing(sqlite3.connect(self.ledger_path)) as db:
            connectors = db.execute("""SELECT provider, dataset, count(*) AS runs,
                    sum(status='success') AS successes, sum(status!='success') AS failures,
                    sum(cache_hit) AS cache_hits, sum(rows_received) AS rows_received,
                    max(started_at) AS last_started,
                    max(CASE WHEN status='success' THEN finished_at END) AS last_success
                FROM ingestion_run GROUP BY provider, dataset ORDER BY provider, dataset""").fetchall()
            last = {(p, d): (status, error) for p, d, status, error in db.execute("""
                SELECT provider, dataset, status, error_details FROM ingestion_run r
                WHERE run_id = (SELECT max(run_id) FROM ingestion_run x
                                WHERE x.provider=r.provider AND x.dataset=r.dataset)""").fetchall()}
            runs = db.execute("""SELECT run_id, provider, dataset, endpoint, request_parameters_json, started_at,
                    finished_at, status, rows_received, error_details, cache_hit
                FROM ingestion_run ORDER BY run_id DESC LIMIT ?""", [recent]).fetchall()
        items = []
        for provider, dataset, total, ok, failed, cache_hits, rows, last_started, last_success in connectors:
            last_status, last_error = last.get((provider, dataset), (None, None))
            items.append({"provider": provider, "dataset": dataset, "runs": total, "successes": ok or 0,
                          "failures": failed or 0, "cache_hits": cache_hits or 0, "rows_received": rows or 0,
                          "last_started": last_started, "last_success": last_success,
                          "last_status": last_status, "last_error": last_error,
                          "health": "healthy" if last_status == "success" else "failing"})
        recent_runs = []
        for run_id, provider, dataset, endpoint, params, started, finished, status, rows, error, cache in runs:
            parameters = json.loads(params or "{}")
            geography = parameters.get("country") or parameters.get("bzn") or parameters.get("geo") or (
                f"{parameters['latitude']}, {parameters['longitude']}" if "latitude" in parameters else "")
            recent_runs.append({"run_id": run_id, "provider": provider, "dataset": dataset, "endpoint": endpoint,
                                "geography": geography, "start": parameters.get("start") or parameters.get("start_date"),
                                "end": parameters.get("end") or parameters.get("end_date"), "started_at": started,
                                "finished_at": finished, "status": status, "rows_received": rows,
                                "error_details": error, "cache_hit": bool(cache)})
        return {"connectors": items, "recent_runs": recent_runs}

    # -------------------------------------------------------------------- dbt
    def dbt(self) -> dict[str, Any]:
        target = self.dbt_dir / "target"
        results_path, manifest_path = target / "run_results.json", target / "manifest.json"
        if not results_path.exists() or not manifest_path.exists():
            return {"available": False, "note": f"No dbt artifacts in {target}; run `dbt build` first."}
        results = json.loads(results_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        nodes = manifest.get("nodes", {})
        tests = []
        for item in results.get("results", []):
            node = nodes.get(item["unique_id"], {})
            attached = node.get("attached_node") or next(iter(node.get("depends_on", {}).get("nodes", [])), "")
            tests.append({"name": node.get("name", item["unique_id"].split(".")[-2]),
                          "resource_type": item["unique_id"].split(".")[0], "status": item["status"],
                          "failures": item.get("failures"), "message": item.get("message"),
                          "model": attached.split(".")[-1] if attached else None,
                          "execution_time_s": round(item.get("execution_time") or 0, 3)})
        counts: dict[str, int] = {}
        for test in tests:
            counts[test["status"]] = counts.get(test["status"], 0) + 1
        return {"available": True, "generated_at": results.get("metadata", {}).get("generated_at"),
                "command": results.get("args", {}).get("which"), "counts": counts, "results": tests,
                "lineage": self._lineage(manifest)}

    @staticmethod
    def _lineage(manifest: dict[str, Any]) -> list[dict[str, Any]]:
        """Each source/model with its direct parents and every downstream model affected by a failure."""
        nodes = {**manifest.get("sources", {}), **{k: v for k, v in manifest.get("nodes", {}).items()
                                                    if k.startswith("model.")}}
        children = {key: [child for child in manifest.get("child_map", {}).get(key, []) if child.startswith("model.")]
                    for key in nodes}

        def downstream(key: str, seen: set[str]) -> set[str]:
            for child in children.get(key, []):
                if child not in seen:
                    seen.add(child)
                    downstream(child, seen)
            return seen

        items = []
        for key, node in sorted(nodes.items(), key=lambda pair: (pair[0].split(".")[0] != "source", pair[0])):
            parents = [parent.split(".")[-1] for parent in node.get("depends_on", {}).get("nodes", [])]
            items.append({"name": node.get("name") or key.split(".")[-1], "kind": key.split(".")[0],
                          "materialized": node.get("config", {}).get("materialized") if key.startswith("model.") else "table",
                          "parents": parents,
                          "downstream": sorted(child.split(".")[-1] for child in downstream(key, set()))})
        return items

    # --------------------------------------------------------- freshness/gaps
    def freshness(self, reference: datetime | None = None) -> dict[str, Any]:
        reference = reference or datetime.now(UTC)
        if not self.database_path.exists():
            raise UnavailableError(f"Database file not found: {self.database_path}")
        rows: list[dict[str, Any]] = []
        try:
            with duckdb.connect(str(self.database_path), read_only=True) as db:
                for domain, (model, geo_column) in GOLD_DOMAINS.items():
                    query = f"""SELECT lower(coalesce(CAST({geo_column} AS VARCHAR), 'unassigned')) AS geography,
                            count(*) AS rows,
                            count(*) FILTER (WHERE data_status='valid') AS valid_rows,
                            CAST(min(interval_start_utc) AS VARCHAR) AS first_interval,
                            CAST(max(interval_end_utc) AS VARCHAR) AS last_interval,
                            count(DISTINCT epoch(date_trunc('hour', interval_start_utc))) AS present_hours,
                            CAST((epoch(date_trunc('hour', max(interval_start_utc)))
                                  - epoch(date_trunc('hour', min(interval_start_utc)))) / 3600 + 1 AS BIGINT)
                              AS expected_hours,
                            min(provider) AS provider
                        FROM {model} GROUP BY 1 ORDER BY 1"""
                    for geography, count, valid, first, last, present, expected, provider in db.execute(query).fetchall():
                        last_dt = datetime.fromisoformat(last) if last else None
                        rows.append({"geography": geography, "domain": domain, "model": model, "provider": provider,
                                     "rows": count, "valid_rows": valid, "first_interval_utc": first,
                                     "last_interval_utc": last, "present_hours": present,
                                     "expected_hours": expected, "missing_hours": max(0, (expected or 0) - present),
                                     "valid_pct": round(valid / count * 100, 2) if count else 0.0,
                                     "age_days": (reference - last_dt).days if last_dt else None,
                                     "status": domain_status(count, valid, present, expected or 0, last_dt, reference)})
        except duckdb.Error as exc:
            raise UnavailableError(f"Gold models are missing or failed to query; run dbt first. Details: {exc}") from exc
        return {"reference_utc": reference.isoformat(), "stale_after_days": STALE_AFTER_DAYS,
                "complete_ratio": COMPLETE_VALID_RATIO, "items": rows,
                "rules": "missing = no valid rows; stale = latest interval older than "
                         f"{STALE_AFTER_DAYS} days; partial = under {COMPLETE_VALID_RATIO:.0%} valid rows or present "
                         "hours between the first and last interval; otherwise complete."}

    @staticmethod
    def sources() -> list[dict[str, str]]:
        return SOURCES

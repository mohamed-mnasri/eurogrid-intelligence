"""Content-addressed raw storage, request caching, and an SQLite run ledger."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_key(sample: dict[str, Any]) -> str:
    identity = {key: sample.get(key) for key in ("provider", "dataset", "endpoint", "request_parameters")}
    return hashlib.sha256(canonical_json(identity).encode()).hexdigest()


def count_rows(sample: dict[str, Any]) -> int:
    payload = sample.get("payload", {})
    if isinstance(payload.get("data"), list):
        return len(payload["data"])
    if isinstance(payload.get("hourly"), dict):
        return len(payload["hourly"].get("time", []))
    if isinstance(payload.get("outputs"), dict):
        hourly = payload["outputs"].get("hourly")
        if isinstance(hourly, list):
            return len(hourly)
        monthly = payload["outputs"].get("monthly", {})
        if isinstance(monthly, dict):
            return sum(len(rows) for rows in monthly.values() if isinstance(rows, list))
    if isinstance(payload.get("value"), dict):
        return len(payload["value"])
    return 0


def _slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "unknown")).strip("_").lower()
    return text or "unknown"


def geography_label(sample: dict[str, Any]) -> str:
    geography = sample.get("metadata", {}).get("geography")
    if isinstance(geography, dict):
        return geography.get("bidding_zone") or geography.get("country") or geography.get("selection") or "all"
    if isinstance(geography, str):
        return geography
    params = sample.get("request_parameters", {})
    return params.get("country") or params.get("geo") or "all"


def period_label(sample: dict[str, Any]) -> tuple[str, str]:
    period = sample.get("metadata", {}).get("period") or {}
    params = sample.get("request_parameters", {})
    start = period.get("start") if isinstance(period, dict) else None
    if not start:
        start = params.get("start") or params.get("start_date") or f"{period.get('start_year', 'unknown')}-01-01"
    try:
        parsed = date.fromisoformat(str(start)[:10])
        return f"{parsed.year:04d}", f"{parsed.month:02d}"
    except ValueError:
        return "unknown", "unknown"


@dataclass
class IngestionResult:
    path: Path
    raw_sha256: str
    request_sha256: str
    rows: int
    cache_hit: bool


class IngestionStore:
    def __init__(self, root: Path = Path("data"), cache_ttl_hours: int = 24) -> None:
        self.root = root
        self.cache_ttl_hours = cache_ttl_hours
        self.db_path = root / "metadata" / "ingestion_runs.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS ingestion_run (
              run_id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, dataset TEXT NOT NULL,
              endpoint TEXT NOT NULL, request_parameters_json TEXT NOT NULL, request_sha256 TEXT NOT NULL,
              started_at TEXT NOT NULL, finished_at TEXT NOT NULL, status TEXT NOT NULL,
              rows_received INTEGER NOT NULL DEFAULT 0, error_details TEXT, cache_hit INTEGER NOT NULL DEFAULT 0,
              raw_sha256 TEXT, raw_path TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_request ON ingestion_run(request_sha256)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    @contextmanager
    def connection(self):
        db = self._connect()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _raw_path(self, sample: dict[str, Any], request_sha: str, raw_sha: str) -> Path:
        year, month = period_label(sample)
        return (self.root / "raw" / f"provider={_slug(sample.get('provider'))}" /
                f"dataset={_slug(sample.get('dataset'))}" / f"geography={_slug(geography_label(sample))}" /
                f"year={year}" / f"month={month}" / f"request={request_sha[:16]}" / f"response_{raw_sha}.json")

    def ingest(self, fetch: Callable[[], Any], *, identity: dict[str, Any] | None = None, force_refresh: bool = False) -> IngestionResult:
        started = datetime.now(timezone.utc).isoformat()
        sample: dict[str, Any] | None = None
        cache_hit = False
        raw_sha = None
        raw_path = None
        error = None
        status = "failed"
        rows = 0
        try:
            request_sha = hashlib.sha256(canonical_json(identity).encode()).hexdigest() if identity is not None else None
            if request_sha:
                cache_path = self.root / "cache" / f"{request_sha}.json"
                if not force_refresh and cache_path.exists():
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                    cached_at = datetime.fromisoformat(cached["cached_at"])
                    age = datetime.now(timezone.utc) - cached_at.astimezone(timezone.utc)
                    if age.total_seconds() <= self.cache_ttl_hours * 3600:
                        sample = cached["sample"]
                        cache_hit = True
            if sample is None:
                sample = fetch()
                if hasattr(sample, "to_dict"):
                    sample = sample.to_dict()
            request_sha = request_sha or request_key(sample)
            cache_path = self.root / "cache" / f"{request_sha}.json"
            if not cache_hit and not force_refresh and cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_at = datetime.fromisoformat(cached["cached_at"])
                age = datetime.now(timezone.utc) - cached_at.astimezone(timezone.utc)
                if age.total_seconds() <= self.cache_ttl_hours * 3600:
                    sample = cached["sample"]
                    cache_hit = True
            rows = count_rows(sample)
            raw_bytes = (canonical_json(sample) + "\n").encode("utf-8")
            raw_sha = hashlib.sha256(raw_bytes).hexdigest()
            raw_path = self._raw_path(sample, request_sha, raw_sha)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            if not raw_path.exists():
                raw_path.write_bytes(raw_bytes)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(canonical_json({"cached_at": datetime.now(timezone.utc).isoformat(), "sample": sample}), encoding="utf-8")
            status = "success"
            return IngestionResult(raw_path, raw_sha, request_sha, rows, cache_hit)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            finished = datetime.now(timezone.utc).isoformat()
            record = sample or identity or {}
            with self.connection() as db:
                db.execute("""INSERT INTO ingestion_run
                  (provider,dataset,endpoint,request_parameters_json,request_sha256,started_at,finished_at,status,
                   rows_received,error_details,cache_hit,raw_sha256,raw_path)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    record.get("provider", "unknown"), record.get("dataset", "unknown"), record.get("endpoint", ""),
                    canonical_json(record.get("request_parameters", {})), request_sha or request_key(record), started, finished,
                    status, rows, error, int(cache_hit), raw_sha, str(raw_path) if raw_path else None))

    def stats(self) -> dict[str, int]:
        with self.connection() as db:
            runs, successes, raw_versions = db.execute("SELECT COUNT(*), SUM(status='success'), COUNT(DISTINCT raw_sha256) FROM ingestion_run").fetchone()
        return {"runs": runs, "successful_runs": successes or 0, "raw_versions": raw_versions}

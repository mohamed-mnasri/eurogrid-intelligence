"""Build the Phase 3 Silver layer from immutable Bronze JSON samples.

The normalizer maps geography, timestamps and units into analytical DuckDB
tables while keeping source timestamps, units, hashes, and quality statuses.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

# These series are ratios or consumption measures, not generation or trade/load.
# Demand and net cross-border trading are retained with the other public-power
# series so Phase 4 can model them separately by their stable source series ID.
NON_GENERATION = {"residual_load", "renewable_share_of_load", "renewable_share_of_generation",
                  "hydro_pumped_storage_consumption", "battery_consumption", "hydro_pumped_storage", "battery"}
WEATHER_VARIABLES = {"temperature_2m": ("temperature", "°C"), "wind_speed_10m": ("wind_speed", "m/s"),
                     "shortwave_radiation": ("shortwave_radiation", "W/m²")}


def parse_timestamp(value: str, source_timezone: str | None = None, previous_utc: datetime | None = None) -> tuple[datetime | None, str | None, str | None]:
    """Return UTC time, source zone/offset, and parse error for one observation.

    For naive timestamps, use the IANA source zone and the previous UTC value to
    disambiguate repeated fall-back hours. Nonexistent spring-forward hours are
    returned as errors rather than guessed.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None, source_timezone, "invalid_timestamp"
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc), source_timezone or str(parsed.tzinfo), None
    if not source_timezone:
        return None, None, "timezone_missing"
    try:
        zone = ZoneInfo(source_timezone)
    except Exception:
        return None, source_timezone, "invalid_timezone"
    candidates = []
    for fold in (0, 1):
        local = parsed.replace(tzinfo=zone, fold=fold)
        utc = local.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == parsed and utc not in [c[0] for c in candidates]:
            candidates.append((utc, local.strftime("%z")))
    if not candidates:
        return None, source_timezone, "nonexistent_local_time"
    candidates.sort()
    eligible = [candidate for candidate in candidates if previous_utc is None or candidate[0] > previous_utc]
    if not eligible:
        return None, source_timezone, "non_monotonic_local_time"
    chosen = eligible[0]
    return chosen[0], f"{source_timezone}{chosen[1]}", None


def classify(value: Any, stale: bool = False) -> tuple[float | None, str]:
    """Keep missing, valid zero/nonzero, invalid, and explicitly stale distinct."""
    if stale:
        return None, "stale"
    if value is None:
        return None, "missing"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, "invalid"
    if not math.isfinite(number):
        return None, "invalid"
    return number, "valid"


def convert(value: float | None, unit: str | None, target: str) -> float | None:
    """Convert the source units used by this project to its normalized units."""
    if value is None:
        return None
    unit_norm = (unit or "").strip().lower().replace(" ", "")
    if target == "MW" and unit_norm == "gw":
        return value * 1000
    if target == "EUR/MWh" and unit_norm in {"eur/mwh", "eur/mwh."}:
        return value
    if target == "m/s" and unit_norm in {"km/h", "kmh⁻¹", "km/h."}:
        return value / 3.6
    if target in {"°c", "w/m²"}:
        return value
    return value


def _key(*parts: Any) -> str:
    """Create a stable natural-key hash from a fact's identity fields."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class Normalizer:
    """Create Silver tables/views and load supported provider samples."""

    def __init__(self, db_path: Path = Path("data/eurogrid.duckdb")) -> None:
        """Open DuckDB and initialize the geography and fact structures."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = duckdb.connect(str(db_path))
        self.db.execute("""CREATE TABLE IF NOT EXISTS dim_geography(
          geography_id VARCHAR PRIMARY KEY, geography_type VARCHAR NOT NULL, code VARCHAR, name VARCHAR,
          country_code VARCHAR, bidding_zone VARCHAR, latitude DOUBLE, longitude DOUBLE, source VARCHAR)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS fct_market_price(
          fact_key VARCHAR PRIMARY KEY, geography_id VARCHAR, interval_start_utc TIMESTAMPTZ, interval_end_utc TIMESTAMPTZ,
          interval_minutes INTEGER, original_timestamp VARCHAR, source_timezone VARCHAR, original_value DOUBLE,
          original_unit VARCHAR, value DOUBLE, unit VARCHAR, data_status VARCHAR, provider VARCHAR, dataset VARCHAR,
          raw_sha256 VARCHAR, series_id VARCHAR)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS fct_generation(
          fact_key VARCHAR PRIMARY KEY, geography_id VARCHAR, interval_start_utc TIMESTAMPTZ, interval_end_utc TIMESTAMPTZ,
          interval_minutes INTEGER, original_timestamp VARCHAR, source_timezone VARCHAR, production_type VARCHAR,
          original_value DOUBLE, original_unit VARCHAR, value DOUBLE, unit VARCHAR, data_status VARCHAR,
          provider VARCHAR, dataset VARCHAR, raw_sha256 VARCHAR)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS fct_weather(
          fact_key VARCHAR PRIMARY KEY, geography_id VARCHAR, interval_start_utc TIMESTAMPTZ, interval_end_utc TIMESTAMPTZ,
          interval_minutes INTEGER, original_timestamp VARCHAR, source_timezone VARCHAR, variable VARCHAR,
          original_value DOUBLE, original_unit VARCHAR, value DOUBLE, unit VARCHAR, data_status VARCHAR,
          provider VARCHAR, dataset VARCHAR, raw_sha256 VARCHAR)""")
        self.db.execute("CREATE OR REPLACE VIEW stg_market_price AS SELECT * FROM fct_market_price")
        self.db.execute("CREATE OR REPLACE VIEW stg_generation AS SELECT * FROM fct_generation")
        self.db.execute("CREATE OR REPLACE VIEW stg_weather AS SELECT * FROM fct_weather")
        self.db.execute("""CREATE OR REPLACE VIEW vw_market_weather_aligned AS
          SELECT p.*, w.variable AS weather_variable, w.value AS weather_value, w.unit AS weather_unit,
                 w.geography_id AS weather_geography_id
          FROM fct_market_price p LEFT JOIN fct_weather w USING(interval_start_utc)""")

    def close(self) -> None:
        """Close the DuckDB connection after loading or inspection."""
        self.db.close()

    def _geography(self, geography_type: str, code: str | None = None, *, country: str | None = None,
                   zone: str | None = None, lat: float | None = None, lon: float | None = None,
                   source: str = "") -> str:
        """Upsert one distinct geography entity and return its stable key."""
        gid = _key(geography_type, code, country, zone, lat, lon)
        self.db.execute("""INSERT OR REPLACE INTO dim_geography VALUES (?,?,?,?,?,?,?,?,?)""",
          [gid, geography_type, code, code, country, zone, lat, lon, source])
        return gid

    def _files(self, raw_dir: Path) -> list[Path]:
        """Find JSON samples recursively while skipping their small manifests."""
        return sorted(p for p in raw_dir.rglob("*.json") if p.name != "manifest.json")

    def _bulk_insert(self, table: str, columns: tuple[str, ...], records: list[list[Any]]) -> None:
        """Bulk-load normalized rows through CSV to avoid slow row-by-row inserts."""
        if not records:
            return
        # DuckDB's CSV reader is a fast built-in bulk loader; no extra dataframe
        # dependency is needed. The target table performs final type conversion.
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", suffix=".csv", delete=False) as temp:
            writer = csv.writer(temp)
            writer.writerow(columns)
            writer.writerows(records)
            temp_path = Path(temp.name)
        try:
            self.db.execute(
                f"INSERT OR REPLACE INTO {table} SELECT * FROM read_csv(?, header=true, all_varchar=true, nullstr='')",
                [str(temp_path)],
            )
        finally:
            temp_path.unlink(missing_ok=True)

    def load_directory(self, raw_dir: Path) -> dict[str, int]:
        """Transactionally load supported price, generation, and weather samples."""
        counts = {"market_price": 0, "generation": 0, "weather": 0, "files": 0}
        self.db.execute("BEGIN TRANSACTION")
        try:
            for path in self._files(raw_dir):
                try:
                    sample = json.loads(path.read_text(encoding="utf-8"))
                    if not {"provider", "dataset", "payload"}.issubset(sample):
                        continue
                except (OSError, json.JSONDecodeError):
                    continue
                raw_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                if sample["provider"] == "energy-charts" and sample["dataset"] == "day_ahead_price":
                    counts["market_price"] += self.load_prices(sample, raw_sha)
                elif sample["provider"] == "energy-charts" and sample["dataset"] == "public_power":
                    counts["generation"] += self.load_generation(sample, raw_sha)
                elif sample["provider"] == "open-meteo" and sample["dataset"] == "historical_weather":
                    counts["weather"] += self.load_weather(sample, raw_sha)
                counts["files"] += 1
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return counts

    def _source(self, sample: dict[str, Any]) -> tuple[str, str, int]:
        """Read source unit, timezone, and interval length from sample metadata."""
        meta, payload = sample.get("metadata", {}), sample.get("payload", {})
        unit = meta.get("unit") or payload.get("unit")
        mins = meta.get("interval_minutes") or payload.get("interval_minutes") or 60
        return str(unit or ""), str(meta.get("timezone") or payload.get("timezone") or "UTC"), int(mins)

    def load_prices(self, sample: dict[str, Any], raw_sha: str = "") -> int:
        """Upsert price intervals with their bidding-zone identity and provenance."""
        payload = sample["payload"]
        geography = payload.get("bidding_zone") or sample.get("metadata", {}).get("geography", {}).get("bidding_zone") or "unknown"
        country = payload.get("country")
        if country:
            self._geography("country", country, country=country, source=sample["provider"])
        gid = self._geography("bidding_zone", geography, country=country, zone=geography, source=sample["provider"])
        unit, zone, mins = self._source(sample)
        previous = None
        count = 0
        records = []
        for row in payload.get("data", []):
            timestamp = row.get("timestamp")
            utc, tz_label, time_error = parse_timestamp(timestamp, zone, previous)
            if utc is not None:
                previous = utc
            item = row.get("values", {}).get("day_ahead_price")
            value, status = classify(item)
            if time_error:
                status = "invalid"
            if utc is None:
                continue
            key = _key(sample["provider"], geography, "day_ahead_price", utc, mins)
            records.append([key, gid, utc, utc + timedelta(minutes=mins), mins, timestamp, tz_label, value, unit, convert(value, unit, "EUR/MWh"), "EUR/MWh", status, sample["provider"], sample["dataset"], raw_sha, "day_ahead_price"])
            count += 1
        self._bulk_insert("fct_market_price", ("fact_key", "geography_id", "interval_start_utc", "interval_end_utc",
            "interval_minutes", "original_timestamp", "source_timezone", "original_value", "original_unit", "value",
            "unit", "data_status", "provider", "dataset", "raw_sha256", "series_id"), records)
        return count

    def load_generation(self, sample: dict[str, Any], raw_sha: str = "") -> int:
        """Upsert generation series, excluding non-generation measures."""
        payload = sample["payload"]
        geography = payload.get("country") or "all"
        if geography == "all":
            # Keep the aggregate's old key stable while making it selectable as
            # an explicit geography in Gold/API coverage.
            gid = self._geography("europe_aggregate", geography, source=sample["provider"])
            self.db.execute("UPDATE dim_geography SET country_code='all' WHERE geography_id=?", [gid])
        else:
            gid = self._geography("country", geography, country=geography, source=sample["provider"])
        unit, zone, mins = self._source(sample)
        series = [s.get("id") for s in sample.get("metadata", {}).get("series", []) if s.get("id") not in NON_GENERATION]
        previous = None
        count = 0
        records = []
        for row in payload.get("data", []):
            timestamp = row.get("timestamp")
            utc, tz_label, _ = parse_timestamp(timestamp, zone, previous)
            if utc is not None:
                previous = utc
            if utc is None:
                continue
            for production_type in series:
                if production_type not in row.get("values", {}):
                    continue
                original = row["values"][production_type]
                value, status = classify(original)
                key = _key(sample["provider"], geography, production_type, utc, mins)
                records.append([key, gid, utc, utc + timedelta(minutes=mins), mins, timestamp, tz_label, production_type, value, unit, convert(value, unit, "MW"), "MW", status, sample["provider"], sample["dataset"], raw_sha])
                count += 1
        self._bulk_insert("fct_generation", ("fact_key", "geography_id", "interval_start_utc", "interval_end_utc",
            "interval_minutes", "original_timestamp", "source_timezone", "production_type", "original_value",
            "original_unit", "value", "unit", "data_status", "provider", "dataset", "raw_sha256"), records)
        return count

    def load_weather(self, sample: dict[str, Any], raw_sha: str = "") -> int:
        """Upsert coordinate weather observations and standardize their units."""
        payload = sample["payload"]
        hourly = payload.get("hourly", {})
        meta = sample.get("metadata", {}).get("geography", {})
        lat, lon = meta.get("latitude", payload.get("latitude")), meta.get("longitude", payload.get("longitude"))
        country_code = meta.get("country_code")
        gid = self._geography("coordinate", f"{lat},{lon}", country=country_code, lat=lat, lon=lon, source=sample["provider"])
        source_zone = sample.get("metadata", {}).get("timezone") or payload.get("timezone") or "UTC"
        mins = int(sample.get("metadata", {}).get("interval_minutes") or 60)
        previous = None
        count = 0
        records = []
        for i, timestamp in enumerate(hourly.get("time", [])):
            utc, tz_label, _ = parse_timestamp(timestamp, source_zone, previous)
            if utc is not None:
                previous = utc
            if utc is None:
                continue
            for name, (variable, target_unit) in WEATHER_VARIABLES.items():
                if name not in hourly:
                    continue
                original_unit = payload.get("hourly_units", {}).get(name, "")
                original = hourly[name][i] if i < len(hourly[name]) else None
                stale_flags = hourly.get(f"{name}_stale", [])
                value, status = classify(original, bool(stale_flags[i]) if i < len(stale_flags) else False)
                key = _key(sample["provider"], gid, variable, utc, mins)
                records.append([key, gid, utc, utc + timedelta(minutes=mins), mins, timestamp, tz_label, variable, value, original_unit, convert(value, original_unit, target_unit), target_unit, status, sample["provider"], sample["dataset"], raw_sha])
                count += 1
        self._bulk_insert("fct_weather", ("fact_key", "geography_id", "interval_start_utc", "interval_end_utc",
            "interval_minutes", "original_timestamp", "source_timezone", "variable", "original_value", "original_unit",
            "value", "unit", "data_status", "provider", "dataset", "raw_sha256"), records)
        return count


def main(argv: list[str] | None = None) -> int:
    """Load supported samples into the configured local DuckDB file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--database", type=Path, default=Path("data/eurogrid.duckdb"))
    args = parser.parse_args(argv)
    normalizer = Normalizer(args.database)
    try:
        print(normalizer.load_directory(args.raw))
    finally:
        normalizer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

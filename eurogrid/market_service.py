"""Read-only queries for the Phase 5 European market overview.

This service reads only dbt Gold views. HTTP routes call these functions rather
than embedding SQL in route handlers, keeping query logic straightforward to
test without starting the web server.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

MAX_QUERY_DAYS = 31


class MarketDataError(Exception):
    """Raised when requested market coverage is unavailable."""


class MarketService:
    """Run bounded, read-only market queries against a local DuckDB file."""

    def __init__(self, database_path: Path = Path("data/eurogrid.duckdb")) -> None:
        """Remember the database path; each method owns a short-lived connection."""
        self.database_path = database_path

    def _connect(self):
        """Open DuckDB read-only so serving the API cannot modify analytical data."""
        if not self.database_path.exists():
            raise MarketDataError(f"Database file not found: {self.database_path}")
        try:
            return duckdb.connect(str(self.database_path), read_only=True)
        except duckdb.Error as exc:
            raise MarketDataError(f"Could not open the analytics database: {exc}") from exc

    def coverage(self) -> list[dict[str, Any]]:
        """Return only bidding zones with observed price records, for the selector."""
        try:
            with self._connect() as db:
                rows = db.execute("""SELECT country_code,bidding_zone,
                    CAST(first_interval_utc AS VARCHAR) AS first_interval_utc,
                    CAST(last_interval_utc AS VARCHAR) AS last_interval_utc,
                    first_source_date,last_source_date,source_timezone,price_interval_count,
                    valid_price_interval_count,flagged_price_interval_count,provider,dataset
                    FROM gold_market_coverage ORDER BY country_code,bidding_zone""").fetchall()
                columns = [item[0] for item in db.description]
        except duckdb.Error as exc:
            raise MarketDataError(f"Gold market coverage is not built yet; run dbt first. Details: {exc}") from exc
        return [dict(zip(columns, row)) for row in rows]

    def overview(self, bidding_zone: str, start_date: date, end_date: date) -> dict[str, Any]:
        """Return price, generation, demand, flows, weather, quality, and lineage."""
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        if (end_date - start_date).days + 1 > MAX_QUERY_DAYS:
            raise ValueError(f"Choose no more than {MAX_QUERY_DAYS} days at a time")

        markets = {row["bidding_zone"]: row for row in self.coverage()}
        if bidding_zone not in markets:
            raise MarketDataError(f"No ingested price coverage for bidding zone {bidding_zone}")
        market = markets[bidding_zone]
        country = (market["country_code"] or "").lower()
        begin_text, finish_text = start_date.isoformat(), end_date.isoformat()

        try:
            with self._connect() as db:
                prices = self._dict_rows(db, """SELECT CAST(price.interval_start_utc AS VARCHAR) AS interval_start_utc,
                    CAST(price.interval_end_utc AS VARCHAR) AS interval_end_utc,
                    price.original_timestamp, price.interval_minutes, price.price_eur_per_mwh,
                    price.data_status, baseline.mean_price_eur_per_mwh,
                    baseline.baseline_observation_count, price.provider, price.dataset, price.raw_sha256
                    FROM gold_market_price AS price
                    LEFT JOIN gold_price_hourly_baseline AS baseline
                      ON price.geography_id=baseline.geography_id
                     AND extract(hour from timezone('UTC',price.interval_start_utc))=baseline.utc_hour
                    WHERE price.bidding_zone=?
                      AND CAST(substr(price.original_timestamp,1,10) AS DATE) BETWEEN ? AND ?
                    ORDER BY price.interval_start_utc""", [bidding_zone, begin_text, finish_text])
                generation = self._dict_rows(db, """SELECT CAST(interval_start_utc AS VARCHAR) AS interval_start_utc, original_timestamp,
                    production_type, generation_mw, data_status, provider, dataset, raw_sha256
                    FROM gold_generation_mix WHERE lower(country_code)=?
                    AND CAST(substr(original_timestamp,1,10) AS DATE) BETWEEN ? AND ?
                    ORDER BY interval_start_utc,production_type""", [country, begin_text, finish_text])
                demand = self._dict_rows(db, """SELECT CAST(interval_start_utc AS VARCHAR) AS interval_start_utc, original_timestamp,
                    demand_mw, data_status, provider, dataset, raw_sha256
                    FROM gold_demand WHERE lower(country_code)=?
                    AND CAST(substr(original_timestamp,1,10) AS DATE) BETWEEN ? AND ?
                    ORDER BY interval_start_utc""", [country, begin_text, finish_text])
                flows = self._dict_rows(db, """SELECT CAST(interval_start_utc AS VARCHAR) AS interval_start_utc, original_timestamp,
                    net_cross_border_trading_mw, data_status, provider, dataset, raw_sha256
                    FROM gold_cross_border_flow WHERE lower(country_code)=?
                    AND CAST(substr(original_timestamp,1,10) AS DATE) BETWEEN ? AND ?
                    ORDER BY interval_start_utc""", [country, begin_text, finish_text])
                weather = self._dict_rows(db, """SELECT CAST(interval_start_utc AS VARCHAR) AS interval_start_utc, original_timestamp,
                    variable, weather_value, weather_unit, data_status, provider, dataset, raw_sha256
                    FROM gold_weather WHERE lower(country_code)=?
                    AND CAST(substr(original_timestamp,1,10) AS DATE) BETWEEN ? AND ?
                    ORDER BY interval_start_utc,variable""", [country, begin_text, finish_text])
        except duckdb.Error as exc:
            raise MarketDataError(f"Gold models are missing or failed to query; run dbt first. Details: {exc}") from exc

        expected = self._expected_intervals(start_date, end_date, market.get("source_timezone"))
        valid = sum(row["data_status"] == "valid" for row in prices)
        flagged = sum(row["data_status"] in {"missing", "invalid", "stale"} for row in prices)
        missing_intervals = max(0, expected - len({row["interval_start_utc"] for row in prices}))
        non_price_rows = generation + demand + flows + weather
        stale_count = sum(row["data_status"] == "stale" for row in prices + non_price_rows)
        flagged_non_price = sum(row["data_status"] in {"missing", "invalid", "stale"} for row in non_price_rows)
        source_models = {
            "prices": ("gold_market_price", prices),
            "generation": ("gold_generation_mix", generation),
            "demand": ("gold_demand", demand),
            "cross_border_flows": ("gold_cross_border_flow", flows),
            "weather": ("gold_weather", weather),
        }
        source_trace = []
        for label, (model, records) in source_models.items():
            grouped: dict[tuple[Any, Any, Any], int] = {}
            for record in records:
                identity = (record.get("provider"), record.get("dataset"), record.get("raw_sha256"))
                grouped[identity] = grouped.get(identity, 0) + 1
            for (provider, dataset, raw_sha256), record_count in sorted(grouped.items(), key=lambda item: str(item[0])):
                source_trace.append({"measure": label, "model": model, "provider": provider, "dataset": dataset,
                                     "raw_sha256": raw_sha256, "record_count": record_count})
        return {
            "market": market,
            "window": {"start_date": begin_text, "end_date": finish_text, "expected_hourly_intervals": expected},
            "prices": prices,
            "generation": generation,
            "demand": demand,
            "cross_border_flows": flows,
            "weather": weather,
            "quality": {"price_intervals_returned": len(prices), "valid_price_values": valid,
                        "flagged_price_values": flagged, "unobserved_hour_slots_estimate": missing_intervals,
                        "generation_rows": len(generation), "demand_rows": len(demand),
                        "flow_rows": len(flows), "weather_rows": len(weather),
                        "stale_observations": stale_count, "flagged_power_weather_values": flagged_non_price},
            "lineage": {
                "prices": "dbt model gold_market_price → Energy-Charts day_ahead_price",
                "generation": "dbt model gold_generation_mix → Energy-Charts public_power",
                "demand": "dbt model gold_demand → Energy-Charts public_power load series",
                "cross_border_flows": "dbt model gold_cross_border_flow → Energy-Charts public_power net trading series",
                "weather": "dbt model gold_weather → Open-Meteo coordinate reference for the selected country",
                "baseline": "dbt model gold_price_hourly_baseline → mean by UTC hour across all valid stored observations for that zone",
            },
            "source_trace": source_trace,
        }

    @staticmethod
    def _dict_rows(db, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        """Run a parameterized query and map its result columns to dictionaries."""
        cursor = db.execute(sql, params)
        columns = [item[0] for item in cursor.description]
        rows = []
        for row in cursor.fetchall():
            rows.append({key: (value.isoformat() if isinstance(value, datetime) else value)
                         for key, value in zip(columns, row)})
        return rows

    @staticmethod
    def _expected_intervals(start: date, end: date, timezone_name: str | None) -> int:
        """Count local calendar hours correctly across 23/25-hour DST days."""
        try:
            zone = ZoneInfo(timezone_name or "UTC")
        except Exception:
            zone = ZoneInfo("UTC")
        utc = ZoneInfo("UTC")
        total = 0
        day = start
        while day <= end:
            next_day = day + timedelta(days=1)
            local_start = datetime.combine(day, time.min, tzinfo=zone)
            local_end = datetime.combine(next_day, time.min, tzinfo=zone)
            total += int((local_end.astimezone(utc) - local_start.astimezone(utc)).total_seconds() // 3600)
            day = next_day
        return total

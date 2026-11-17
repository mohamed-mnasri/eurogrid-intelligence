"""Read-only queries for the European market overview.

This service reads tested Gold models rather than raw files. It builds one
selector from both price-zone coverage and country/Europe power coverage, so a
selection without a published price can still show its generation and load.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

MAX_QUERY_DAYS = 31
COUNTRY_NAMES = {
    "all": "All Europe (aggregate)",
    "eu": "European Union (aggregate)",
    "de": "Germany",
    "fr": "France",
    "es": "Spain",
    "pl": "Poland",
    "nl": "Netherlands",
    "uk": "United Kingdom",
}
HIDDEN_COUNTRIES = {"it"}  # Stored IT-North data is a sub-country zone, not Italy-wide data.


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
        """List available countries/aggregates, attaching prices where present."""
        try:
            with self._connect() as db:
                price_rows = self._dict_rows(db, """SELECT country_code,bidding_zone,
                    CAST(first_interval_utc AS VARCHAR) AS first_interval_utc,
                    CAST(last_interval_utc AS VARCHAR) AS last_interval_utc,
                    first_source_date,last_source_date,source_timezone,price_interval_count,
                    valid_price_interval_count,flagged_price_interval_count,provider,dataset
                    FROM gold_market_coverage ORDER BY country_code,bidding_zone""", [])
                power_rows = self._dict_rows(db, """SELECT lower(coalesce(country_code,geography_code)) AS country_code,
                    min(substr(original_timestamp,1,10)) AS first_source_date,
                    max(substr(original_timestamp,1,10)) AS last_source_date,
                    min(source_timezone) AS source_timezone,
                    count(*) AS generation_row_count
                    FROM gold_generation_mix
                    GROUP BY lower(coalesce(country_code,geography_code))""", [])
        except duckdb.Error as exc:
            raise MarketDataError(f"Gold market coverage is not built yet; run dbt first. Details: {exc}") from exc

        prices_by_country: dict[str, dict[str, Any]] = {}
        for row in price_rows:
            code = str(row.get("country_code") or "").lower()
            if code and code not in HIDDEN_COUNTRIES:
                prices_by_country[code] = row
        power_by_country = {
            str(row["country_code"]).lower(): row
            for row in power_rows
            if row.get("country_code") and str(row["country_code"]).lower() not in HIDDEN_COUNTRIES
        }

        markets = []
        for code in sorted(set(prices_by_country) | set(power_by_country), key=self._market_sort):
            price = prices_by_country.get(code)
            power = power_by_country.get(code)
            zone = price.get("bidding_zone") if price else None
            if not zone:
                zone = {"all": "ALL", "eu": "EU"}.get(code, code.upper())
            starts = [value for value in (price and price.get("first_source_date"), power and power.get("first_source_date")) if value]
            ends = [value for value in (price and price.get("last_source_date"), power and power.get("last_source_date")) if value]
            markets.append({
                "country_code": code,
                "bidding_zone": zone,
                "market_name": COUNTRY_NAMES.get(code, code.upper()),
                "is_aggregate": code in {"all", "eu"},
                "has_prices": price is not None,
                "first_source_date": min(starts) if starts else None,
                "last_source_date": max(ends) if ends else None,
                "source_timezone": (price and price.get("source_timezone")) or (power and power.get("source_timezone")) or "UTC",
                "price_interval_count": int((price or {}).get("price_interval_count") or 0),
                "valid_price_interval_count": int((price or {}).get("valid_price_interval_count") or 0),
                "flagged_price_interval_count": int((price or {}).get("flagged_price_interval_count") or 0),
                "generation_row_count": int((power or {}).get("generation_row_count") or 0),
                "provider": (price and price.get("provider")) or "energy-charts",
                "dataset": "day_ahead_price" if price else "public_power",
            })
        return markets

    @staticmethod
    def _market_sort(code: str) -> tuple[int, str]:
        """Keep Europe aggregates first, followed by named countries."""
        return ({"all": 0, "eu": 1}.get(code, 2), COUNTRY_NAMES.get(code, code).lower())

    def overview(self, bidding_zone: str, start_date: date, end_date: date) -> dict[str, Any]:
        """Return the selected period's available prices, power, weather and sources."""
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        if (end_date - start_date).days + 1 > MAX_QUERY_DAYS:
            raise ValueError(f"Choose no more than {MAX_QUERY_DAYS} days at a time")

        markets = {row["bidding_zone"]: row for row in self.coverage()}
        if bidding_zone not in markets:
            raise MarketDataError(f"No ingested data for market area {bidding_zone}")
        market = markets[bidding_zone]
        country = str(market["country_code"]).lower()
        aggregate = market["is_aggregate"]
        begin_text, finish_text = start_date.isoformat(), end_date.isoformat()

        try:
            with self._connect() as db:
                prices = []
                if market["has_prices"]:
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

                generation = self._dict_rows(db, """SELECT CAST(interval_start_utc AS VARCHAR) AS interval_start_utc,
                    original_timestamp, production_type, generation_mw, data_status, provider, dataset, raw_sha256
                    FROM gold_generation_mix
                    WHERE lower(coalesce(country_code,geography_code))=?
                      AND CAST(substr(original_timestamp,1,10) AS DATE) BETWEEN ? AND ?
                    ORDER BY interval_start_utc,production_type""", [country, begin_text, finish_text])
                demand = self._dict_rows(db, """SELECT CAST(interval_start_utc AS VARCHAR) AS interval_start_utc,
                    original_timestamp,demand_mw,data_status,provider,dataset,raw_sha256
                    FROM gold_demand
                    WHERE lower(coalesce(country_code,geography_code))=?
                      AND CAST(substr(original_timestamp,1,10) AS DATE) BETWEEN ? AND ?
                    ORDER BY interval_start_utc""", [country, begin_text, finish_text])
                flows = []
                if not aggregate:
                    flows = self._dict_rows(db, """SELECT CAST(interval_start_utc AS VARCHAR) AS interval_start_utc,
                        original_timestamp,net_cross_border_trading_mw,data_status,provider,dataset,raw_sha256
                        FROM gold_cross_border_flow WHERE lower(country_code)=?
                          AND CAST(substr(original_timestamp,1,10) AS DATE) BETWEEN ? AND ?
                        ORDER BY interval_start_utc""", [country, begin_text, finish_text])
                weather = []
                if not aggregate:
                    weather = self._dict_rows(db, """SELECT CAST(interval_start_utc AS VARCHAR) AS interval_start_utc,
                        original_timestamp,variable,weather_value,weather_unit,data_status,provider,dataset,raw_sha256
                        FROM gold_weather WHERE lower(country_code)=?
                          AND CAST(timezone(?,interval_start_utc) AS DATE) BETWEEN ? AND ?
                        ORDER BY interval_start_utc,variable""",
                        [country, market.get("source_timezone") or "UTC", begin_text, finish_text])
        except duckdb.Error as exc:
            raise MarketDataError(f"Gold models are missing or failed to query; run dbt first. Details: {exc}") from exc

        expected = self._expected_intervals(start_date, end_date, market.get("source_timezone"))
        valid = sum(row["data_status"] == "valid" for row in prices)
        flagged = sum(row["data_status"] in {"missing", "invalid", "stale"} for row in prices)
        price_hours = {self._utc_hour(row["interval_start_utc"]) for row in prices}
        missing_intervals = max(0, expected - len(price_hours)) if market["has_prices"] else 0
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
            "quality": {"price_intervals_returned": len(prices), "price_hours_returned": len(price_hours),
                        "valid_price_values": valid,"flagged_price_values": flagged,
                        "unobserved_hour_slots_estimate": missing_intervals,
                        "generation_rows": len(generation),"demand_rows": len(demand),"flow_rows": len(flows),
                        "weather_rows": len(weather),"stale_observations": stale_count,
                        "flagged_power_weather_values": flagged_non_price},
            "lineage": {
                "prices": "Energy-Charts day-ahead price by bidding zone; no single all-Europe price is published.",
                "generation": "Energy-Charts public-power generation series; total power including industry is a separate dataset.",
                "demand": "Energy-Charts public-power load series.",
                "cross_border_flows": "Energy-Charts scheduled cross-border trading series; provider sign is preserved.",
                "weather": "Open-Meteo historical weather at one country reference coordinate.",
                "baseline": "gold_price_hourly_baseline: stored valid price average for the same UTC hour.",
            },
            "source_trace": source_trace,
        }

    @staticmethod
    def _utc_hour(value: str) -> str:
        """Return an ISO UTC hour key, grouping 15-minute source data correctly."""
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(ZoneInfo("UTC"))
        return parsed.replace(minute=0, second=0, microsecond=0).isoformat()

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

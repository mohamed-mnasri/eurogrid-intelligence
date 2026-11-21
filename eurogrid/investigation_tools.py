"""Phase 6 deterministic investigation tools.

Each public method is one *approved tool* (handbook section 12.2). Tools:

* take validated Pydantic inputs (allowed zone, timezone-aware UTC window,
  at most ``MAX_TOOL_DAYS`` days),
* run fixed, parameterised SQL against tested Gold models (read-only),
* return units, completeness and source IDs with every number, and
* fail closed: missing data is reported as ``missing``/``unavailable``,
  never as zero.

Every result carries an :class:`Evidence` record whose ID is a hash of the tool
name, parameters and result, so the same data always yields the same evidence
ID. The same tools are used by the rule-based report (Phase 6), the API
(Phase 8) and the LLM agent (Phase 9); none of them accepts SQL text.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, Field, field_validator, model_validator

from eurogrid.anomaly import DetectorConfig, HourlyObservation, score_series

MAX_TOOL_DAYS = 31
BASELINE_DAYS = 14          # comparable hours: same UTC hour on each of the prior 14 days
COMPLETE_RATIO = 0.95       # project threshold for calling a domain "complete"
UTC = timezone.utc

# Interconnected price zones among the markets currently ingested. This is a
# deliberately small, documented map — not every physical border is listed.
NEIGHBOUR_ZONES: dict[str, list[str]] = {
    "DE-LU": ["FR", "NL", "PL"],
    "FR": ["DE-LU", "ES"],
    "NL": ["DE-LU"],
    "PL": ["DE-LU"],
    "ES": ["FR"],
}
WIND_TYPES = {"wind_onshore", "wind_offshore"}
SOLAR_TYPES = {"solar"}
RENEWABLE_TYPES = WIND_TYPES | SOLAR_TYPES | {
    "hydro_run_of_river", "hydro_water_reservoir", "biomass", "geothermal", "other_renewables"}
FOSSIL_PREFIX = "fossil_"
WEATHER_VARIABLES = ("temperature", "wind_speed", "shortwave_radiation")
DOMAINS = ("price", "generation", "load", "cross_border_flows", "weather", "outages")

Status = Literal["complete", "partial", "missing", "unavailable", "not_applicable"]


class ToolError(Exception):
    """Raised for rejected tool inputs (unknown zone, bad window)."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class ToolWindow(BaseModel):
    """Validated tool input shared by every market tool."""

    zone: str = Field(min_length=2, max_length=16)
    start_utc: datetime
    end_utc: datetime

    @field_validator("start_utc", "end_utc")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must include a timezone (use UTC)")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _bounded(self) -> "ToolWindow":
        if self.end_utc <= self.start_utc:
            raise ValueError("end_utc must be after start_utc")
        if self.end_utc - self.start_utc > timedelta(days=MAX_TOOL_DAYS):
            raise ValueError(f"tool windows are limited to {MAX_TOOL_DAYS} days")
        return self

    @property
    def expected_hours(self) -> int:
        return int((self.end_utc - self.start_utc) / timedelta(hours=1))


class Evidence(BaseModel):
    """What was asked, which models answered, what came back and from which raw files."""

    evidence_id: str
    tool_name: str
    parameters: dict[str, Any]
    models: list[str]
    status: Status
    result: dict[str, Any]
    source_ids: list[str]


class ToolResult(BaseModel):
    """Standard tool output: status, unit, compact summary and its evidence."""

    tool_name: str
    status: Status
    unit: str | None = None
    summary: dict[str, Any]
    notes: list[str] = []
    evidence: Evidence


def make_result(tool_name: str, parameters: dict[str, Any], models: list[str], status: Status,
                summary: dict[str, Any], source_ids: set[str] | list[str], unit: str | None = None,
                notes: list[str] | None = None) -> ToolResult:
    """Wrap a summary with a deterministic evidence record."""
    sources = sorted({source for source in source_ids if source})
    evidence_id = "ev_" + hashlib.sha256(_canonical(
        {"tool": tool_name, "parameters": parameters, "status": status, "result": summary,
         "sources": sources}).encode()).hexdigest()[:16]
    evidence = Evidence(evidence_id=evidence_id, tool_name=tool_name, parameters=parameters, models=models,
                        status=status, result=summary, source_ids=sources)
    return ToolResult(tool_name=tool_name, status=status, unit=unit, summary=summary,
                      notes=notes or [], evidence=evidence)


def coverage_status(observed_hours: int, expected_hours: int) -> Status:
    """Classify completeness; this is a coverage indicator, not a probability."""
    if expected_hours <= 0 or observed_hours == 0:
        return "missing"
    return "complete" if observed_hours / expected_hours >= COMPLETE_RATIO else "partial"


def comparable_change(series: dict[datetime, float], start: datetime, end: datetime,
                      days: int = BASELINE_DAYS) -> dict[str, Any]:
    """Compare a window with the same UTC hours on each of the prior ``days`` days."""
    observed = {hour: value for hour, value in series.items() if start <= hour < end}
    baseline_values = []
    for hour in observed:
        for offset in range(1, days + 1):
            value = series.get(hour - timedelta(days=offset))
            if value is not None:
                baseline_values.append(value)
    observed_mean = mean(observed.values()) if observed else None
    baseline_mean = mean(baseline_values) if baseline_values else None
    change = (observed_mean - baseline_mean) if observed_mean is not None and baseline_mean is not None else None
    change_pct = (change / abs(baseline_mean) * 100) if change is not None and baseline_mean else None
    return {
        "observed_mean": _round(observed_mean), "baseline_mean": _round(baseline_mean),
        "change": _round(change), "change_pct": _round(change_pct, 1),
        "observed_hours": len(observed), "baseline_values": len(baseline_values),
    }


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


class InvestigationTools:
    """Approved, read-only analytical tools over the Gold DuckDB models."""

    def __init__(self, database_path: Path = Path("data/eurogrid.duckdb")) -> None:
        self.database_path = database_path
        self._zones: dict[str, dict[str, Any]] | None = None

    # ------------------------------------------------------------------ helpers
    def _connect(self):
        if not self.database_path.exists():
            raise ToolError(f"Database file not found: {self.database_path}")
        return duckdb.connect(str(self.database_path), read_only=True)

    def zones(self) -> dict[str, dict[str, Any]]:
        """Allowlist of bidding zones that have price facts, with country and timezone."""
        if self._zones is None:
            with self._connect() as db:
                rows = db.execute("""SELECT bidding_zone, lower(min(country_code)), min(source_timezone),
                    CAST(min(interval_start_utc) AS VARCHAR), CAST(max(interval_end_utc) AS VARCHAR)
                    FROM gold_market_price WHERE bidding_zone IS NOT NULL GROUP BY bidding_zone""").fetchall()
            self._zones = {zone: {"bidding_zone": zone, "country_code": country, "timezone": tz or "UTC",
                                  "first_interval_utc": first, "last_interval_utc": last}
                           for zone, country, tz, first, last in rows}
        return self._zones

    def window(self, zone: str, start_utc: datetime, end_utc: datetime) -> ToolWindow:
        """Validate a tool request and reject zones outside the allowlist."""
        try:
            request = ToolWindow(zone=zone, start_utc=start_utc, end_utc=end_utc)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if request.zone not in self.zones():
            raise ToolError(f"Zone {zone!r} is not an approved geography; choose one of {sorted(self.zones())}")
        return request

    @staticmethod
    def _params(request: ToolWindow, **extra: Any) -> dict[str, Any]:
        return {"zone": request.zone, "start_utc": request.start_utc.isoformat(),
                "end_utc": request.end_utc.isoformat(), **extra}

    @staticmethod
    def _hourly(rows: list[tuple]) -> tuple[dict[datetime, float], set[str]]:
        """Average (epoch seconds, value, raw_sha256) rows into UTC-hour buckets."""
        buckets: dict[datetime, list[float]] = {}
        sources: set[str] = set()
        for stamp, value, source in rows:
            if value is None:
                continue
            hour = datetime.fromtimestamp(float(stamp), UTC).replace(minute=0, second=0, microsecond=0)
            buckets.setdefault(hour, []).append(float(value))
            if source:
                sources.add(source)
        return {hour: mean(values) for hour, values in buckets.items()}, sources

    def price_hours(self, zone: str, start: datetime, end: datetime) -> tuple[dict[datetime, float], set[str]]:
        """Valid hourly prices for an allowlisted zone (used internally and by detection)."""
        if zone not in self.zones():
            raise ToolError(f"Zone {zone!r} is not an approved geography")
        with self._connect() as db:
            rows = db.execute("""SELECT epoch(interval_start_utc), price_eur_per_mwh, raw_sha256 FROM gold_market_price
                WHERE bidding_zone=? AND data_status='valid' AND interval_start_utc>=CAST(? AS TIMESTAMPTZ) AND interval_start_utc<CAST(? AS TIMESTAMPTZ)""",
                [zone, start.isoformat(), end.isoformat()]).fetchall()
        return self._hourly(rows)

    def _power_hours(self, table: str, value_column: str, country: str, start: datetime, end: datetime,
                     extra_filter: str = "", extra_params: list[Any] | None = None):
        # ``table``/``value_column`` come only from the fixed calls in this class.
        with self._connect() as db:
            return db.execute(f"""SELECT epoch(interval_start_utc), {value_column}, raw_sha256 {', production_type' if table == 'gold_generation_mix' else ''}
                FROM {table} WHERE lower(coalesce(country_code, geography_code))=? AND data_status='valid'
                AND interval_start_utc>=CAST(? AS TIMESTAMPTZ) AND interval_start_utc<CAST(? AS TIMESTAMPTZ) {extra_filter}""",
                [country, start.isoformat(), end.isoformat(), *(extra_params or [])]).fetchall()

    # -------------------------------------------------------------------- tools
    def get_market_prices(self, zone: str, start_utc: datetime, end_utc: datetime) -> ToolResult:
        """Hourly day-ahead prices with min/mean/max and completeness."""
        request = self.window(zone, start_utc, end_utc)
        hours, sources = self.price_hours(request.zone, request.start_utc, request.end_utc)
        status = coverage_status(len(hours), request.expected_hours)
        values = list(hours.values())
        summary = {"observed_hours": len(hours), "expected_hours": request.expected_hours,
                   "mean": _round(mean(values)) if values else None,
                   "min": _round(min(values)) if values else None,
                   "max": _round(max(values)) if values else None,
                   "hourly": [{"interval_start_utc": hour.isoformat(), "value": _round(value)}
                              for hour, value in sorted(hours.items())][:24 * MAX_TOOL_DAYS]}
        return make_result("get_market_prices", self._params(request), ["gold_market_price"], status, summary,
                           sources, unit="EUR/MWh")

    def compare_price_baseline(self, zone: str, start_utc: datetime, end_utc: datetime,
                               config: DetectorConfig = DetectorConfig()) -> ToolResult:
        """Observed prices vs comparable-hour robust baseline, with anomaly scores."""
        request = self.window(zone, start_utc, end_utc)
        history_start = request.start_utc - timedelta(days=config.window_days)
        hours, sources = self.price_hours(request.zone, history_start, request.end_utc)
        observations = [HourlyObservation(hour, value) for hour, value in hours.items()]
        scored = score_series(observations, self.zones()[request.zone]["timezone"], config,
                              score_from=request.start_utc)
        scored_hours = [hour for hour in scored if hour.status == "scored"]
        flagged = [hour for hour in scored if hour.is_anomalous]
        observed = [hour.value for hour in scored]
        medians = [hour.baseline_median for hour in scored if hour.baseline_median is not None]
        status = coverage_status(len(scored), request.expected_hours)
        summary = {
            "observed_hours": len(scored), "expected_hours": request.expected_hours,
            "scored_hours": len(scored_hours), "anomalous_hours": len(flagged),
            "observed_mean": _round(mean(observed)) if observed else None,
            "baseline_median_mean": _round(mean(medians)) if medians else None,
            "max_abs_score": _round(max((abs(hour.score) for hour in scored_hours), default=0.0)) if scored_hours else None,
            "baseline_rule": f"median/MAD of the same local hour and day type over the prior {config.window_days} days",
            "threshold": config.threshold,
        }
        if summary["observed_mean"] is not None and summary["baseline_median_mean"]:
            summary["change_pct"] = _round((summary["observed_mean"] - summary["baseline_median_mean"])
                                           / abs(summary["baseline_median_mean"]) * 100, 1)
        return make_result("compare_price_baseline", self._params(request, detector=config.__dict__),
                           ["gold_market_price"], status, summary, sources, unit="EUR/MWh")

    def get_generation_mix(self, zone: str, start_utc: datetime, end_utc: datetime) -> ToolResult:
        """Mean generation by source and group, and change vs comparable hours."""
        request = self.window(zone, start_utc, end_utc)
        country = self.zones()[request.zone]["country_code"]
        rows = self._power_hours("gold_generation_mix", "generation_mw", country,
                                 request.start_utc - timedelta(days=BASELINE_DAYS), request.end_utc)
        by_type: dict[str, list[tuple]] = {}
        for stamp, value, source, production_type in rows:
            by_type.setdefault(production_type, []).append((stamp, value, source))
        sources: set[str] = set()
        per_type: dict[str, dict[datetime, float]] = {}
        for production_type, type_rows in by_type.items():
            per_type[production_type], type_sources = self._hourly(type_rows)
            sources |= type_sources

        def group(types: set[str] | None, prefix: str | None = None) -> dict[datetime, float]:
            names = [name for name in per_type if (types and name in types) or (prefix and name.startswith(prefix))]
            if not names:
                return {}
            # Only sum hours where every member series reported, so gaps are not read as zero output.
            common = set.intersection(*(set(per_type[name]) for name in names))
            return {hour: sum(per_type[name][hour] for name in names) for hour in common}

        groups = {"wind": group(WIND_TYPES), "solar": group(SOLAR_TYPES),
                  "wind_and_solar": group(WIND_TYPES | SOLAR_TYPES), "renewables": group(RENEWABLE_TYPES),
                  "fossil": group(None, FOSSIL_PREFIX), "nuclear": group({"nuclear"})}
        group_summary = {name: comparable_change(series, request.start_utc, request.end_utc)
                         for name, series in groups.items() if series}
        type_summary = {name: comparable_change(series, request.start_utc, request.end_utc)
                        for name, series in sorted(per_type.items())}
        observed_hours = max((item["observed_hours"] for item in type_summary.values()), default=0)
        status = coverage_status(observed_hours, request.expected_hours)
        summary = {"country_code": country, "observed_hours": observed_hours,
                   "expected_hours": request.expected_hours, "groups": group_summary, "by_type": type_summary,
                   "baseline_rule": f"same UTC hours on each of the prior {BASELINE_DAYS} days"}
        return make_result("get_generation_mix", self._params(request), ["gold_generation_mix"], status, summary,
                           sources, unit="MW", notes=["Public-power generation only; industrial self-supply is excluded."])

    def compare_demand(self, zone: str, start_utc: datetime, end_utc: datetime) -> ToolResult:
        """Actual public-grid load vs comparable historical hours (no forecast is ingested)."""
        request = self.window(zone, start_utc, end_utc)
        country = self.zones()[request.zone]["country_code"]
        hours, sources = self._hourly(self._power_hours("gold_demand", "demand_mw", country,
                                      request.start_utc - timedelta(days=BASELINE_DAYS), request.end_utc))
        change = comparable_change(hours, request.start_utc, request.end_utc)
        status = coverage_status(change["observed_hours"], request.expected_hours)
        summary = {"country_code": country, "expected_hours": request.expected_hours, **change,
                   "forecast": "unavailable",
                   "baseline_rule": f"same UTC hours on each of the prior {BASELINE_DAYS} days"}
        return make_result("compare_demand", self._params(request), ["gold_demand"], status, summary, sources,
                           unit="MW", notes=["No load forecast is ingested, so only historical comparison is possible."])

    def get_cross_border_flows(self, zone: str, start_utc: datetime, end_utc: datetime) -> ToolResult:
        """Net scheduled cross-border trading vs comparable hours, provider sign preserved."""
        request = self.window(zone, start_utc, end_utc)
        country = self.zones()[request.zone]["country_code"]
        hours, sources = self._hourly(self._power_hours("gold_cross_border_flow", "net_cross_border_trading_mw",
                                      country, request.start_utc - timedelta(days=BASELINE_DAYS), request.end_utc))
        change = comparable_change(hours, request.start_utc, request.end_utc)
        status = coverage_status(change["observed_hours"], request.expected_hours)
        summary = {"country_code": country, "expected_hours": request.expected_hours, **change,
                   "sign_convention": "Energy-Charts provider sign, preserved as reported",
                   "baseline_rule": f"same UTC hours on each of the prior {BASELINE_DAYS} days"}
        return make_result("get_cross_border_flows", self._params(request), ["gold_cross_border_flow"], status,
                           summary, sources, unit="MW",
                           notes=["Net scheduled trading for the whole country, not per-border physical flows."])

    def get_reported_outages(self, zone: str, start_utc: datetime, end_utc: datetime) -> ToolResult:
        """Outage records are not ingested yet, so this tool returns an explicit unavailable status."""
        request = self.window(zone, start_utc, end_utc)
        summary = {"records": None, "reason": "ENTSO-E unavailability data requires a personal API token and is not ingested."}
        return make_result("get_reported_outages", self._params(request), [], "unavailable", summary, [],
                           notes=["Outage evidence could not be checked; this is not evidence that no outage occurred."])

    def get_weather_context(self, zone: str, start_utc: datetime, end_utc: datetime,
                            variables: tuple[str, ...] = WEATHER_VARIABLES) -> ToolResult:
        """Weather at the country's reference coordinate vs comparable hours."""
        request = self.window(zone, start_utc, end_utc)
        unknown = set(variables) - set(WEATHER_VARIABLES)
        if unknown:
            raise ToolError(f"Unsupported weather variables: {sorted(unknown)}")
        country = self.zones()[request.zone]["country_code"]
        with self._connect() as db:
            rows = db.execute("""SELECT variable, epoch(interval_start_utc), weather_value, raw_sha256, weather_unit,
                latitude, longitude FROM gold_weather WHERE lower(country_code)=? AND data_status='valid'
                AND interval_start_utc>=CAST(? AS TIMESTAMPTZ) AND interval_start_utc<CAST(? AS TIMESTAMPTZ)""",
                [country, (request.start_utc - timedelta(days=BASELINE_DAYS)).isoformat(), request.end_utc.isoformat()]).fetchall()
        sources: set[str] = set()
        result: dict[str, Any] = {}
        location = None
        for variable in variables:
            var_rows = [(stamp, value, source) for name, stamp, value, source, *_ in rows if name == variable]
            hours, var_sources = self._hourly(var_rows)
            sources |= var_sources
            unit = next((unit for name, *_rest, unit, _lat, _lon in rows if name == variable), None)
            result[variable] = {"unit": unit, **comparable_change(hours, request.start_utc, request.end_utc)}
        if rows:
            location = {"latitude": rows[0][5], "longitude": rows[0][6]}
        observed_hours = max((item["observed_hours"] for item in result.values()), default=0)
        status = coverage_status(observed_hours, request.expected_hours)
        summary = {"country_code": country, "reference_location": location, "expected_hours": request.expected_hours,
                   "variables": result}
        return make_result("get_weather_context", self._params(request, variables=list(variables)), ["gold_weather"],
                           status, summary, sources,
                           notes=["Modelled weather at one reference point; it is context, not a national average."])

    def compare_neighbour_prices(self, zone: str, start_utc: datetime, end_utc: datetime) -> ToolResult:
        """Did connected zones' prices move the same way (market-wide) or not (local)?"""
        request = self.window(zone, start_utc, end_utc)
        neighbours = [name for name in NEIGHBOUR_ZONES.get(request.zone, []) if name in self.zones()]
        if not neighbours:
            return make_result("compare_neighbour_prices", self._params(request), [], "not_applicable",
                               {"neighbours": {}}, [], notes=["No connected zone with ingested prices is configured."])
        sources: set[str] = set()
        result = {}
        for neighbour in [request.zone, *neighbours]:
            hours, zone_sources = self.price_hours(neighbour, request.start_utc - timedelta(days=BASELINE_DAYS),
                                                   request.end_utc)
            sources |= zone_sources
            result[neighbour] = comparable_change(hours, request.start_utc, request.end_utc)
        observed = [item for name, item in result.items() if name != request.zone and item["observed_hours"]]
        status: Status = "complete" if len(observed) == len(neighbours) else "partial" if observed else "missing"
        summary = {"zone": result.pop(request.zone), "neighbours": result,
                   "neighbour_map": "Static map of interconnected zones among ingested markets."}
        return make_result("compare_neighbour_prices", self._params(request, neighbours=neighbours),
                           ["gold_market_price"], status, summary, sources, unit="EUR/MWh")

    def get_data_quality_status(self, zone: str, start_utc: datetime, end_utc: datetime,
                                required_domains: tuple[str, ...] = ("price", "generation", "load")) -> ToolResult:
        """Completeness per data domain and which evidence domains are blocked."""
        request = self.window(zone, start_utc, end_utc)
        unknown = set(required_domains) - set(DOMAINS)
        if unknown:
            raise ToolError(f"Unknown data domains: {sorted(unknown)}")
        country = self.zones()[request.zone]["country_code"]
        expected = request.expected_hours
        price_hours, sources = self.price_hours(request.zone, request.start_utc, request.end_utc)
        counts = {"price": len(price_hours)}
        for domain, table, column in (("generation", "gold_generation_mix", "generation_mw"),
                                      ("load", "gold_demand", "demand_mw"),
                                      ("cross_border_flows", "gold_cross_border_flow", "net_cross_border_trading_mw")):
            rows = self._power_hours(table, column, country, request.start_utc, request.end_utc)
            hours, domain_sources = self._hourly([row[:3] for row in rows])
            counts[domain] = len(hours)
            sources |= domain_sources
        with self._connect() as db:
            weather_rows = db.execute("""SELECT epoch(interval_start_utc), weather_value, raw_sha256 FROM gold_weather
                WHERE lower(country_code)=? AND data_status='valid' AND interval_start_utc>=CAST(? AS TIMESTAMPTZ) AND interval_start_utc<CAST(? AS TIMESTAMPTZ)""",
                [country, request.start_utc.isoformat(), request.end_utc.isoformat()]).fetchall()
        counts["weather"] = len(self._hourly(weather_rows)[0])
        domains = {}
        for domain in DOMAINS:
            if domain == "outages":
                domains[domain] = {"status": "unavailable", "completeness_ratio": None, "observed_hours": None}
                continue
            domains[domain] = {"status": coverage_status(counts[domain], expected),
                               "completeness_ratio": _round(counts[domain] / expected, 3) if expected else None,
                               "observed_hours": counts[domain]}
        blocked = [domain for domain in required_domains if domains[domain]["status"] in {"missing", "unavailable"}]
        limited = [domain for domain in required_domains if domains[domain]["status"] == "partial"]
        overall: Status = "missing" if blocked else "partial" if limited else "complete"
        summary = {"expected_hours": expected, "required_domains": list(required_domains), "domains": domains,
                   "blocked_domains": blocked, "partial_domains": limited,
                   "complete_threshold": COMPLETE_RATIO}
        return make_result("get_data_quality_status", self._params(request, required_domains=list(required_domains)),
                           ["gold_market_price", "gold_generation_mix", "gold_demand", "gold_cross_border_flow",
                            "gold_weather"], overall, summary, sources,
                           notes=["Completeness is a data-coverage indicator, not the probability a finding is correct."])

    def registry(self) -> dict[str, Any]:
        """Name → callable map of approved tools (used by the agent allowlist)."""
        return {name: getattr(self, name) for name in APPROVED_TOOLS}


APPROVED_TOOLS = (
    "get_market_prices", "compare_price_baseline", "get_generation_mix", "compare_demand",
    "get_cross_border_flows", "get_reported_outages", "get_weather_context", "compare_neighbour_prices",
    "get_data_quality_status",
)

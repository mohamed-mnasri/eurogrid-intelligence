"""Phase 6 anomaly detection runs, evidence storage and rule-based reports.

Flow
----
1. ``detect_price_anomalies`` loads valid hourly prices (plus look-back history),
   scores them with :mod:`eurogrid.anomaly` and groups events.
2. ``CaseStore`` saves events, evidence and reports in a small local SQLite
   file (``data/metadata/investigations.sqlite``). IDs are deterministic, so
   re-running on the same data overwrites rather than duplicates.
3. ``investigate`` runs a fixed sequence of approved tools and applies written
   rules to separate observations, coinciding relationships, plausible
   contributing factors and proven causes (handbook section 9.3). No LLM is
   used, and confidence is a set of per-domain factors decided by code.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel

from eurogrid.anomaly import AnomalyEvent, DetectorConfig, HourlyObservation, group_events, score_series
from eurogrid.investigation_tools import Evidence, InvestigationTools, ToolError, ToolResult

UTC = timezone.utc
MAX_DETECTION_DAYS = 366
REPORT_GENERATOR = "rule-based-v1"
# Rule thresholds, stored in each report so readers can see why a finding fired.
RULES = {
    "renewable_change_pct": 15.0,
    "demand_change_pct": 5.0,
    "flow_change_mw": 500.0,
    "neighbour_shared_change_pct": 20.0,
}


class Finding(BaseModel):
    """One report statement and the evidence that supports it."""

    statement: str
    evidence_ids: list[str]


class ToolCall(BaseModel):
    """One entry in the investigation trace."""

    step: int
    tool_name: str
    parameters: dict[str, Any]
    status: str
    evidence_id: str


class InvestigationReport(BaseModel):
    """Structured, evidence-referenced investigation of one anomaly."""

    report_id: str
    anomaly_id: str
    generator: str
    status: Literal["complete", "limited", "blocked"]
    anomaly: dict[str, Any]
    window: dict[str, str]
    confidence_factors: dict[str, dict[str, Any]]
    observations: list[Finding]
    coinciding_relationships: list[Finding]
    plausible_contributing_factors: list[Finding]
    proven_causes: list[Finding]
    missing_evidence: list[str]
    limitations: list[str]
    rules: dict[str, float]
    tool_trace: list[ToolCall]
    evidence: list[Evidence]


# --------------------------------------------------------------------- storage
class CaseStore:
    """SQLite store for anomaly events, evidence records and reports."""

    def __init__(self, path: Path = Path("data/metadata/investigations.sqlite")) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS anomaly_event (
                    anomaly_id TEXT PRIMARY KEY, geography TEXT NOT NULL, metric TEXT NOT NULL,
                    direction TEXT NOT NULL, start_utc TEXT NOT NULL, end_utc TEXT NOT NULL,
                    max_abs_score REAL NOT NULL, detector_version TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS anomaly_geo_start ON anomaly_event(geography, start_utc);
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY, tool_name TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS investigation_report (
                    report_id TEXT PRIMARY KEY, anomaly_id TEXT NOT NULL, generator TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS report_anomaly ON investigation_report(anomaly_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_trace (
                    report_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL);
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save_events(self, events: list[AnomalyEvent]) -> int:
        with self._db() as db:
            db.executemany("""INSERT OR REPLACE INTO anomaly_event VALUES (?,?,?,?,?,?,?,?,?)""", [
                (event.anomaly_id, event.geography, event.metric, event.direction, event.start_utc,
                 event.end_utc, event.max_abs_score, event.detector_version, json.dumps(event.to_dict()))
                for event in events])
        return len(events)

    def list_events(self, geography: str | None = None, start_utc: str | None = None, end_utc: str | None = None,
                    direction: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        clauses, params = [], []
        for column, operator, value in (("geography", "=", geography), ("start_utc", ">=", start_utc),
                                        ("start_utc", "<", end_utc), ("direction", "=", direction)):
            if value is not None:
                clauses.append(f"{column} {operator} ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._db() as db:
            total = db.execute(f"SELECT count(*) FROM anomaly_event {where}", params).fetchone()[0]
            rows = db.execute(f"SELECT payload FROM anomaly_event {where} ORDER BY start_utc DESC LIMIT ? OFFSET ?",
                              [*params, limit, offset]).fetchall()
        return [json.loads(row[0]) for row in rows], total

    def get_event(self, anomaly_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT payload FROM anomaly_event WHERE anomaly_id=?", [anomaly_id]).fetchone()
        return json.loads(row[0]) if row else None

    def save_evidence(self, records: list[Evidence]) -> None:
        with self._db() as db:
            db.executemany("INSERT OR REPLACE INTO evidence VALUES (?,?,?)",
                           [(record.evidence_id, record.tool_name, record.model_dump_json()) for record in records])

    def get_evidence(self, evidence_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT payload FROM evidence WHERE evidence_id=?", [evidence_id]).fetchone()
        return json.loads(row[0]) if row else None

    def save_report(self, report: InvestigationReport) -> None:
        self.save_evidence(report.evidence)
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO investigation_report VALUES (?,?,?,?,?,?)",
                       (report.report_id, report.anomaly_id, report.generator, report.status,
                        datetime.now(UTC).isoformat(), report.model_dump_json()))

    def latest_report(self, anomaly_id: str, generator: str | None = None) -> dict | None:
        sql = "SELECT payload FROM investigation_report WHERE anomaly_id=?"
        params: list[Any] = [anomaly_id]
        if generator:
            sql += " AND generator=?"
            params.append(generator)
        with self._db() as db:
            row = db.execute(sql + " ORDER BY created_at DESC LIMIT 1", params).fetchone()
        return json.loads(row[0]) if row else None

    def get_report(self, report_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT payload FROM investigation_report WHERE report_id=?", [report_id]).fetchone()
        return json.loads(row[0]) if row else None

    def list_reports(self, anomaly_id: str) -> list[dict]:
        """Every stored report for an anomaly (all generators), newest first, as summaries."""
        with self._db() as db:
            rows = db.execute("""SELECT report_id, generator, status, created_at FROM investigation_report
                WHERE anomaly_id=? ORDER BY created_at DESC""", [anomaly_id]).fetchall()
        return [{"report_id": a, "generator": b, "status": c, "created_at": d} for a, b, c, d in rows]

    def save_trace(self, report_id: str, trace: dict) -> None:
        """Agent activity trace (node path, who chose each tool, the model's raw draft)."""
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO agent_trace VALUES (?,?,?)",
                       (report_id, datetime.now(UTC).isoformat(), json.dumps(trace)))

    def get_trace(self, report_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT payload FROM agent_trace WHERE report_id=?", [report_id]).fetchone()
        return json.loads(row[0]) if row else None


def partial_domain_notes(domains: dict[str, dict[str, Any]]) -> list[str]:
    """Name every domain that is only partly available, so a limited report says exactly what is missing."""
    notes = []
    for name in ("price", "generation", "load", "cross_border_flows", "weather"):
        info = domains.get(name) or {}
        if info.get("status") == "partial":
            ratio = info.get("completeness_ratio")
            share = f" ({ratio * 100:.0f}% of hours valid)" if ratio is not None else ""
            notes.append(f"{name.replace('_', ' ').capitalize()} data is only partial for the event window{share}; "
                         "findings that use it are limited.")
    return notes


def material_change(tool_name: str, summary: dict[str, Any], spike: bool) -> bool:
    """Does this evidence show a change large enough (by the written RULES) to be called "coinciding"?

    The same thresholds the rule-based investigator uses; applied by code to the agent's draft so a model
    cannot present an unchanged series as a relationship. Price/quality evidence describes the event itself
    and is never a coinciding factor.
    """
    if tool_name == "get_generation_mix":
        change = (summary.get("groups", {}).get("wind_and_solar") or {}).get("change_pct")
        return change is not None and (change <= -RULES["renewable_change_pct"] if spike
                                       else change >= RULES["renewable_change_pct"])
    if tool_name == "compare_demand":
        change = summary.get("change_pct")
        return change is not None and (change >= RULES["demand_change_pct"] if spike
                                       else change <= -RULES["demand_change_pct"])
    if tool_name == "get_cross_border_flows":
        change = summary.get("change")
        return change is not None and abs(change) >= RULES["flow_change_mw"]
    if tool_name == "compare_neighbour_prices":
        moved = [item.get("change_pct") for item in summary.get("neighbours", {}).values()
                 if item.get("change_pct") is not None]
        threshold = RULES["neighbour_shared_change_pct"]
        shared = [pct for pct in moved if (pct >= threshold if spike else pct <= -threshold)]
        return bool(moved) and len(shared) * 2 >= len(moved)
    if tool_name == "get_weather_context":
        return any(abs(item.get("change_pct") or 0) >= RULES["renewable_change_pct"]
                   for item in summary.get("variables", {}).values() if isinstance(item, dict))
    return False


# ------------------------------------------------------------------- detection
def detect_price_anomalies(tools: InvestigationTools, zone: str, start: date, end: date,
                           config: DetectorConfig = DetectorConfig()) -> list[AnomalyEvent]:
    """Score valid hourly prices for local calendar dates ``start``..``end`` (UTC-bounded)."""
    if start > end:
        raise ToolError("start must be on or before end")
    if (end - start).days + 1 > MAX_DETECTION_DAYS:
        raise ToolError(f"detection is limited to {MAX_DETECTION_DAYS} days per run")
    if zone not in tools.zones():
        raise ToolError(f"Zone {zone!r} is not an approved geography; choose one of {sorted(tools.zones())}")
    begin = datetime.combine(start, time.min, tzinfo=UTC)
    finish = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
    hours, _ = tools.price_hours(zone, begin - timedelta(days=config.window_days), finish)
    observations = [HourlyObservation(hour, value) for hour, value in hours.items()]
    scored = score_series(observations, tools.zones()[zone]["timezone"], config, score_from=begin)
    events = group_events(scored, zone, "day_ahead_price", "EUR/MWh", config)
    # Attach the raw files behind each event window for lineage.
    for event in events:
        _, sources = tools.price_hours(zone, datetime.fromisoformat(event.start_utc), datetime.fromisoformat(event.end_utc))
        event.source_ids = sorted(sources)
    return events


# ----------------------------------------------------------------- the report
def _fmt(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


def investigate(event: dict[str, Any], tools: InvestigationTools) -> InvestigationReport:
    """Run the fixed tool sequence for one anomaly and apply the written rules."""
    zone = event["geography"]
    start = datetime.fromisoformat(event["start_utc"])
    end = datetime.fromisoformat(event["end_utc"])
    spike = event["direction"] == "spike"
    trace: list[ToolCall] = []
    results: dict[str, ToolResult] = {}

    def run(name: str, *args: Any) -> ToolResult:
        result = getattr(tools, name)(zone, start, end, *args)
        trace.append(ToolCall(step=len(trace) + 1, tool_name=name, parameters=result.evidence.parameters,
                              status=result.status, evidence_id=result.evidence.evidence_id))
        results[name] = result
        return result

    quality = run("get_data_quality_status", ("price", "generation", "load"))
    domains = quality.summary["domains"]
    confidence = {
        "price_data": domains["price"], "generation_coverage": domains["generation"],
        "load_coverage": domains["load"], "flow_coverage": domains["cross_border_flows"],
        "outage_coverage": domains["outages"], "weather_context": domains["weather"],
        "time_alignment": {"status": "complete", "note": "All series compared on UTC hour buckets."},
    }
    observations: list[Finding] = []
    coinciding: list[Finding] = []
    plausible: list[Finding] = []
    missing: list[str] = []
    limitations = [
        "Public data shows what coincided with the event; it does not prove the price-formation mechanism.",
        "Generation and load are Energy-Charts public-power series, not a complete system balance.",
    ]

    missing.extend(partial_domain_notes(domains))
    if domains["price"]["status"] == "missing":
        report_status: Literal["complete", "limited", "blocked"] = "blocked"
        missing.append("Price data for the event window is missing, so the anomaly cannot be verified.")
    else:
        price = run("compare_price_baseline")
        p = price.summary
        observations.append(Finding(
            statement=(f"{zone} day-ahead prices averaged {_fmt(p['observed_mean'])} EUR/MWh over "
                       f"{p['observed_hours']} hour(s), against a comparable-hour median of "
                       f"{_fmt(p['baseline_median_mean'])} EUR/MWh ({_fmt(p.get('change_pct'))}%). "
                       f"Peak robust score {_fmt(event['max_abs_score'], 2)} (threshold {p['threshold']})."),
            evidence_ids=[price.evidence.evidence_id]))

        # Renewable supply
        generation = run("get_generation_mix")
        groups = generation.summary["groups"]
        ws = groups.get("wind_and_solar")
        if generation.status in {"missing", "unavailable"} or not ws or ws["change_pct"] is None:
            missing.append("Wind/solar generation could not be compared for this window.")
        else:
            change = ws["change_pct"]
            text = (f"Wind and solar generation averaged {_fmt(ws['observed_mean'], 0)} MW, "
                    f"{_fmt(abs(change))}% {'below' if change < 0 else 'above'} the same hours over the prior 14 days.")
            evidence = [generation.evidence.evidence_id]
            supports = (change <= -RULES["renewable_change_pct"]) if spike else (change >= RULES["renewable_change_pct"])
            if supports:
                coinciding.append(Finding(statement=text, evidence_ids=evidence))
                plausible.append(Finding(statement=(
                    f"{'Lower' if spike else 'Higher'} wind/solar output coincided with the price {event['direction']} "
                    "and may have contributed; the data shows a coincidence, not a demonstrated supply effect."),
                    evidence_ids=evidence))
            else:
                observations.append(Finding(statement=text + " This is not a large enough change to suggest a renewable-supply effect.",
                                            evidence_ids=evidence))

        # Demand
        demand = run("compare_demand")
        d = demand.summary
        if demand.status in {"missing", "unavailable"} or d["change_pct"] is None:
            missing.append("Public-grid load could not be compared for this window.")
        else:
            text = (f"Public-grid load averaged {_fmt(d['observed_mean'], 0)} MW, "
                    f"{_fmt(abs(d['change_pct']))}% {'above' if d['change_pct'] >= 0 else 'below'} comparable hours.")
            supports = (d["change_pct"] >= RULES["demand_change_pct"]) if spike else (d["change_pct"] <= -RULES["demand_change_pct"])
            if supports:
                coinciding.append(Finding(statement=text, evidence_ids=[demand.evidence.evidence_id]))
                plausible.append(Finding(statement=(
                    f"{'Higher' if spike else 'Lower'} demand coincided with the event and may have contributed."),
                    evidence_ids=[demand.evidence.evidence_id]))
            else:
                observations.append(Finding(statement=text + " Demand was broadly in line with the reference.",
                                            evidence_ids=[demand.evidence.evidence_id]))
        missing.append("No load forecast is ingested, so forecast error could not be checked.")

        # Cross-border trading
        flows = run("get_cross_border_flows")
        f = flows.summary
        if flows.status in {"missing", "unavailable"} or f["change"] is None:
            missing.append("Cross-border trading could not be compared for this window.")
        elif abs(f["change"]) >= RULES["flow_change_mw"]:
            coinciding.append(Finding(statement=(
                f"Net scheduled cross-border trading changed by {_fmt(f['change'], 0)} MW versus comparable hours "
                f"(from {_fmt(f['baseline_mean'], 0)} to {_fmt(f['observed_mean'], 0)} MW, provider sign)."),
                evidence_ids=[flows.evidence.evidence_id]))
        else:
            observations.append(Finding(statement=(
                f"Net scheduled cross-border trading changed by {_fmt(f['change'], 0)} MW, below the "
                f"{_fmt(RULES['flow_change_mw'], 0)} MW rule threshold."), evidence_ids=[flows.evidence.evidence_id]))

        # Neighbouring markets
        neighbours = run("compare_neighbour_prices")
        moved = {name: item["change_pct"] for name, item in neighbours.summary.get("neighbours", {}).items()
                 if item["change_pct"] is not None}
        if neighbours.status == "not_applicable" or not moved:
            missing.append("No connected market with price data was available for comparison.")
        else:
            threshold = RULES["neighbour_shared_change_pct"]
            shared = [name for name, pct in moved.items() if (pct >= threshold if spike else pct <= -threshold)]
            detail = ", ".join(f"{name} {_fmt(pct)}%" for name, pct in sorted(moved.items()))
            if len(shared) * 2 >= len(moved):
                coinciding.append(Finding(statement=(
                    f"Connected markets moved the same way ({detail}), so the event appears shared across "
                    "connected markets rather than local to this zone."), evidence_ids=[neighbours.evidence.evidence_id]))
            else:
                observations.append(Finding(statement=(
                    f"Most connected markets did not move as strongly ({detail}); the event looks mainly local to {zone}."),
                    evidence_ids=[neighbours.evidence.evidence_id]))

        # Weather context
        weather = run("get_weather_context")
        variables = weather.summary["variables"]
        if weather.status in {"missing", "unavailable"}:
            missing.append("Weather context was unavailable for this window.")
        else:
            parts = []
            for name, label in (("wind_speed", "wind speed"), ("shortwave_radiation", "sunlight"),
                                ("temperature", "temperature")):
                item = variables.get(name, {})
                if item.get("observed_mean") is not None and item.get("baseline_mean") is not None:
                    parts.append(f"{label} {_fmt(item['observed_mean'])} {item.get('unit') or ''} "
                                 f"(comparable hours {_fmt(item['baseline_mean'])})".replace("  ", " "))
            if parts:
                observations.append(Finding(statement="Weather at the reference location: " + "; ".join(parts) + ".",
                                            evidence_ids=[weather.evidence.evidence_id]))

        # Outages
        outages = run("get_reported_outages")
        missing.append("Outage evidence could not be checked (ENTSO-E unavailability data is not ingested).")
        del outages

        core_partial = any(domains[name]["status"] != "complete" for name in ("price", "generation", "load"))
        report_status = "limited" if core_partial else "complete"
        if core_partial:
            limitations.append("At least one core data domain is incomplete for the event window; findings are limited.")

    evidence = [result.evidence for result in results.values()]
    report_id = "rpt_" + hashlib.sha256(json.dumps(
        {"anomaly": event["anomaly_id"], "generator": REPORT_GENERATOR, "rules": RULES,
         "evidence": [item.evidence_id for item in evidence]}, sort_keys=True).encode()).hexdigest()[:16]
    return InvestigationReport(
        report_id=report_id, anomaly_id=event["anomaly_id"], generator=REPORT_GENERATOR, status=report_status,
        anomaly=event, window={"start_utc": event["start_utc"], "end_utc": event["end_utc"]},
        confidence_factors=confidence, observations=observations, coinciding_relationships=coinciding,
        plausible_contributing_factors=plausible, proven_causes=[],
        missing_evidence=missing,
        limitations=limitations + ["No cause is marked as proven: the available public data cannot establish causation."],
        rules=RULES, tool_trace=trace, evidence=evidence)


def render_markdown(report: InvestigationReport | dict[str, Any]) -> str:
    """Human-readable version of a report, with evidence IDs beside each statement."""
    data = report.model_dump() if isinstance(report, InvestigationReport) else report
    event = data["anomaly"]
    lines = [f"# Investigation {data['report_id']}", "",
             f"**Anomaly:** {event['anomaly_id']} · {event['geography']} · price {event['direction']} · "
             f"{event['start_utc']} → {event['end_utc']} ({event['hour_count']} h)",
             f"**Status:** {data['status']} · **Generator:** {data['generator']}", ""]

    def section(title: str, findings: list[dict[str, Any]], empty: str) -> None:
        lines.extend([f"## {title}", ""])
        if not findings:
            lines.append(f"- {empty}")
        for finding in findings:
            lines.append(f"- {finding['statement']} [{', '.join(finding['evidence_ids'])}]")
        lines.append("")

    section("Observations", data["observations"], "None.")
    section("Relationships that coincide with the event", data["coinciding_relationships"], "None found.")
    section("Plausible contributing factors", data["plausible_contributing_factors"], "None supported by the evidence.")
    section("Proven causes", data["proven_causes"], "None. Public data does not establish causation.")
    lines.extend(["## Missing evidence", ""] + [f"- {item}" for item in data["missing_evidence"]] + [""])
    lines.extend(["## Confidence factors", "", "| Domain | Status | Completeness |", "|---|---|---|"])
    for name, factor in data["confidence_factors"].items():
        ratio = factor.get("completeness_ratio")
        lines.append(f"| {name} | {factor.get('status')} | {'' if ratio is None else f'{ratio:.0%}'} |")
    lines.extend(["", "## Tool trace", ""])
    for call in data["tool_trace"]:
        lines.append(f"{call['step']}. `{call['tool_name']}` → {call['status']} ({call['evidence_id']})")
    lines.extend(["", "## Limitations", ""] + [f"- {item}" for item in data["limitations"]])
    return "\n".join(lines) + "\n"

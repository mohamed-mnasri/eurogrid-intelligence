"""Scenario Lab: controlled synthetic cases with known injected conditions (handbook 13.3).

Each case builds its own synthetic DuckDB (:mod:`eurogrid.synthetic`), detects the
injected DE-LU spike, investigates it and scores the report. The cases, the
scoring rules and every measured result are published as they are; there is no
blended "accuracy" number. Synthetic injected conditions are not historical
real-world events.

Scored dimensions (pass/fail per case):

* ``detected``               – the detector found an event overlapping the injected window.
* ``tool_selection``         – every tool needed to see the injected condition was called.
* ``parameter_accuracy``     – every tool call used the event's zone and stayed inside its window.
* ``evidence_accuracy``      – every finding cites at least one evidence ID stored with the report.
* ``data_quality_handling``  – report status matches the expectation and missing domains are named.
* ``expected_signals``       – the injected condition appears in a coinciding/plausible finding.
* ``no_false_signals``       – conditions that were *not* injected are not presented as coinciding/plausible.
* ``unsupported_claims``     – zero statements with causal wording and no proven causes (target: zero).
* ``repeatability``          – investigating again gives the same report ID and statements (rule-based only).

With ``repeats > 1`` the whole suite runs several times; a check passes only if it passed in every run, and
``stability`` reports how many runs each case passed. Every run records investigation latency and, for the
agent, model calls and tokens.

Not covered yet (documented, not hidden): outage records overlapping the event
(no outage source is connected), timestamp shifts and duplicate intervals
(caught by dbt uniqueness tests and chaos mode rather than by the investigator).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
import time
from contextlib import closing
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from eurogrid.investigation import CaseStore, detect_price_anomalies, investigate
from eurogrid.investigation_tools import InvestigationTools
from eurogrid.synthetic import EVENT_END, EVENT_START, Conditions, build_synthetic_database, delete_event_prices

UTC = timezone.utc
CAUSAL_WORDS = re.compile(r"\b(caus(e|ed|es|ing)|because|due to|led to|result(ed|s)? in|prove[sdn]?|proof)\b",
                          re.IGNORECASE)
SIGNAL_TOOLS = ("get_generation_mix", "compare_demand", "get_cross_border_flows", "compare_neighbour_prices")


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    label: str
    description: str
    conditions: Conditions
    expected_signals: tuple[str, ...]          # tools whose evidence should support a coinciding/plausible finding
    expected_status: str
    expected_missing: tuple[str, ...] = ()     # words that must appear in missing_evidence
    blocked_after_detection: bool = False


CASES: tuple[EvaluationCase, ...] = (
    EvaluationCase("wind_drop", "Sudden wind decrease",
                   "German wind falls from 20 GW to 2 GW and wind speed from 6 to 1 m/s; load unchanged.",
                   Conditions(demand_mw=None), ("get_generation_mix",), "complete"),
    EvaluationCase("demand_spike", "Demand spike",
                   "German load rises 20% (50 → 60 GW); wind and weather unchanged.",
                   Conditions(wind_mw=None, wind_speed_ms=None, demand_mw=60000.0), ("compare_demand",), "complete"),
    EvaluationCase("import_reduction", "Cross-border import reduction",
                   "Net scheduled trading swings by 3 GW (+1 GW → −2 GW, provider sign); supply and load unchanged.",
                   Conditions(wind_mw=None, wind_speed_ms=None, demand_mw=None, net_trading_mw=-2000.0),
                   ("get_cross_border_flows",), "complete"),
    EvaluationCase("regional_event", "Shared regional spike",
                   "France spikes with Germany (380 EUR/MWh); German supply and load unchanged.",
                   Conditions(wind_mw=None, wind_speed_ms=None, demand_mw=None, neighbour_price=380.0),
                   ("compare_neighbour_prices",), "complete"),
    EvaluationCase("no_clear_explanation", "No clear single explanation",
                   "Only the price moves; generation, load, trading, weather and neighbours stay at baseline.",
                   Conditions(wind_mw=None, wind_speed_ms=None, demand_mw=None), (), "complete"),
    EvaluationCase("missing_generation", "Missing generation feed",
                   "Wind falls as in wind_drop, but German generation rows are deleted for the event window.",
                   Conditions(demand_mw=None, missing_generation=True), (), "limited", ("generation",)),
    EvaluationCase("missing_price", "Missing price feed after detection",
                   "The spike is detected, then DE-LU prices for the window are deleted before investigating.",
                   Conditions(), (), "blocked", ("price",), blocked_after_detection=True),
)


@dataclass
class CaseResult:
    case_id: str
    label: str
    description: str
    generator: str
    status: str | None
    report_id: str | None
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())


Investigator = Callable[[dict[str, Any], InvestigationTools, CaseStore, Path], dict[str, Any]]


def rule_based(event: dict[str, Any], tools: InvestigationTools, store: CaseStore, _: Path) -> dict[str, Any]:
    report = investigate(event, tools)
    store.save_report(report)
    return report.model_dump()


class agent_investigator:
    """The Phase 9 agent with a local Ollama model (slow; CLI only). Keeps the last run's telemetry."""

    def __init__(self, model: str, max_tool_calls: int = 6) -> None:
        self.model, self.max_tool_calls = model, max_tool_calls
        self.last_telemetry: dict[str, Any] | None = None

    def __call__(self, event: dict[str, Any], tools: InvestigationTools, store: CaseStore, out_dir: Path) -> dict[str, Any]:
        from eurogrid.agent import run_investigation
        from eurogrid.llm import OllamaLLM

        final = run_investigation(event["anomaly_id"], tools, store, OllamaLLM(self.model), out_dir, self.max_tool_calls)
        self.last_telemetry = final.get("telemetry")
        return final["report"]


def _cited_tools(report: dict[str, Any]) -> set[str]:
    by_id = {item["evidence_id"]: item["tool_name"] for item in report["evidence"]}
    return {by_id.get(ref, "") for finding in report["coinciding_relationships"] + report["plausible_contributing_factors"]
            for ref in finding["evidence_ids"]}


def score_case(case: EvaluationCase, event: dict[str, Any] | None, report: dict[str, Any] | None,
               repeat: dict[str, Any] | None, generator: str) -> CaseResult:
    result = CaseResult(case.case_id, case.label, case.description, generator,
                        report["status"] if report else None, report["report_id"] if report else None)
    result.checks["detected"] = event is not None
    if event is None or report is None:
        return result
    findings = report["observations"] + report["coinciding_relationships"] + report["plausible_contributing_factors"]
    known = {item["evidence_id"] for item in report["evidence"]}
    called = {call["tool_name"] for call in report["tool_trace"]}
    start, end = datetime.fromisoformat(event["start_utc"]), datetime.fromisoformat(event["end_utc"])
    params_ok = all(call["parameters"].get("zone", event["geography"]) == event["geography"]
                    and datetime.fromisoformat(call["parameters"]["start_utc"]) >= start
                    and datetime.fromisoformat(call["parameters"]["end_utc"]) <= end
                    for call in report["tool_trace"] if "start_utc" in call["parameters"])
    missing_text = " ".join(report["missing_evidence"]).lower()
    cited = _cited_tools(report)
    unexpected = sorted(set(SIGNAL_TOOLS) - set(case.expected_signals) & cited)
    causal = [finding["statement"] for finding in findings if CAUSAL_WORDS.search(finding["statement"])]

    result.checks["tool_selection"] = case.expected_status == "blocked" or set(case.expected_signals) <= called
    result.checks["parameter_accuracy"] = params_ok
    result.checks["evidence_accuracy"] = all(finding["evidence_ids"] and set(finding["evidence_ids"]) <= known
                                             for finding in findings)
    result.checks["data_quality_handling"] = (report["status"] == case.expected_status
                                              and all(word in missing_text for word in case.expected_missing))
    result.checks["expected_signals"] = set(case.expected_signals) <= cited
    result.checks["no_false_signals"] = not unexpected
    result.checks["unsupported_claims"] = not causal and not report["proven_causes"]
    if repeat is not None:
        result.checks["repeatability"] = (repeat["report_id"] == report["report_id"]
                                          and [f["statement"] for f in repeat["observations"]]
                                          == [f["statement"] for f in report["observations"]])
    result.details = {"event": {key: event[key] for key in ("anomaly_id", "start_utc", "end_utc", "max_abs_score")},
                      "tools_called": [call["tool_name"] for call in report["tool_trace"]],
                      "cited_signal_tools": sorted(cited & set(SIGNAL_TOOLS)), "unexpected_signals": unexpected,
                      "causal_statements": causal, "missing_evidence": report["missing_evidence"],
                      "finding_counts": {"observations": len(report["observations"]),
                                         "coinciding": len(report["coinciding_relationships"]),
                                         "plausible": len(report["plausible_contributing_factors"])}}
    return result


def run_case(case: EvaluationCase, investigator: Investigator, generator: str, workdir: Path) -> CaseResult:
    db_path = build_synthetic_database(workdir / f"{case.case_id}.duckdb", case.conditions)
    tools = InvestigationTools(db_path)
    store = CaseStore(workdir / f"{case.case_id}.sqlite")
    events = detect_price_anomalies(tools, "DE-LU", date(2026, 3, 1), date(2026, 3, 12))
    window = (datetime.fromisoformat(EVENT_START), datetime.fromisoformat(EVENT_END))
    event = next((item.to_dict() for item in events
                  if datetime.fromisoformat(item.start_utc) < window[1] and datetime.fromisoformat(item.end_utc) > window[0]),
                 None)
    if event is None:
        return score_case(case, None, None, None, generator)
    store.save_events([item for item in events if item.anomaly_id == event["anomaly_id"]])
    if case.blocked_after_detection:
        delete_event_prices(db_path)
    started = time.perf_counter()
    report = investigator(event, tools, store, workdir / "reports")
    elapsed = time.perf_counter() - started
    telemetry = getattr(investigator, "last_telemetry", None)
    repeat = investigator(event, tools, store, workdir / "reports") if generator == "rule-based-v1" else None
    result = score_case(case, event, report, repeat, generator)
    result.details["investigation_s"] = round(elapsed, 3)
    if telemetry and telemetry.get("measured"):
        result.details["llm"] = {key: telemetry[key] for key in ("calls", "failed_calls", "model_s",
                                                                 "prompt_tokens", "completion_tokens")}
    return result


def _percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))], 3)


def run_suite(generator: str = "rule-based", model: str = "qwen3.5:4b",
              cases: tuple[EvaluationCase, ...] = CASES, repeats: int = 1) -> dict[str, Any]:
    investigator, name = ((rule_based, "rule-based-v1") if generator == "rule-based"
                          else (agent_investigator(model), f"langgraph-ollama:{model}"))
    runs: list[list[CaseResult]] = []
    for _ in range(max(1, repeats)):
        with tempfile.TemporaryDirectory() as temp:
            runs.append([run_case(case, investigator, name, Path(temp)) for case in cases])
    # A check passes only if it passed in every run; details come from the first run.
    results = []
    for index, first in enumerate(runs[0]):
        merged = CaseResult(first.case_id, first.label, first.description, name, first.status, first.report_id,
                            dict(first.checks), dict(first.details))
        for run in runs[1:]:
            for key, ok in run[index].checks.items():
                merged.checks[key] = merged.checks.get(key, True) and ok
        merged.details["passed_runs"] = sum(1 for run in runs if run[index].passed)
        results.append(merged)
    durations = [result.details["investigation_s"] for run in runs for result in run if "investigation_s" in result.details]
    llm = [result.details["llm"] for run in runs for result in run if "llm" in result.details]
    performance = {"investigations": len(durations), "median_s": _percentile(durations, 0.5),
                   "p95_s": _percentile(durations, 0.95), "max_s": round(max(durations), 3) if durations else None}
    if llm:
        performance.update({
            "median_model_calls": statistics.median(item["calls"] for item in llm),
            "median_prompt_tokens": statistics.median(item["prompt_tokens"] for item in llm),
            "median_completion_tokens": statistics.median(item["completion_tokens"] for item in llm),
            "total_tokens": sum(item["prompt_tokens"] + item["completion_tokens"] for item in llm),
            "failed_model_calls": sum(item["failed_calls"] for item in llm)})
    dimensions = sorted({key for result in results for key in result.checks})
    return {
        "generator": name, "created_at": datetime.now(UTC).isoformat(), "dataset": "synthetic (eurogrid.synthetic)",
        "cases": [{**asdict(result), "passed": result.passed} for result in results],
        "dimension_totals": {key: {"passed": sum(1 for r in results if r.checks.get(key)),
                                   "scored": sum(1 for r in results if key in r.checks)} for key in dimensions},
        "cases_passed": sum(1 for result in results if result.passed), "cases_total": len(results),
        "repeats": len(runs), "cases_passed_per_run": [sum(1 for result in run if result.passed) for run in runs],
        "performance": performance,
        "rules": __doc__.split("Scored dimensions (pass/fail per case):")[1].split("Not covered yet")[0].strip(),
        "not_covered": "Outage records overlapping the event (no outage source connected); timestamp shifts and "
                       "duplicate intervals (handled by dbt tests and chaos mode).",
        "case_definitions": [{"case_id": case.case_id, "label": case.label, "description": case.description,
                              "expected_signals": list(case.expected_signals), "expected_status": case.expected_status,
                              "conditions": asdict(case.conditions)} for case in cases],
    }


class EvaluationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS evaluation_run (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT, generator TEXT NOT NULL, created_at TEXT NOT NULL,
                payload TEXT NOT NULL)""")

    def save(self, payload: dict[str, Any]) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO evaluation_run (generator, created_at, payload) VALUES (?,?,?)",
                       (payload["generator"], payload["created_at"], json.dumps(payload)))

    def latest(self) -> list[dict[str, Any]]:
        """The most recent run for each generator."""
        with closing(sqlite3.connect(self.path)) as db, db:
            rows = db.execute("""SELECT payload FROM evaluation_run r WHERE run_id =
                (SELECT max(run_id) FROM evaluation_run x WHERE x.generator = r.generator) ORDER BY generator""").fetchall()
        return [json.loads(row[0]) for row in rows]

    def history(self, limit: int = 30) -> list[dict[str, Any]]:
        """Summary of past runs, newest first, so changes to the agent or rules can be compared over time."""
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT run_id, payload FROM evaluation_run ORDER BY run_id DESC LIMIT ?", [limit]).fetchall()
        items = []
        for run_id, payload in rows:
            data = json.loads(payload)
            items.append({"run_id": run_id, "generator": data["generator"], "created_at": data["created_at"],
                          "cases_passed": data["cases_passed"], "cases_total": data["cases_total"],
                          "repeats": data.get("repeats", 1), "performance": data.get("performance"),
                          "failed_checks": {case["case_id"]: [k for k, v in case["checks"].items() if not v]
                                            for case in data["cases"] if not case["passed"]}})
        return items


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run the Scenario Lab evaluation suite")
    parser.add_argument("--generator", choices=["rule-based", "agent"], default="rule-based")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--repeats", type=int, default=1, help="run the suite N times; a check must pass in every run")
    parser.add_argument("--store", type=Path, default=Path("data/metadata/evaluations.sqlite"))
    args = parser.parse_args(argv)
    payload = run_suite(args.generator, args.model, repeats=args.repeats)
    EvaluationStore(args.store).save(payload)
    print(f"{payload['generator']}: {payload['cases_passed']}/{payload['cases_total']} cases passed every check "
          f"in all {payload['repeats']} run(s); per run: {payload['cases_passed_per_run']}")
    print(f"performance: {json.dumps(payload['performance'])}")
    for case in payload["cases"]:
        failed = [name for name, ok in case["checks"].items() if not ok]
        print(f"  {'PASS' if case['passed'] else 'FAIL'}  {case['case_id']:<22} status={case['status']}"
              + (f"  failed: {', '.join(failed)}" if failed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

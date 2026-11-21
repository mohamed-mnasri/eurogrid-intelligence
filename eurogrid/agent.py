"""Phase 9 LangGraph investigation agent.

The graph (each box is a node, arrows are edges):

    load_event → quality_gate ─(price missing)──────────────────────────┐
                      │                                                 │
                      ▼                                                 │
                 choose_tool ⇄ run_tool   (model picks, code runs)      │
                      │                                                 │
                      ▼                                                 ▼
               validate_evidence → write_report (model) → check_report (code) → END

What the model does: picks the next approved tool by name, then drafts
findings that cite evidence IDs. What code does: everything else — loading the
anomaly, the data-quality gate, running tools under the policy, rejecting
findings without valid evidence or with causal claims, confidence factors,
status, and saving the report and trace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from eurogrid.agent_tools import TOOL_DESCRIPTIONS, PolicyViolation, ToolCallRequest, ToolPolicy, execute_tool
from eurogrid.investigation import CaseStore, Finding, InvestigationReport, ToolCall, render_markdown
from eurogrid.investigation_tools import Evidence, InvestigationTools
from eurogrid.llm import LLM, LLMError, OllamaLLM

CORE_DOMAINS = ("price", "generation", "load")
UNUSABLE = {"missing", "unavailable", "not_applicable"}
CAUSAL_WORDS = re.compile(r"\b(caus(e|ed|es|ing)|because|due to|led to|result(ed|s)? in|prove[sdn]?|proof)\b",
                          re.IGNORECASE)


# ----------------------------------------------------------------- step 3: state
class AgentState(TypedDict, total=False):
    anomaly_id: str
    anomaly: dict[str, Any]
    geography: str
    time_window: dict[str, str]
    quality: dict[str, Any]
    available_evidence: list[dict[str, Any]]
    missing_evidence: list[str]
    tool_trace: list[dict[str, Any]]
    node_trace: list[str]
    next_tool: str
    draft_findings: list[dict[str, Any]]
    report: dict[str, Any]
    status: str


# ------------------------------------------------- what the model must reply with
class ToolChoice(BaseModel):
    next_tool: Literal[
        "compare_price_baseline", "get_generation_mix", "compare_demand", "get_cross_border_flows",
        "compare_neighbour_prices", "get_weather_context", "get_reported_outages", "finish"]
    reason: str = Field(max_length=300)


class DraftFinding(BaseModel):
    category: Literal["observation", "coinciding", "plausible"]
    statement: str = Field(max_length=400)
    evidence_ids: list[str]


class DraftReport(BaseModel):
    findings: list[DraftFinding] = Field(max_length=12)


def compact(summary: dict[str, Any]) -> str:
    """Shorten a tool summary for the prompt (drop long hourly/per-source lists)."""
    small = {key: value for key, value in summary.items() if key not in {"hourly", "by_type"}}
    return json.dumps(small, default=str)[:1200]


# ------------------------------------------------------------- step 4: the graph
def build_graph(tools: InvestigationTools, store: CaseStore, llm: LLM, max_tool_calls: int = 6):
    """Wire the nodes together. Tools, store and model are passed in, so tests can use fakes."""

    def load_event(state: AgentState) -> AgentState:
        anomaly = store.get_event(state["anomaly_id"])
        if anomaly is None:
            raise ValueError(f"Unknown anomaly {state['anomaly_id']}; run detection first")
        return {"anomaly": anomaly, "geography": anomaly["geography"],
                "time_window": {"start_utc": anomaly["start_utc"], "end_utc": anomaly["end_utc"]},
                "available_evidence": [], "missing_evidence": [], "tool_trace": [],
                "node_trace": ["load_event"], "status": "running"}

    def policy(state: AgentState) -> ToolPolicy:
        return ToolPolicy(allowed_zones=[state["geography"]], max_tool_calls=max_tool_calls,
                          window_start=datetime.fromisoformat(state["time_window"]["start_utc"]),
                          window_end=datetime.fromisoformat(state["time_window"]["end_utc"]))

    def quality_gate(state: AgentState) -> AgentState:
        window = policy(state)
        result = tools.get_data_quality_status(state["geography"], window.window_start, window.window_end)
        domains = result.summary["domains"]
        missing = [f"{name} data is {info['status']} for the event window"
                   for name, info in domains.items() if info["status"] in UNUSABLE]
        trace = [{"step": 1, "tool_name": result.tool_name, "parameters": result.evidence.parameters,
                  "status": result.status, "evidence_id": result.evidence.evidence_id, "chosen_by": "code"}]
        return {"quality": result.summary, "available_evidence": [result.evidence.model_dump()],
                "missing_evidence": missing, "tool_trace": trace,
                "status": "blocked" if domains["price"]["status"] == "missing" else "running",
                "node_trace": state["node_trace"] + ["quality_gate"]}

    def remaining_tools(state: AgentState) -> list[str]:
        used = {call["tool_name"] for call in state["tool_trace"]}
        return [name for name in TOOL_DESCRIPTIONS if name not in used]

    def choose_tool(state: AgentState) -> AgentState:
        remaining = remaining_tools(state)
        done = "\n".join(f"- {e['tool_name']} ({e['status']}): {compact(e['result'])}"
                         for e in state["available_evidence"])
        options = "\n".join(f"- {name}: {TOOL_DESCRIPTIONS[name]}" for name in remaining)
        event = state["anomaly"]
        system = ("You are an electricity-market analyst. Choose the ONE most useful next tool to explain the "
                  "price anomaly, or 'finish' when the evidence is enough. Only choose from the listed tools.")
        user = (f"Anomaly: {event['geography']} price {event['direction']} from {event['start_utc']} to "
                f"{event['end_utc']}, peak {event['observed_value']} EUR/MWh vs usual {event['baseline_median']}.\n"
                f"Evidence so far:\n{done}\n\nTools you may still use:\n{options}")
        chosen_by = "model"
        try:
            choice = llm.structured(system, user, ToolChoice).next_tool
        except LLMError:
            choice, chosen_by = (remaining[0] if remaining else "finish"), "code (model reply was invalid)"
        if choice != "finish" and choice not in remaining:
            choice, chosen_by = (remaining[0] if remaining else "finish"), "code (model repeated a tool)"
        return {"next_tool": choice, "node_trace": state["node_trace"] + [f"choose_tool:{choice} by {chosen_by}"]}

    def run_tool(state: AgentState) -> AgentState:
        window = policy(state)
        request = ToolCallRequest(tool_name=state["next_tool"], zone=state["geography"],
                                  start_utc=window.window_start, end_utc=window.window_end)
        calls = len(state["tool_trace"]) - 1          # the quality gate does not count toward the budget
        try:
            result = execute_tool(tools, request, window, calls)
        except PolicyViolation as exc:
            return {"missing_evidence": state["missing_evidence"] + [f"Blocked tool call: {exc}"],
                    "node_trace": state["node_trace"] + ["run_tool:blocked"]}
        missing = list(state["missing_evidence"])
        if result.status in UNUSABLE:
            missing.append(f"{result.tool_name} returned {result.status}: " + "; ".join(result.notes))
        trace = state["tool_trace"] + [{"step": len(state["tool_trace"]) + 1, "tool_name": result.tool_name,
                                        "parameters": result.evidence.parameters, "status": result.status,
                                        "evidence_id": result.evidence.evidence_id, "chosen_by": "model"}]
        return {"available_evidence": state["available_evidence"] + [result.evidence.model_dump()],
                "missing_evidence": missing, "tool_trace": trace,
                "node_trace": state["node_trace"] + [f"run_tool:{result.tool_name}"]}

    def validate_evidence(state: AgentState) -> AgentState:
        """Code makes sure the price comparison exists, whatever the model chose."""
        update: AgentState = {"node_trace": state["node_trace"] + ["validate_evidence"]}
        if not any(call["tool_name"] == "compare_price_baseline" for call in state["tool_trace"]):
            window = policy(state)
            result = tools.compare_price_baseline(state["geography"], window.window_start, window.window_end)
            update["available_evidence"] = state["available_evidence"] + [result.evidence.model_dump()]
            update["tool_trace"] = state["tool_trace"] + [{
                "step": len(state["tool_trace"]) + 1, "tool_name": result.tool_name,
                "parameters": result.evidence.parameters, "status": result.status,
                "evidence_id": result.evidence.evidence_id, "chosen_by": "code (required)"}]
        return update

    def write_report(state: AgentState) -> AgentState:
        evidence = "\n".join(f"[{e['evidence_id']}] {e['tool_name']} ({e['status']}): {compact(e['result'])}"
                             for e in state["available_evidence"])
        system = ("You write short, factual findings about an electricity price anomaly using ONLY the evidence "
                  "given. Every finding must cite the evidence IDs it uses, in square brackets as given. "
                  "Categories: 'observation' (what was measured), 'coinciding' (something that changed at the "
                  "same time), 'plausible' (a factor that may have contributed). Never claim a cause: do not use "
                  "words like 'caused', 'because', 'due to' or 'led to'. Do not invent numbers.")
        user = (f"Anomaly: {json.dumps(state['anomaly'], default=str)[:800]}\n\nEvidence:\n{evidence}\n\n"
                f"Missing evidence:\n" + "\n".join(f"- {item}" for item in state["missing_evidence"]))
        try:
            draft = llm.structured(system, user, DraftReport)
            findings = [finding.model_dump() for finding in draft.findings]
            note = "write_report"
        except LLMError as exc:
            findings, note = [], f"write_report:failed ({exc})"
        return {"draft_findings": findings, "node_trace": state["node_trace"] + [note]}

    def check_report(state: AgentState) -> AgentState:
        """Step 6 rules, applied by code: keep only findings backed by usable evidence."""
        usable = {e["evidence_id"] for e in state["available_evidence"] if e["status"] not in UNUSABLE}
        known = {e["evidence_id"] for e in state["available_evidence"]}
        kept: dict[str, list[Finding]] = {"observation": [], "coinciding": [], "plausible": []}
        rejected = []
        for item in state.get("draft_findings", []):
            ids = [ref.strip("[] ") for ref in item["evidence_ids"]]
            if not ids or not set(ids) <= known:
                rejected.append(f"no valid evidence reference: {item['statement'][:80]}")
            elif not set(ids) <= usable:
                rejected.append(f"cites missing/unavailable evidence: {item['statement'][:80]}")
            elif CAUSAL_WORDS.search(item["statement"]):
                rejected.append(f"unsupported causal claim: {item['statement'][:80]}")
            else:
                kept[item["category"]].append(Finding(statement=item["statement"], evidence_ids=ids))

        domains = (state.get("quality") or {}).get("domains", {})
        confidence = {name: domains.get(key, {"status": "unknown"}) for name, key in (
            ("price_data", "price"), ("generation_coverage", "generation"), ("load_coverage", "load"),
            ("flow_coverage", "cross_border_flows"), ("outage_coverage", "outages"), ("weather_context", "weather"))}
        if state["status"] == "blocked":
            status = "blocked"
        elif any(domains.get(name, {}).get("status") != "complete" for name in CORE_DOMAINS) \
                or not any(kept.values()):
            status = "limited"
        else:
            status = "complete"
        limitations = ["Findings were drafted by a local language model and checked by code; only statements "
                       "citing usable evidence are kept.",
                       "No cause is marked as proven: the available public data cannot establish causation."]
        if rejected:
            limitations.append(f"{len(rejected)} drafted statement(s) were removed: " + " | ".join(rejected))
        generator = f"langgraph-{llm.name}"
        evidence = [Evidence(**e) for e in state["available_evidence"]]
        report_id = "rpt_" + hashlib.sha256(json.dumps(
            {"anomaly": state["anomaly_id"], "generator": generator,
             "findings": {k: [f.statement for f in v] for k, v in kept.items()},
             "evidence": [e.evidence_id for e in evidence]}, sort_keys=True).encode()).hexdigest()[:16]
        report = InvestigationReport(
            report_id=report_id, anomaly_id=state["anomaly_id"], generator=generator, status=status,
            anomaly=state.get("anomaly", {}), window=state.get("time_window", {}), confidence_factors=confidence,
            observations=kept["observation"], coinciding_relationships=kept["coinciding"],
            plausible_contributing_factors=kept["plausible"], proven_causes=[],
            missing_evidence=state["missing_evidence"], limitations=limitations,
            rules={"max_tool_calls": float(max_tool_calls)},
            tool_trace=[ToolCall(**{k: v for k, v in call.items() if k != "chosen_by"}) for call in state["tool_trace"]],
            evidence=evidence)
        return {"report": report.model_dump(), "status": status,
                "node_trace": state["node_trace"] + ["check_report"]}

    # --- routing functions: they read the state and return the next node's name
    def after_quality(state: AgentState) -> str:
        return "check_report" if state["status"] == "blocked" else "choose_tool"

    def after_choice(state: AgentState) -> str:
        return "validate_evidence" if state["next_tool"] == "finish" else "run_tool"

    def after_tool(state: AgentState) -> str:
        calls = len(state["tool_trace"]) - 1
        if calls >= max_tool_calls or not remaining_tools(state) or state["node_trace"][-1] == "run_tool:blocked":
            return "validate_evidence"
        return "choose_tool"

    graph = StateGraph(AgentState)
    for name, node in (("load_event", load_event), ("quality_gate", quality_gate), ("choose_tool", choose_tool),
                       ("run_tool", run_tool), ("validate_evidence", validate_evidence),
                       ("write_report", write_report), ("check_report", check_report)):
        graph.add_node(name, node)
    graph.add_edge(START, "load_event")
    graph.add_edge("load_event", "quality_gate")
    graph.add_conditional_edges("quality_gate", after_quality, ["choose_tool", "check_report"])
    graph.add_conditional_edges("choose_tool", after_choice, ["run_tool", "validate_evidence"])
    graph.add_conditional_edges("run_tool", after_tool, ["choose_tool", "validate_evidence"])
    graph.add_edge("validate_evidence", "write_report")
    graph.add_edge("write_report", "check_report")
    graph.add_edge("check_report", END)
    return graph.compile()


# ------------------------------------------------------- step 7: run and save
def run_investigation(anomaly_id: str, tools: InvestigationTools, store: CaseStore, llm: LLM,
                      out_dir: Path, max_tool_calls: int = 6) -> AgentState:
    app = build_graph(tools, store, llm, max_tool_calls)
    final = app.invoke({"anomaly_id": anomaly_id}, {"recursion_limit": 50})
    report = InvestigationReport(**final["report"])
    store.save_report(report)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{report.report_id}.md").write_text(render_markdown(report), encoding="utf-8")
    trace = {"report_id": report.report_id, "model": llm.name, "node_trace": final["node_trace"],
             "tool_trace": final["tool_trace"], "draft_findings": final.get("draft_findings", []),
             "missing_evidence": final["missing_evidence"], "status": final["status"]}
    (out_dir / f"{report.report_id}_trace.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")
    return final


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run the Phase 9 LangGraph investigation agent")
    parser.add_argument("--anomaly-id", required=True)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--max-tool-calls", type=int, default=6)
    parser.add_argument("--database", type=Path, default=Path("data/eurogrid.duckdb"))
    parser.add_argument("--store", type=Path, default=Path("data/metadata/investigations.sqlite"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/investigations"))
    args = parser.parse_args(argv)
    final = run_investigation(args.anomaly_id, InvestigationTools(args.database), CaseStore(args.store),
                              OllamaLLM(args.model), args.out_dir, args.max_tool_calls)
    print("Path through the graph:")
    for step in final["node_trace"]:
        print(f"  → {step}")
    print()
    print(render_markdown(final["report"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

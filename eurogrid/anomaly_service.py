"""Phase 8 service for anomalies, investigations, evidence and approved tools.

Route handlers call this class; it owns all database and calculation work and
translates lower-level errors into :mod:`eurogrid.service_errors`.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import duckdb

from eurogrid.anomaly import DetectorConfig
from eurogrid.investigation import (CaseStore, InvestigationReport, detect_price_anomalies, investigate,
                                   render_markdown)
from eurogrid.investigation_tools import APPROVED_TOOLS, InvestigationTools, ToolError, ToolResult
from eurogrid.llm import LLM, OllamaLLM, ollama_status
from eurogrid.service_errors import InvalidRequestError, NotFoundError, UnavailableError

MAX_PAGE_SIZE = 200
DEFAULT_MODEL = "qwen3.5:4b"


def default_llm_factory(model: str) -> LLM:
    """Real local model; fails with UnavailableError before the graph runs if Ollama is not ready."""
    ok, detail = ollama_status(model)
    if not ok:
        raise UnavailableError(detail)
    return OllamaLLM(model)


class AnomalyService:
    def __init__(self, database_path: Path, store_path: Path, report_dir: Path | None = None,
                 llm_factory: Callable[[str], LLM] | None = None) -> None:
        self.tools = InvestigationTools(database_path)
        self.store = CaseStore(store_path)
        self.report_dir = report_dir or store_path.parent.parent / "investigations"
        self.llm_factory = llm_factory or default_llm_factory

    def _guard(self, call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except ToolError as exc:
            message = str(exc)
            if message.startswith("Database file not found"):
                raise UnavailableError(message) from exc
            raise InvalidRequestError(message) from exc
        except duckdb.Error as exc:
            raise UnavailableError(f"Gold models are missing or failed to query; run dbt first. Details: {exc}") from exc

    def zones(self) -> list[dict[str, Any]]:
        return sorted(self._guard(self.tools.zones).values(), key=lambda zone: zone["bidding_zone"])

    def detect(self, zone: str, start: date, end: date, config: DetectorConfig) -> list[dict[str, Any]]:
        events = self._guard(detect_price_anomalies, self.tools, zone, start, end, config)
        self.store.save_events(events)
        return [event.to_dict() for event in events]

    def list(self, zone: str | None, start: date | None, end: date | None, direction: str | None,
             limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise InvalidRequestError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        return self.store.list_events(zone, start.isoformat() if start else None,
                                      end.isoformat() if end else None, direction, limit, offset)

    def get(self, anomaly_id: str) -> dict[str, Any]:
        event = self.store.get_event(anomaly_id)
        if event is None:
            raise NotFoundError(f"Unknown anomaly {anomaly_id}; run detection first")
        return event

    def investigate(self, anomaly_id: str) -> InvestigationReport:
        report = self._guard(investigate, self.get(anomaly_id), self.tools)
        self.store.save_report(report)
        return report

    def investigate_with_agent(self, anomaly_id: str, model: str = DEFAULT_MODEL,
                               max_tool_calls: int = 6) -> dict[str, Any]:
        """Run the Phase 9 LangGraph agent with a local model; returns the checked report."""
        from eurogrid.agent import run_investigation  # langgraph is only needed for this path

        self.get(anomaly_id)
        llm = self.llm_factory(model)
        final = self._guard(run_investigation, anomaly_id, self.tools, self.store, llm, self.report_dir,
                            max_tool_calls)
        return final["report"]

    def reports(self, anomaly_id: str) -> list[dict[str, Any]]:
        self.get(anomaly_id)
        return self.store.list_reports(anomaly_id)

    def trace(self, report_id: str) -> dict[str, Any]:
        report = self.report(report_id)
        stored = self.store.get_trace(report_id)
        if stored is not None:
            return {"report_id": report_id, "generator": report["generator"], **stored}
        # Rule-based reports have no model decisions: the activity trace is the fixed tool sequence.
        return {"report_id": report_id, "generator": report["generator"], "model": None,
                "node_trace": ["load_event", "rule_based_tools", "rule_based_findings", "save_report"],
                "tool_trace": [{**call, "chosen_by": "code (fixed rule sequence)"} for call in report["tool_trace"]],
                "draft_findings": [], "missing_evidence": report["missing_evidence"], "status": report["status"]}

    def markdown(self, report_id: str) -> str:
        return render_markdown(self.report(report_id))

    def latest_report(self, anomaly_id: str, generator: str | None = None) -> dict[str, Any]:
        self.get(anomaly_id)
        report = self.store.latest_report(anomaly_id, generator)
        if report is None:
            raise NotFoundError(f"No investigation report for {anomaly_id} yet")
        return report

    def report(self, report_id: str) -> dict[str, Any]:
        report = self.store.get_report(report_id)
        if report is None:
            raise NotFoundError(f"Unknown report {report_id}")
        return report

    def evidence(self, evidence_id: str) -> dict[str, Any]:
        record = self.store.get_evidence(evidence_id)
        if record is None:
            raise NotFoundError(f"Unknown evidence {evidence_id}")
        return record

    def run_tool(self, tool_name: str, zone: str, start_utc: datetime, end_utc: datetime,
                 options: dict[str, Any] | None = None) -> ToolResult:
        """Run one approved tool by name and store its evidence."""
        if tool_name not in APPROVED_TOOLS:
            raise NotFoundError(f"Unknown tool {tool_name}; approved tools: {', '.join(APPROVED_TOOLS)}")
        options = options or {}
        allowed_options = {"get_weather_context": {"variables"}, "get_data_quality_status": {"required_domains"}}
        unexpected = set(options) - allowed_options.get(tool_name, set())
        if unexpected:
            raise InvalidRequestError(f"{tool_name} does not accept options {sorted(unexpected)}")
        arguments = [tuple(value) for value in options.values()]
        result = self._guard(getattr(self.tools, tool_name), zone, start_utc, end_utc, *arguments)
        self.store.save_evidence([result.evidence])
        return result

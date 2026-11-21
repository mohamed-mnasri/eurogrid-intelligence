"""Phase 9 agent tests with a fake model, so they run without Ollama."""

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from eurogrid.agent import DraftFinding, DraftReport, ToolChoice, run_investigation
from eurogrid.agent_tools import PolicyViolation, ToolCallRequest, ToolPolicy, execute_tool
from eurogrid.investigation import CaseStore, detect_price_anomalies
from eurogrid.investigation_tools import InvestigationTools
from eurogrid.llm import LLMError
from tests.sample_db import build_sample_database

UTC = timezone.utc


class FakeLLM:
    """Plays back scripted tool choices, then a scripted draft report."""

    name = "fake"

    def __init__(self, choices, findings=None, fail_report=False):
        self.choices = list(choices)
        self.findings = findings or []
        self.fail_report = fail_report

    def structured(self, system, user, schema):
        if schema is ToolChoice:
            name = self.choices.pop(0) if self.choices else "finish"
            if name == "INVALID":
                raise LLMError("bad json")
            return ToolChoice(next_tool=name, reason="test")
        if self.fail_report:
            raise LLMError("bad json")
        return DraftReport(findings=[DraftFinding(**item) for item in self.findings])


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.tools = InvestigationTools(build_sample_database(root / "sample.duckdb"))
        self.store = CaseStore(root / "cases.sqlite")
        event = detect_price_anomalies(self.tools, "DE-LU", date(2026, 3, 1), date(2026, 3, 12))[0]
        self.store.save_events([event])
        self.anomaly_id = event.anomaly_id
        self.out = root / "out"

    def tearDown(self):
        self.temp.cleanup()

    def run_agent(self, llm, max_tool_calls=6):
        return run_investigation(self.anomaly_id, self.tools, self.store, llm, self.out, max_tool_calls)


class AgentTests(AgentTestCase):
    def evidence_id(self, final, tool):
        return next(e["evidence_id"] for e in final["available_evidence"] if e["tool_name"] == tool)

    def test_model_chooses_tools_and_only_evidenced_findings_survive(self):
        dry = self.run_agent(FakeLLM(["get_generation_mix", "compare_demand", "finish"]))
        gen, demand = self.evidence_id(dry, "get_generation_mix"), self.evidence_id(dry, "compare_demand")
        findings = [
            {"category": "coinciding", "statement": "Wind generation was 90% below the prior 14 days.", "evidence_ids": [gen]},
            {"category": "coinciding", "statement": "Demand was 10% higher than usual.", "evidence_ids": [f"[{demand}]"]},
            {"category": "plausible", "statement": "Low wind caused the spike.", "evidence_ids": [gen]},
            {"category": "observation", "statement": "Prices were very high.", "evidence_ids": []},
            {"category": "observation", "statement": "Gas was expensive.", "evidence_ids": ["ev_madeup000000000"]},
        ]
        final = self.run_agent(FakeLLM(["get_generation_mix", "compare_demand", "finish"], findings))
        report = final["report"]
        self.assertEqual([c["tool_name"] for c in final["tool_trace"]],
                         ["get_data_quality_status", "get_generation_mix", "compare_demand", "compare_price_baseline"])
        self.assertEqual(final["tool_trace"][-1]["chosen_by"], "code (required)")
        self.assertEqual(len(report["coinciding_relationships"]), 2)
        self.assertEqual(report["plausible_contributing_factors"], [])     # causal claim removed
        self.assertEqual(report["observations"], [])                        # no / fake evidence removed
        self.assertIn("3 drafted statement(s) were removed", " ".join(report["limitations"]))
        evidence = {e["evidence_id"] for e in report["evidence"]}
        for finding in report["coinciding_relationships"]:
            self.assertTrue(set(finding["evidence_ids"]) <= evidence)
        self.assertEqual(report["status"], "complete")
        self.assertTrue((self.out / f"{report['report_id']}_trace.json").exists())
        self.assertEqual(self.store.latest_report(self.anomaly_id, "langgraph-fake")["report_id"], report["report_id"])

    def test_relationships_without_a_material_change_are_refiled_as_observations(self):
        dry = self.run_agent(FakeLLM(["compare_neighbour_prices", "finish"]))
        neighbours = self.evidence_id(dry, "compare_neighbour_prices")   # FR stays calm in the sample
        final = self.run_agent(FakeLLM(["compare_neighbour_prices", "finish"], [
            {"category": "coinciding", "statement": "France was priced near its usual level.", "evidence_ids": [neighbours]}]))
        report = final["report"]
        self.assertEqual(report["coinciding_relationships"], [])
        self.assertEqual([f["statement"] for f in report["observations"]], ["France was priced near its usual level."])
        self.assertIn("re-filed as observations", " ".join(report["limitations"]))

    def test_tool_budget_and_invalid_model_replies(self):
        final = self.run_agent(FakeLLM(["INVALID"] * 10), max_tool_calls=2)
        chosen = [c for c in final["tool_trace"] if c["tool_name"] != "get_data_quality_status"]
        fallback = [c for c in chosen if c["chosen_by"] == "code (model reply was invalid)"]
        self.assertEqual(len(fallback), 2)   # stopped at the budget, and the trace says code chose them
        self.assertIn("by code (model reply was invalid)", " ".join(final["node_trace"]))

    def test_outage_tool_is_recorded_as_missing_not_as_a_finding(self):
        dry = self.run_agent(FakeLLM(["get_reported_outages", "finish"]))
        outage = self.evidence_id(dry, "get_reported_outages")
        final = self.run_agent(FakeLLM(["get_reported_outages", "finish"], [
            {"category": "observation", "statement": "No outages were reported.", "evidence_ids": [outage]}]))
        self.assertEqual(final["report"]["observations"], [])
        self.assertTrue(any("get_reported_outages returned unavailable" in m for m in final["missing_evidence"]))
        self.assertEqual(final["report"]["status"], "limited")      # nothing usable was kept

    def test_report_failure_still_produces_a_limited_report(self):
        final = self.run_agent(FakeLLM(["finish"], fail_report=True))
        self.assertEqual(final["report"]["status"], "limited")
        self.assertTrue(any(step.startswith("write_report:failed") for step in final["node_trace"]))


class PolicyTests(AgentTestCase):
    def test_policy_blocks_other_zones_windows_and_budget(self):
        start, end = datetime(2026, 3, 5, 17, tzinfo=UTC), datetime(2026, 3, 5, 20, tzinfo=UTC)
        policy = ToolPolicy(allowed_zones=["DE-LU"], window_start=start, window_end=end, max_tool_calls=1)
        ok = ToolCallRequest(tool_name="compare_demand", zone="DE-LU", start_utc=start, end_utc=end)
        self.assertEqual(execute_tool(self.tools, ok, policy, 0).status, "complete")
        with self.assertRaises(PolicyViolation):
            execute_tool(self.tools, ok.model_copy(update={"zone": "FR"}), policy, 0)
        with self.assertRaises(PolicyViolation):
            execute_tool(self.tools, ok.model_copy(update={"end_utc": datetime(2026, 3, 9, tzinfo=UTC)}), policy, 0)
        with self.assertRaises(PolicyViolation):
            execute_tool(self.tools, ok, policy, 1)
        with self.assertRaises(ValueError):
            ToolCallRequest(tool_name="run_sql", zone="DE-LU", start_utc=start, end_utc=end)


class BlockedTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        import duckdb
        db = duckdb.connect(str(self.tools.database_path))
        db.execute("DELETE FROM gold_market_price WHERE bidding_zone='DE-LU' AND interval_start_utc >= TIMESTAMPTZ '2026-03-05 17:00:00+00' AND interval_start_utc < TIMESTAMPTZ '2026-03-05 20:00:00+00'")
        db.close()

    def test_missing_price_blocks_without_calling_the_model(self):
        final = self.run_agent(FakeLLM([]))
        self.assertEqual(final["report"]["status"], "blocked")
        self.assertEqual(final["node_trace"], ["load_event", "quality_gate", "check_report"])


if __name__ == "__main__":
    unittest.main()

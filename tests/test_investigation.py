"""Phase 6 tool, evidence and rule-based report tests on a synthetic Gold DuckDB."""

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from eurogrid.investigation import CaseStore, detect_price_anomalies, investigate, render_markdown
from eurogrid.investigation_tools import InvestigationTools, ToolError, percent_change
from tests.sample_db import build_sample_database

UTC = timezone.utc
START = datetime(2026, 3, 5, 17, tzinfo=UTC)
END = datetime(2026, 3, 5, 20, tzinfo=UTC)


class InvestigationTestCase(unittest.TestCase):
    missing_generation = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.tools = InvestigationTools(build_sample_database(root / "sample.duckdb", self.missing_generation))
        self.store = CaseStore(root / "cases.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def detect(self):
        return detect_price_anomalies(self.tools, "DE-LU", date(2026, 3, 1), date(2026, 3, 12))


class ToolTests(InvestigationTestCase):
    def test_tools_reject_unapproved_zones_and_unbounded_windows(self):
        with self.assertRaises(ToolError):
            self.tools.get_market_prices("XX", START, END)
        with self.assertRaises(ToolError):
            self.tools.get_market_prices("DE-LU", START, START + timedelta(days=32))
        with self.assertRaises(ToolError):
            self.tools.get_market_prices("DE-LU", END, START)
        with self.assertRaises(ToolError):
            self.tools.get_market_prices("DE-LU", START.replace(tzinfo=None), END)
        self.assertNotIn("run_sql", self.tools.registry())

    def test_price_tool_returns_units_completeness_and_sources(self):
        result = self.tools.get_market_prices("DE-LU", START, END)
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.unit, "EUR/MWh")
        self.assertEqual(result.summary["observed_hours"], 3)
        self.assertEqual(result.summary["mean"], 400.0)
        self.assertEqual(result.evidence.source_ids, ["raw-price-de"])
        self.assertEqual(result.evidence.models, ["gold_market_price"])

    def test_evidence_ids_are_repeatable(self):
        first = self.tools.get_generation_mix("DE-LU", START, END)
        second = self.tools.get_generation_mix("DE-LU", START, END)
        self.assertEqual(first.evidence.evidence_id, second.evidence.evidence_id)
        other = self.tools.get_generation_mix("FR", START, END)
        self.assertNotEqual(first.evidence.evidence_id, other.evidence.evidence_id)

    def test_generation_and_demand_compare_with_prior_comparable_hours(self):
        generation = self.tools.get_generation_mix("DE-LU", START, END).summary
        wind = generation["groups"]["wind"]
        self.assertEqual(wind["observed_mean"], 2000.0)
        self.assertEqual(wind["baseline_mean"], 20000.0)
        self.assertEqual(wind["change_pct"], -90.0)
        demand = self.tools.compare_demand("DE-LU", START, END).summary
        self.assertEqual(demand["change_pct"], 10.0)
        self.assertEqual(demand["forecast"], "unavailable")

    def test_outages_fail_closed_as_unavailable(self):
        result = self.tools.get_reported_outages("DE-LU", START, END)
        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.summary["records"])

    def test_neighbour_prices_show_the_event_is_local(self):
        result = self.tools.compare_neighbour_prices("DE-LU", START, END)
        self.assertEqual(list(result.summary["neighbours"]), ["FR"])
        self.assertLess(abs(result.summary["neighbours"]["FR"]["change_pct"]), 5)

    def test_percent_change_is_withheld_when_it_would_mislead(self):
        self.assertEqual(percent_change(110.0, 100.0), 10.0)
        self.assertEqual(percent_change(-90.0, -100.0), 10.0)       # less negative = up
        self.assertIsNone(percent_change(1500.0, 1.0))               # baseline near zero
        self.assertIsNone(percent_change(800.0, -200.0))             # net flow changed direction
        self.assertIsNone(percent_change(50.0, 0.0))

    def test_weather_rejects_unknown_variables(self):
        with self.assertRaises(ToolError):
            self.tools.get_weather_context("DE-LU", START, END, ("humidity",))


class DetectionAndReportTests(InvestigationTestCase):
    def test_injected_spike_is_detected_once_and_repeatably(self):
        events = self.detect()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event.direction, event.hour_count), ("spike", 3))
        self.assertEqual(event.start_utc, START.isoformat())
        self.assertEqual(event.source_ids, ["raw-price-de"])
        self.assertEqual(self.detect()[0].to_dict(), event.to_dict())

    def test_report_separates_findings_and_references_evidence(self):
        event = self.detect()[0]
        self.store.save_events([event])
        report = investigate(self.store.get_event(event.anomaly_id), self.tools)
        self.assertEqual(report.status, "complete")
        evidence_ids = {item.evidence_id for item in report.evidence}
        for finding in (report.observations + report.coinciding_relationships
                        + report.plausible_contributing_factors):
            self.assertTrue(finding.evidence_ids)
            self.assertTrue(set(finding.evidence_ids) <= evidence_ids)
        coinciding = " ".join(item.statement for item in report.coinciding_relationships)
        self.assertIn("Wind and solar generation", coinciding)
        self.assertIn("Public-grid load", coinciding)
        self.assertTrue(any("mainly local" in item.statement for item in report.observations))
        self.assertEqual(report.proven_causes, [])
        self.assertTrue(any("Outage evidence could not be checked" in item for item in report.missing_evidence))
        self.assertEqual(report.confidence_factors["outage_coverage"]["status"], "unavailable")
        self.assertEqual([call.step for call in report.tool_trace], list(range(1, len(report.tool_trace) + 1)))

    def test_report_is_repeatable_and_stored_with_evidence(self):
        event = self.detect()[0]
        self.store.save_events([event])
        first = investigate(event.to_dict(), self.tools)
        second = investigate(event.to_dict(), self.tools)
        self.assertEqual(first.report_id, second.report_id)
        self.assertEqual(first.model_dump(), second.model_dump())
        self.store.save_report(first)
        self.assertEqual(self.store.latest_report(event.anomaly_id)["report_id"], first.report_id)
        self.assertEqual(self.store.get_evidence(first.evidence[0].evidence_id)["tool_name"], "get_data_quality_status")
        self.assertIn("## Proven causes", render_markdown(first))

    def test_store_lists_with_filters_and_pagination(self):
        events = self.detect()
        self.store.save_events(events)
        self.store.save_events(events)  # idempotent
        rows, total = self.store.list_events(geography="DE-LU", limit=10)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["anomaly_id"], events[0].anomaly_id)
        self.assertEqual(self.store.list_events(geography="FR")[1], 0)


class MissingGenerationTests(InvestigationTestCase):
    missing_generation = True

    def test_missing_generation_limits_the_report_instead_of_inventing_a_finding(self):
        event = self.detect()[0]
        report = investigate(event.to_dict(), self.tools)
        self.assertEqual(report.status, "limited")
        self.assertEqual(report.confidence_factors["generation_coverage"]["status"], "missing")
        self.assertTrue(any("Wind/solar generation could not be compared" in item for item in report.missing_evidence))
        self.assertFalse(any("Wind and solar" in item.statement for item in report.coinciding_relationships))


if __name__ == "__main__":
    unittest.main()

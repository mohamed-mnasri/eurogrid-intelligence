"""Phase 10 endpoints behind the React pages: analytics, agent investigations, operations, Scenario Lab."""

import json
import sqlite3
import tempfile
import unittest
import warnings
from contextlib import closing
from pathlib import Path

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from eurogrid.api import create_app
from eurogrid.evaluation import CASES, run_suite, score_case
from eurogrid.ingestion import IngestionStore
from eurogrid.simulation_models import default_systems
from eurogrid.service_errors import UnavailableError
from tests.sample_db import build_sample_database
from tests.test_agent import FakeLLM
from tests.test_api_service import FakePVProvider


def write_dbt_artifacts(target: Path) -> None:
    target.mkdir(parents=True)
    manifest = {
        "sources": {"source.p.eurogrid.fct_market_price": {"name": "fct_market_price", "depends_on": {}}},
        "nodes": {
            "model.p.stg_market_price": {"name": "stg_market_price", "config": {"materialized": "view"},
                                         "depends_on": {"nodes": ["source.p.eurogrid.fct_market_price"]}},
            "model.p.gold_market_price": {"name": "gold_market_price", "config": {"materialized": "view"},
                                          "depends_on": {"nodes": ["model.p.stg_market_price"]}},
            "test.p.unique_gold_market_price_fact_key.abc": {"name": "unique_gold_market_price_fact_key",
                                                             "attached_node": "model.p.gold_market_price",
                                                             "depends_on": {"nodes": ["model.p.gold_market_price"]}},
        },
        "child_map": {"source.p.eurogrid.fct_market_price": ["model.p.stg_market_price"],
                      "model.p.stg_market_price": ["model.p.gold_market_price"],
                      "model.p.gold_market_price": ["test.p.unique_gold_market_price_fact_key.abc"]},
    }
    results = {"metadata": {"generated_at": "2026-11-17T12:00:00Z"}, "args": {"which": "test"},
               "results": [{"unique_id": "test.p.unique_gold_market_price_fact_key.abc", "status": "fail",
                            "failures": 3, "message": "Got 3 results", "execution_time": 0.1}]}
    (target / "manifest.json").write_text(json.dumps(manifest))
    (target / "run_results.json").write_text(json.dumps(results))


class Phase10ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        database = build_sample_database(root / "sample.duckdb")
        write_dbt_artifacts(root / "dbt" / "target")
        store = IngestionStore(root)
        with closing(sqlite3.connect(store.db_path)) as db, db:
            db.execute("""INSERT INTO ingestion_run (provider, dataset, endpoint, request_parameters_json, request_sha256,
                started_at, finished_at, status, rows_received, error_details) VALUES
                ('energy-charts','day_ahead_price','/v2/price','{"bzn":"DE-LU","start":"2026-02-01"}','a',
                 '2026-03-01T00:00:00+00:00','2026-03-01T00:00:05+00:00','success',960,NULL),
                ('energy-charts','day_ahead_price','/v2/price','{"bzn":"FR","start":"2026-02-01"}','b',
                 '2026-03-02T00:00:00+00:00','2026-03-02T00:00:05+00:00','failed',0,'HTTP 429')""")
        cls.llm = FakeLLM(["get_generation_mix", "finish"])
        cls.client = TestClient(create_app(database, data_dir=root, pv_provider_factory=lambda source: FakePVProvider(),
                                           llm_factory=lambda model: cls.llm, dbt_dir=root / "dbt"))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def anomaly_id(self):
        events = self.client.post("/api/anomalies/detect", json={
            "bidding_zone": "DE-LU", "start_date": "2026-03-01", "end_date": "2026-03-12"}).json()["events"]
        return events[0]["anomaly_id"]


class AnalyticsTests(Phase10ApiTestCase):
    def test_price_comparison_and_profile(self):
        comparison = self.client.get("/api/analytics/price-comparison", params={"year": 2026}).json()
        zones = {market["bidding_zone"]: market for market in comparison["markets"]}
        self.assertEqual(set(zones), {"DE-LU", "FR"})
        march = next(month for month in zones["DE-LU"]["months"] if month["month"] == "2026-03")
        self.assertEqual(march["max_eur_per_mwh"], 400.0)
        profile = self.client.get("/api/analytics/price-profile", params={"bidding_zone": "FR", "year": 2026}).json()
        self.assertEqual(len(profile["cells"]), 2 * 24)          # Feb + Mar, 24 local hours
        self.assertEqual(self.client.get("/api/analytics/price-profile",
                                         params={"bidding_zone": "XX", "year": 2026}).status_code, 404)


class InvestigationPageTests(Phase10ApiTestCase):
    def test_rule_based_and_agent_reports_are_listed_traced_and_exported(self):
        anomaly = self.anomaly_id()
        rule = self.client.post("/api/investigations", json={"anomaly_id": anomaly}).json()
        agent = self.client.post("/api/investigations", json={"anomaly_id": anomaly, "generator": "agent"})
        self.assertEqual(agent.status_code, 200, agent.text)
        agent = agent.json()
        self.assertEqual(agent["generator"], "langgraph-fake")
        listed = self.client.get(f"/api/anomalies/{anomaly}/investigations").json()
        self.assertEqual({item["generator"] for item in listed}, {"rule-based-v1", "langgraph-fake"})
        trace = self.client.get(f"/api/investigations/{agent['report_id']}/trace").json()
        self.assertIn("choose_tool:get_generation_mix by model", trace["node_trace"])
        rule_trace = self.client.get(f"/api/investigations/{rule['report_id']}/trace").json()
        self.assertTrue(all(call["chosen_by"].startswith("code") for call in rule_trace["tool_trace"]))
        markdown = self.client.get(f"/api/investigations/{rule['report_id']}/export", params={"format": "markdown"})
        self.assertTrue(markdown.text.startswith(f"# Investigation {rule['report_id']}"))
        self.assertIn("attachment", markdown.headers["content-disposition"])
        exported = self.client.get(f"/api/investigations/{rule['report_id']}/export", params={"format": "json"}).json()
        self.assertEqual(exported["report_id"], rule["report_id"])

    def test_agent_returns_503_when_ollama_is_not_ready(self):
        anomaly = self.anomaly_id()

        def unavailable(model):
            raise UnavailableError("Ollama is not reachable")
        root = Path(self.temp.name)
        client = TestClient(create_app(root / "sample.duckdb", data_dir=root, llm_factory=unavailable))
        response = client.post("/api/investigations", json={"anomaly_id": anomaly, "generator": "agent"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("Ollama", response.json()["detail"])


class SimulationMonthlyTests(Phase10ApiTestCase):
    def test_monthly_balance_sums_to_annual_totals(self):
        scenario = {"scenario_id": "monthly", "annual_consumption_kwh": 3500, "simulation_year": 2023,
                    "location": {"latitude": 52.52, "longitude": 13.405, "timezone": "Europe/Berlin"},
                    "tariff": {"kind": "fixed", "import_eur_per_kwh": 0.36, "export_eur_per_kwh": 0.08},
                    "systems": [default_systems()[1].model_dump()]}
        created = self.client.post("/api/simulations", json=scenario).json()
        system = created["result"]["systems"][0]
        monthly = self.client.get(f"/api/simulations/{created['simulation_id']}/monthly",
                                  params={"system_id": system["system_id"]}).json()
        self.assertEqual(len(monthly["items"]), 12)
        self.assertAlmostEqual(sum(item["grid_import_kwh"] for item in monthly["items"]),
                               system["energy"]["grid_import_kwh"], places=1)
        cost = sum(item["import_cost_eur"] - item["export_revenue_eur"] for item in monthly["items"])
        self.assertAlmostEqual(cost, system["financial"]["bill_with_system_eur"], places=0)


class OperationsTests(Phase10ApiTestCase):
    def test_ingestion_dbt_freshness_and_sources(self):
        ingestion = self.client.get("/api/operations/ingestion").json()
        connector = ingestion["connectors"][0]
        self.assertEqual((connector["runs"], connector["failures"], connector["health"]), (2, 1, "failing"))
        self.assertEqual(connector["last_error"], "HTTP 429")
        self.assertEqual(ingestion["recent_runs"][0]["geography"], "FR")
        dbt = self.client.get("/api/operations/dbt").json()
        self.assertEqual(dbt["counts"], {"fail": 1})
        self.assertEqual(dbt["results"][0]["model"], "gold_market_price")
        source = next(item for item in dbt["lineage"] if item["name"] == "fct_market_price")
        self.assertEqual(source["downstream"], ["gold_market_price", "stg_market_price"])
        freshness = self.client.get("/api/operations/freshness").json()
        price = next(item for item in freshness["items"] if item["domain"] == "price" and item["geography"] == "de")
        self.assertEqual((price["missing_hours"], price["status"]), (0, "stale"))   # synthetic data ends March 2026
        self.assertTrue(any(item["provider_id"] == "entsoe" for item in self.client.get("/api/operations/sources").json()))


class EvaluationTests(Phase10ApiTestCase):
    def test_rule_based_suite_runs_and_is_stored(self):
        run = self.client.post("/api/evaluations/run").json()
        self.assertEqual(run["cases_total"], len(CASES))
        self.assertEqual(run["cases_passed"], len(CASES))
        latest = self.client.get("/api/evaluations").json()
        self.assertEqual([item["generator"] for item in latest], ["rule-based-v1"])

    def test_scoring_fails_causal_claims_and_missing_evidence(self):
        payload = run_suite("rule-based", cases=CASES[:1])
        case = payload["cases"][0]
        self.assertTrue(case["passed"])
        event = {"anomaly_id": "anm_x", "geography": "DE-LU", "start_utc": "2026-03-05T17:00:00+00:00",
                 "end_utc": "2026-03-05T20:00:00+00:00", "max_abs_score": 9.0}
        bad = {"report_id": "rpt_x", "status": "complete", "evidence": [], "tool_trace": [], "proven_causes": [],
               "missing_evidence": [], "observations": [],
               "coinciding_relationships": [{"statement": "Low wind caused the spike.", "evidence_ids": []}],
               "plausible_contributing_factors": []}
        scored = score_case(CASES[0], event, bad, None, "test")
        self.assertFalse(scored.checks["unsupported_claims"])
        self.assertFalse(scored.checks["evidence_accuracy"])
        self.assertFalse(scored.checks["expected_signals"])
        self.assertFalse(scored.passed)


if __name__ == "__main__":
    unittest.main()

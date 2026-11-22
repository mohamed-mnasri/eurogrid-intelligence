"""Phase 8 service-level API tests over HTTP, using the synthetic sample DuckDB."""

import tempfile
import unittest
import warnings
from pathlib import Path

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from eurogrid.api import create_app
from eurogrid.simulation_models import default_systems
from tests.sample_db import build_sample_database
from tests.test_simulation import synthetic_pv_per_kwp


class FakePVProvider:
    """Offline PV provider so simulation tests never call PVGIS/Open-Meteo."""

    metadata = {"source": "synthetic test PV"}

    def per_kwp(self, location, system, year):
        return synthetic_pv_per_kwp(year)


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        database = build_sample_database(root / "sample.duckdb")
        cls.client = TestClient(create_app(database, data_dir=root, pv_provider_factory=lambda source: FakePVProvider()))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def detect(self):
        response = self.client.post("/api/anomalies/detect", json={
            "bidding_zone": "DE-LU", "start_date": "2026-03-01", "end_date": "2026-03-12"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class MarketApiTests(ApiTestCase):
    def test_openapi_documents_every_service_area(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        for path in ("/api/coverage", "/api/market-series", "/api/anomalies", "/api/anomalies/detect",
                     "/api/investigations", "/api/evidence/{evidence_id}", "/api/tools/{tool_name}",
                     "/api/simulations", "/api/simulations/{simulation_id}/hourly"):
            self.assertIn(path, paths)
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")

    def test_market_series_is_paginated_and_bounded(self):
        params = {"bidding_zone": "DE-LU", "measure": "price", "start_date": "2026-03-01",
                  "end_date": "2026-03-02", "limit": 10}
        first = self.client.get("/api/market-series", params=params).json()
        self.assertEqual((first["total"], len(first["items"]), first["next_offset"]), (48, 10, 10))
        self.assertEqual(first["unit"], "EUR/MWh")
        last = self.client.get("/api/market-series", params={**params, "offset": 40}).json()
        self.assertEqual((len(last["items"]), last["next_offset"]), (8, None))
        self.assertTrue(first["source_trace"])

    def test_market_series_errors_are_clear(self):
        base = {"bidding_zone": "DE-LU", "measure": "price", "start_date": "2026-02-01"}
        too_long = self.client.get("/api/market-series", params={**base, "end_date": "2026-03-12"})
        self.assertEqual(too_long.status_code, 422)
        self.assertIn("31 days", too_long.json()["detail"])
        self.assertEqual(self.client.get("/api/market-series", params={**base, "measure": "sql",
                                                                       "end_date": "2026-02-02"}).status_code, 422)
        unknown = self.client.get("/api/market-series", params={**base, "bidding_zone": "XX", "end_date": "2026-02-02"})
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(self.client.get("/api/market-series", params={**base, "end_date": "2026-02-02",
                                                                       "limit": 99999}).status_code, 422)


class AnomalyApiTests(ApiTestCase):
    def test_detect_list_and_get(self):
        detected = self.detect()
        self.assertEqual(detected["detected"], 1)
        anomaly_id = detected["events"][0]["anomaly_id"]
        self.assertEqual(self.detect()["events"][0]["anomaly_id"], anomaly_id)   # repeatable
        page = self.client.get("/api/anomalies", params={"bidding_zone": "DE-LU", "limit": 1}).json()
        self.assertEqual((page["total"], page["items"][0]["anomaly_id"]), (1, anomaly_id))
        self.assertEqual(self.client.get(f"/api/anomalies/{anomaly_id}").json()["direction"], "spike")
        self.assertEqual(self.client.get("/api/anomalies", params={"direction": "dip"}).json()["total"], 0)

    def test_anomaly_errors(self):
        self.assertEqual(self.client.get("/api/anomalies/anm_0000000000000000").status_code, 404)
        self.assertEqual(self.client.get("/api/anomalies/not-an-id").status_code, 422)
        long_range = self.client.post("/api/anomalies/detect", json={
            "bidding_zone": "DE-LU", "start_date": "2025-01-01", "end_date": "2026-03-01"})
        self.assertEqual(long_range.status_code, 422)
        unknown = self.client.post("/api/anomalies/detect", json={
            "bidding_zone": "XX", "start_date": "2026-03-01", "end_date": "2026-03-02"})
        self.assertEqual(unknown.status_code, 422)
        self.assertIn("approved geography", unknown.json()["detail"])
        self.assertEqual(self.client.get("/api/anomalies", params={"limit": 1000}).status_code, 422)

    def test_investigation_and_evidence_round_trip(self):
        anomaly_id = self.detect()["events"][0]["anomaly_id"]
        report = self.client.post("/api/investigations", json={"anomaly_id": anomaly_id})
        self.assertEqual(report.status_code, 200, report.text)
        body = report.json()
        self.assertEqual(body["status"], "complete")
        self.assertEqual(self.client.get(f"/api/investigations/{body['report_id']}").json(), body)
        self.assertEqual(self.client.get(f"/api/anomalies/{anomaly_id}/investigation").json()["report_id"],
                         body["report_id"])
        evidence_id = body["coinciding_relationships"][0]["evidence_ids"][0]
        evidence = self.client.get(f"/api/evidence/{evidence_id}").json()
        self.assertEqual(evidence["evidence_id"], evidence_id)
        self.assertIn("tool_name", evidence)
        self.assertEqual(self.client.post("/api/investigations", json={"anomaly_id": "anm_ffffffffffffffff"}).status_code, 404)
        self.assertEqual(self.client.get("/api/evidence/ev_0000000000000000").status_code, 404)


class ToolApiTests(ApiTestCase):
    window = {"bidding_zone": "DE-LU", "start_utc": "2026-03-05T17:00:00Z", "end_utc": "2026-03-05T20:00:00Z"}

    def test_tool_catalogue_and_call(self):
        names = [tool["name"] for tool in self.client.get("/api/tools").json()]
        self.assertIn("compare_demand", names)
        result = self.client.post("/api/tools/compare_demand", json=self.window).json()
        self.assertEqual(result["summary"]["change_pct"], 10.0)
        stored = self.client.get(f"/api/evidence/{result['evidence']['evidence_id']}")
        self.assertEqual(stored.status_code, 200)
        weather = self.client.post("/api/tools/get_weather_context", json={**self.window, "variables": ["wind_speed"]})
        self.assertEqual(list(weather.json()["summary"]["variables"]), ["wind_speed"])

    def test_tool_guards(self):
        self.assertEqual(self.client.post("/api/tools/run_sql", json=self.window).status_code, 404)
        self.assertEqual(self.client.post("/api/tools/compare_demand", json={
            **self.window, "end_utc": "2026-04-30T00:00:00Z"}).status_code, 422)
        self.assertEqual(self.client.post("/api/tools/compare_demand", json={
            **self.window, "start_utc": "2026-03-05T17:00:00"}).status_code, 422)
        self.assertEqual(self.client.post("/api/tools/compare_demand", json={
            **self.window, "variables": ["wind_speed"]}).status_code, 422)
        self.assertEqual(self.client.post("/api/tools/compare_demand", json={
            **self.window, "bidding_zone": "XX"}).status_code, 422)


class SimulationApiTests(ApiTestCase):
    scenario = {"scenario_id": "api-test", "location": {"latitude": 52.52, "longitude": 13.405},
                "annual_consumption_kwh": 3500, "simulation_year": 2023,
                "tariff": {"kind": "fixed", "import_eur_per_kwh": 0.36, "export_eur_per_kwh": 0.08},
                "systems": [system.model_dump() for system in default_systems()]}

    def test_simulation_create_get_and_hourly_pages(self):
        created = self.client.post("/api/simulations", json=self.scenario)
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        simulation_id = body["simulation_id"]
        self.assertEqual(len(body["result"]["systems"]), 3)
        self.assertEqual(self.client.post("/api/simulations", json=self.scenario).json()["simulation_id"], simulation_id)
        record = self.client.get(f"/api/simulations/{simulation_id}").json()
        self.assertEqual(record["scenario"]["scenario_id"], "api-test")
        page = self.client.get(f"/api/simulations/{simulation_id}/hourly",
                               params={"system_id": "pv-4kw-bat-5kwh", "offset": 8736, "limit": 744}).json()
        self.assertEqual((page["total"], len(page["items"]), page["next_offset"]), (8760, 24, None))
        self.assertIn("soc_end_kwh", page["items"][0])
        self.assertGreaterEqual(self.client.get("/api/simulations").json()["total"], 1)
        self.assertEqual(len(self.client.get("/api/simulations/default-systems").json()), 3)

    def test_simulation_errors(self):
        created = self.client.post("/api/simulations", json=self.scenario).json()
        hourly = f"/api/simulations/{created['simulation_id']}/hourly"
        self.assertEqual(self.client.get(hourly, params={"system_id": "pv-4kw", "limit": 745}).status_code, 422)
        self.assertEqual(self.client.get(hourly, params={"system_id": "nope"}).status_code, 404)
        self.assertEqual(self.client.get("/api/simulations/sim_0000000000000000").status_code, 404)
        invalid = self.client.post("/api/simulations", json={**self.scenario, "annual_consumption_kwh": 10})
        self.assertEqual(invalid.status_code, 422)
        # The sample DB only holds Feb–Mar 2026 prices, so a full-year dynamic tariff must refuse, not guess.
        dynamic = self.client.post("/api/simulations", json={
            **self.scenario, "simulation_year": 2026, "pv_source": "weather_model",
            "tariff": {"kind": "dynamic", "bidding_zone": "DE-LU"}})
        self.assertEqual(dynamic.status_code, 422)
        self.assertIn("wholesale prices are missing", dynamic.json()["detail"])


if __name__ == "__main__":
    unittest.main()

"""Phase 11: Grid-to-Home event replay and test-only chaos mode."""

import math
import tempfile
import unittest
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from eurogrid.api import create_app
from eurogrid.chaos import build_slice, dbt_executable, inject, run_chaos
from eurogrid.investigation import CaseStore, detect_price_anomalies
from eurogrid.investigation_tools import InvestigationTools
from eurogrid.replay import ReplayError, render_replay_markdown, run_replay
from eurogrid.simulation_models import FixedTariff, HouseholdScenario, Location, default_systems
from eurogrid.synthetic import build_synthetic_sources, delete_event_prices
from tests.sample_db import build_sample_database
from tests.test_api_service import FakePVProvider

UTC = timezone.utc


class FakeWindowPV:
    """Clear-sky-like PV per kWp: zero at night, a midday bump (UTC hours 6–18)."""

    metadata = {"source": "synthetic window PV", "raw_sha256": "test"}

    def per_kwp_window(self, location, system, start_utc, end_utc):
        hours = int((end_utc - start_utc) / timedelta(hours=1))
        result = {}
        for offset in range(hours):
            hour = start_utc + timedelta(hours=offset)
            result[hour] = max(0.0, math.sin(math.pi * (hour.hour - 6) / 12)) * 0.5 if 6 <= hour.hour <= 18 else 0.0
        return result


def scenario(tariff=None) -> HouseholdScenario:
    return HouseholdScenario(
        scenario_id="replay-test", location=Location(latitude=52.52, longitude=13.405, timezone="Europe/Berlin"),
        annual_consumption_kwh=3500, simulation_year=2023, pv_source="pvgis",
        tariff=tariff or FixedTariff(import_eur_per_kwh=0.36, export_eur_per_kwh=0.08), systems=default_systems())


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = build_sample_database(root / "sample.duckdb")
        self.tools = InvestigationTools(self.db)
        self.store = CaseStore(root / "cases.sqlite")
        event = detect_price_anomalies(self.tools, "DE-LU", date(2026, 3, 1), date(2026, 3, 12))[0]
        self.store.save_events([event])
        self.event = event.to_dict()

    def tearDown(self):
        self.temp.cleanup()

    def test_replay_compares_tariffs_systems_and_usual_prices(self):
        result = run_replay(self.event, scenario(), "sim_0000000000000000", self.tools, self.store, FakeWindowPV())
        self.assertEqual(result.window["hours"], 24)                       # 5 March, Berlin local day
        self.assertEqual(result.window["start_utc"], "2026-03-04T23:00:00+00:00")
        options = {option.option_id: option for option in result.options}
        self.assertEqual(list(options), ["no-system", "pv-4kw", "pv-4kw-bat-5kwh", "pv-8kw-bat-10kwh"])
        none, battery = options["no-system"], options["pv-8kw-bat-10kwh"]
        fixed, dynamic = none.costs[0], none.costs[1]
        self.assertEqual(fixed.event_premium_eur, 0.0)                      # a fixed tariff does not pass the spike on
        self.assertGreater(dynamic.event_premium_eur, 0.5)                  # 400 vs ~50 EUR/MWh for 3 evening hours
        self.assertEqual(none.grid_import_kwh, none.load_kwh)
        self.assertLess(battery.costs[1].event_premium_eur, dynamic.event_premium_eur)
        self.assertGreater(battery.event_hours_battery_discharge_kwh, 0)
        for option in result.options:                                       # energy closes over the window
            flows = result.hourly["flows"][option.option_id]
            self.assertAlmostEqual(sum(flows["load_kwh"]), option.load_kwh, places=2)
        self.assertEqual(sum(result.hourly["in_event"]), 3)
        self.assertEqual(result.market["generator"], "rule-based-v1")       # investigation created for the summary
        self.assertTrue(any("counterfactual" in item.lower() for item in result.assumptions))
        report = render_replay_markdown(result.model_dump())
        for heading in ("What the market did (measured)", "What it would have meant for this household",
                        "Assumptions and limitations"):
            self.assertIn(heading, report)

    def test_replay_is_repeatable_and_fails_closed_on_missing_prices(self):
        first = run_replay(self.event, scenario(), "sim_0000000000000000", self.tools, self.store, FakeWindowPV())
        again = run_replay(self.event, scenario(), "sim_0000000000000000", self.tools, self.store, FakeWindowPV())
        self.assertEqual(first.replay_id, again.replay_id)
        self.assertEqual(first.options, again.options)
        delete_event_prices(self.db)
        with self.assertRaises(ReplayError):
            run_replay(self.event, scenario(), "sim_0000000000000000", InvestigationTools(self.db), self.store, FakeWindowPV())


class ReplayApiTests(unittest.TestCase):
    def test_replay_endpoints(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            client = TestClient(create_app(build_sample_database(root / "sample.duckdb"), data_dir=root,
                                           pv_provider_factory=lambda source: FakePVProvider(),
                                           window_pv_factory=FakeWindowPV, dbt_runner=None))
            anomaly = client.post("/api/anomalies/detect", json={"bidding_zone": "DE-LU", "start_date": "2026-03-01",
                                                                 "end_date": "2026-03-12"}).json()["events"][0]["anomaly_id"]
            simulation = client.post("/api/simulations", json=scenario().model_dump(mode="json")).json()["simulation_id"]
            created = client.post("/api/replays", json={"anomaly_id": anomaly, "simulation_id": simulation})
            self.assertEqual(created.status_code, 200, created.text)
            replay_id = created.json()["replay_id"]
            self.assertEqual(client.get(f"/api/replays/{replay_id}").json()["simulation_id"], simulation)
            self.assertEqual(client.get("/api/replays").json()[0]["replay_id"], replay_id)
            markdown = client.get(f"/api/replays/{replay_id}/export").text
            self.assertTrue(markdown.startswith(f"# Event-to-household replay {replay_id}"))
            missing = client.post("/api/replays", json={"anomaly_id": anomaly, "simulation_id": "sim_ffffffffffffffff"})
            self.assertEqual(missing.status_code, 404)
            chaos = client.post("/api/chaos/runs", json={"fault": "missing_feed", "anomaly_id": "anm_ffffffffffffffff"})
            self.assertEqual(chaos.status_code, 404)


class ChaosTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.production = build_synthetic_sources(self.root / "production.duckdb")

    def tearDown(self):
        self.temp.cleanup()

    def counts(self, path):
        with duckdb.connect(str(path), read_only=True) as db:
            return {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in ("fct_market_price", "fct_generation", "fct_weather")}

    def test_faults_change_only_the_copy(self):
        start, end = datetime(2026, 3, 4, 23, tzinfo=UTC), datetime(2026, 3, 5, 23, tzinfo=UTC)
        before = self.counts(self.production)
        copy = self.root / "copy.duckdb"
        rows = build_slice(self.production, copy, start - timedelta(days=2), end)
        self.assertEqual(rows["fct_market_price"], 2 * 24 * 3)              # two zones, three days
        self.assertEqual(inject(copy, "missing_feed", "DE-LU", "de", start, end), 3 * 24)   # wind, solar, gas
        with duckdb.connect(str(copy), read_only=True) as db:
            left = db.execute("""SELECT count(*) FROM fct_generation WHERE geography_id='geo-de'
                AND production_type='load' AND interval_start_utc >= TIMESTAMPTZ '2026-03-04 23:00:00+00'""").fetchone()[0]
        self.assertEqual(left, 24)                                           # load is not part of the generation feed
        self.assertEqual(inject(copy, "malformed_feed", "DE-LU", "de", start, end), 48)
        self.assertEqual(self.counts(self.production), before)               # production untouched

    def test_chaos_without_dbt_reports_that_it_could_not_assess(self):
        event = self._event()
        result = run_chaos("missing_feed", event, self.production, self.root / "chaos", dbt_runner=None)
        self.assertIsNone(result["faulty"]["assessment"])
        self.assertEqual(result["faulty"]["dbt"]["reason"], "dbt disabled")
        self.assertEqual(result["faulty"]["rows_changed"], 72)

    @unittest.skipUnless(dbt_executable(), "dbt is not installed in this environment")
    def test_chaos_with_real_dbt_detects_faults_and_blocks_unsupported_findings(self):
        event = self._event()
        before = self.counts(self.production)
        missing = run_chaos("missing_feed", event, self.production, self.root / "chaos")
        self.assertTrue(missing["faulty"]["dbt"]["ran"], missing["faulty"]["dbt"].get("reason"))
        self.assertFalse(missing["detected_by_dbt"])                         # schema tests do not see a gap...
        self.assertEqual(missing["faulty"]["assessment"]["quality"]["generation"], "missing")   # ...the gate does
        self.assertEqual(missing["clean"]["assessment"]["quality"]["generation"], "complete")
        self.assertIn("gold_generation_mix", missing["downstream_models"])
        self.assertEqual(missing["report_checks"]["rule-based"], {"not_complete": True, "names_missing_evidence": True,
                                                                  "no_causal_claims": True, "no_proven_causes": True})
        malformed = run_chaos("malformed_feed", event, self.production, self.root / "chaos")
        self.assertTrue(malformed["detected_by_dbt"])
        self.assertIn("unique_gold_market_price_fact_key", malformed["failing_tests"])
        self.assertEqual(malformed["faulty"]["assessment"]["quality"]["price"], "partial")
        self.assertTrue(malformed["report_checks"]["rule-based"]["names_missing_evidence"])
        self.assertEqual(self.counts(self.production), before)

    def _event(self):
        # The same synthetic data as Gold tables, so detection finds the injected spike's event.
        from eurogrid.synthetic import build_synthetic_database
        tools = InvestigationTools(build_synthetic_database(self.root / "gold.duckdb"))
        return detect_price_anomalies(tools, "DE-LU", date(2026, 3, 1), date(2026, 3, 12))[0].to_dict()


if __name__ == "__main__":
    unittest.main()

"""Integration tests for the read-only market query service and HTTP routes."""

from datetime import date
import tempfile
import unittest
from pathlib import Path

import duckdb
from fastapi import HTTPException

from eurogrid.api import create_app
from eurogrid.market_service import MarketService


class MarketApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "market.duckdb"
        db = duckdb.connect(str(self.database))
        db.execute("""CREATE TABLE gold_market_coverage (
            country_code VARCHAR,bidding_zone VARCHAR,first_interval_utc VARCHAR,last_interval_utc VARCHAR,
            first_source_date VARCHAR,last_source_date VARCHAR,source_timezone VARCHAR,
            price_interval_count INTEGER,valid_price_interval_count INTEGER,
            flagged_price_interval_count INTEGER,provider VARCHAR,dataset VARCHAR)""")
        db.execute("INSERT INTO gold_market_coverage VALUES ('fr','FR','2025-01-01 00:00:00+00','2025-01-01 02:00:00+00','2025-01-01','2025-01-31','Europe/Paris',3,3,0,'energy-charts','day_ahead_price')")
        db.execute("INSERT INTO gold_market_coverage VALUES ('de','DE-LU','2025-01-01 00:00:00+00','2025-01-01 02:00:00+00','2025-01-01','2025-01-31','Europe/Berlin',3,3,0,'energy-charts','day_ahead_price')")
        db.execute("""CREATE TABLE gold_market_price (
            interval_start_utc TIMESTAMPTZ,interval_end_utc TIMESTAMPTZ,original_timestamp VARCHAR,
            interval_minutes INTEGER,price_eur_per_mwh DOUBLE,data_status VARCHAR,
            provider VARCHAR,dataset VARCHAR,raw_sha256 VARCHAR,bidding_zone VARCHAR,geography_id VARCHAR)""")
        db.execute("""CREATE TABLE gold_price_hourly_baseline (
            geography_id VARCHAR,utc_hour INTEGER,mean_price_eur_per_mwh DOUBLE,baseline_observation_count BIGINT)""")
        db.execute("INSERT INTO gold_price_hourly_baseline VALUES ('price-geo',0,42,4),('price-geo',1,43,4)")
        db.execute("""INSERT INTO gold_market_price VALUES
            ('2025-01-01 00:00:00+00','2025-01-01 01:00:00+00','2025-01-01T01:00:00+01:00',60,40,'valid','energy-charts','day_ahead_price','hash1','FR','price-geo'),
            ('2025-01-01 01:00:00+00','2025-01-01 02:00:00+00','2025-01-01T02:00:00+01:00',60,44,'valid','energy-charts','day_ahead_price','hash2','FR','price-geo'),
            ('2025-01-01 02:00:00+00','2025-01-01 03:00:00+00','2025-01-01T03:00:00+01:00',60,NULL,'missing','energy-charts','day_ahead_price','hash3','FR','price-geo')""")
        db.execute("""CREATE TABLE gold_generation_mix (
            interval_start_utc TIMESTAMPTZ,original_timestamp VARCHAR,source_timezone VARCHAR,production_type VARCHAR,
            generation_mw DOUBLE,data_status VARCHAR,provider VARCHAR,dataset VARCHAR,raw_sha256 VARCHAR,country_code VARCHAR,geography_code VARCHAR)""")
        db.execute("""INSERT INTO gold_generation_mix VALUES
            ('2025-01-01 00:00:00+00','2025-01-01T01:00:00+01:00','Europe/Paris','solar',100,'valid','energy-charts','public_power','hash4','fr','fr'),
            ('2025-01-01 00:00:00+00','2025-01-01T01:00:00+01:00','Europe/Berlin','wind_onshore',200,'valid','energy-charts','public_power','hash8','all','all'),
            ('2025-01-01 00:00:00+00','2025-01-01T01:00:00+01:00','Europe/Berlin','wind_onshore',80,'valid','energy-charts','public_power','hash9','eu','eu'),
            ('2025-01-01 00:00:00+00','2025-01-01T01:00:00+01:00','Europe/London','wind_onshore',90,'valid','energy-charts','public_power','hash10','uk','uk')""")
        db.execute("""CREATE TABLE gold_demand (
            interval_start_utc TIMESTAMPTZ,original_timestamp VARCHAR,demand_mw DOUBLE,
            data_status VARCHAR,provider VARCHAR,dataset VARCHAR,raw_sha256 VARCHAR,country_code VARCHAR,geography_code VARCHAR)""")
        db.execute("""INSERT INTO gold_demand VALUES
            ('2025-01-01 00:00:00+00','2025-01-01T01:00:00+01:00',500,'valid','energy-charts','public_power','hash5','fr','fr'),
            ('2025-01-01 00:00:00+00','2025-01-01T01:00:00+01:00',1000,'valid','energy-charts','public_power','hash11','all','all')""")
        db.execute("""CREATE TABLE gold_cross_border_flow (
            interval_start_utc TIMESTAMPTZ,original_timestamp VARCHAR,net_cross_border_trading_mw DOUBLE,
            data_status VARCHAR,provider VARCHAR,dataset VARCHAR,raw_sha256 VARCHAR,country_code VARCHAR)""")
        db.execute("INSERT INTO gold_cross_border_flow VALUES ('2025-01-01 00:00:00+00','2025-01-01T01:00:00+01:00',-20,'valid','energy-charts','public_power','hash6','fr')")
        db.execute("""CREATE TABLE gold_weather (
            interval_start_utc TIMESTAMPTZ,original_timestamp VARCHAR,variable VARCHAR,weather_value DOUBLE,
            weather_unit VARCHAR,data_status VARCHAR,provider VARCHAR,dataset VARCHAR,raw_sha256 VARCHAR,country_code VARCHAR)""")
        db.execute("""INSERT INTO gold_weather VALUES
            ('2024-12-31 23:00:00+00','2024-12-31T23:00:00Z','temperature',7,'°C','valid','open-meteo','historical_weather','hash0','fr'),
            ('2025-01-01 00:00:00+00','2025-01-01T00:00:00Z','temperature',8,'°C','valid','open-meteo','historical_weather','hash7','fr')""")
        db.close()
        self.app = create_app(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def route(self, path):
        """Get a registered endpoint so route behavior can be tested directly."""
        return next(route.endpoint for route in self.app.routes if getattr(route, "path", None) == path)

    def test_coverage_and_overview_return_gold_model_records_and_gaps(self):
        coverage = self.route("/api/coverage")()
        self.assertEqual(next(item for item in coverage["markets"] if item["market_name"] == "France")["bidding_zone"], "FR")
        payload = self.route("/api/overview")("FR", date(2025, 1, 1), date(2025, 1, 1))
        self.assertEqual(len(payload["prices"]), 3)
        self.assertEqual(len(payload["generation"]), 1)
        self.assertEqual(len(payload["demand"]), 1)
        self.assertEqual(len(payload["cross_border_flows"]), 1)
        # Include UTC weather from the prior date when it belongs to local midnight in France.
        self.assertEqual(len(payload["weather"]), 2)
        self.assertEqual({row["original_timestamp"] for row in payload["weather"]},
                         {"2024-12-31T23:00:00Z", "2025-01-01T00:00:00Z"})
        self.assertEqual(payload["quality"]["flagged_price_values"], 1)
        self.assertEqual(payload["quality"]["unobserved_hour_slots_estimate"], 21)
        self.assertIn("day-ahead price", payload["lineage"]["prices"])

    def test_coverage_includes_named_europe_aggregates_and_power_only_countries(self):
        markets = self.route("/api/coverage")()["markets"]
        by_name = {item["market_name"]: item for item in markets}
        self.assertEqual(markets[0]["market_name"], "All Europe (aggregate)")
        self.assertFalse(by_name["All Europe (aggregate)"]["has_prices"])
        self.assertEqual(by_name["United Kingdom"]["bidding_zone"], "UK")
        self.assertFalse(by_name["United Kingdom"]["has_prices"])
        self.assertNotIn("Italy", by_name)
        self.assertNotIn("European Union (aggregate)", by_name)

    def test_all_europe_price_is_labeled_average_of_available_country_zones(self):
        payload = self.route("/api/overview")("ALL", date(2025, 1, 1), date(2025, 1, 1))
        self.assertEqual(payload["market"]["price_method"], "market_average")
        self.assertEqual(len(payload["prices"]), 2)
        self.assertEqual(payload["prices"][0]["price_eur_per_mwh"], 40)
        self.assertEqual(payload["prices"][0]["market_count"], 1)
        self.assertIn("indicative comparison", payload["lineage"]["prices"])

    def test_api_rejects_unknown_zone_and_ranges_longer_than_31_days(self):
        with self.assertRaises(HTTPException) as unknown:
            self.route("/api/overview")("XX", date(2025, 1, 1), date(2025, 1, 1))
        self.assertEqual(unknown.exception.status_code, 404)
        with self.assertRaises(HTTPException) as too_long:
            self.route("/api/overview")("FR", date(2025, 1, 1), date(2025, 2, 1))
        self.assertEqual(too_long.exception.status_code, 422)

    def test_day_length_accounts_for_dst(self):
        self.assertEqual(MarketService._expected_intervals(date(2025, 3, 30), date(2025, 3, 30), "Europe/Paris"), 23)
        self.assertEqual(MarketService._expected_intervals(date(2025, 10, 26), date(2025, 10, 26), "Europe/Paris"), 25)


if __name__ == "__main__":
    unittest.main()

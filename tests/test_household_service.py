"""Tests for Eurostat household benchmark parsing and API behavior."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from eurogrid.api import create_app
from eurogrid.household_service import eurostat_series, household_benchmark, household_price_history


class EurostatSeriesTests(unittest.TestCase):
    def test_reads_json_stat_values_using_dimension_order(self):
        payload = {
            "id": ["geo", "unit", "time"],
            "size": [2, 1, 2],
            "dimension": {
                "geo": {"category": {"index": {"DE": 0, "FR": 1}}},
                "unit": {"category": {"index": {"GWH": 0}}},
                "time": {"category": {"index": {"2023": 0, "2024": 1}}},
            },
            "value": {"0": 10, "1": 11, "2": 20, "3": 21},
        }
        self.assertEqual(eurostat_series(payload, {"geo": "FR", "unit": "GWH"}, "time"),
                         {"2023": 20.0, "2024": 21.0})

    def test_skips_suppressed_values(self):
        payload = {
            "id": ["time"], "size": [2],
            "dimension": {"time": {"category": {"index": {"2023": 0, "2024": 1}}}},
            "value": {"0": 4.5},
        }
        self.assertEqual(eurostat_series(payload, {}, "time"), {"2023": 4.5})

    def test_household_price_benchmark_uses_latest_period_and_only_tariff_data(self):
        payload = {
            "id": ["freq", "siec", "nrg_cons", "unit", "tax", "currency", "geo", "time"],
            "size": [1, 1, 1, 1, 1, 1, 1, 2],
            "dimension": {
                "freq": {"category": {"index": {"S": 0}}},
                "siec": {"category": {"index": {"E7000": 0}}},
                "nrg_cons": {"category": {"index": {"KWH2500-4999": 0}}},
                "unit": {"category": {"index": {"KWH": 0}}},
                "tax": {"category": {"index": {"I_TAX": 0}}},
                "currency": {"category": {"index": {"EUR": 0}}},
                "geo": {"category": {"index": {"DE_TEST": 0}}},
                "time": {"category": {"index": {"2025-S2": 0, "2026-S1": 1}}},
            },
            "value": {"0": 0.34, "1": 0.36},
        }
        with patch("eurogrid.household_service.HttpJsonClient") as client_class, \
             patch("eurogrid.household_service.EurostatConnector") as connector_class:
            client_class.return_value.__enter__.return_value = object()
            connector_class.return_value.household_prices.return_value = SimpleNamespace(payload=payload)
            result = household_benchmark("DE_TEST")
        self.assertEqual(result["retail_period"], "2026-S1")
        self.assertEqual(result["retail_eur_per_kwh"], 0.36)
        connector_class.return_value.household_prices.assert_called_once()


    def test_price_history_returns_only_published_half_years_for_the_year(self):
        series = ({"period": "2025-S1", "retail_eur_per_kwh": 0.38},
                  {"period": "2025-S2", "retail_eur_per_kwh": 0.39},
                  {"period": "2026-S1", "retail_eur_per_kwh": 0.36})
        with patch("eurogrid.household_service.household_price_series", return_value=series):
            self.assertEqual([row["period"] for row in household_price_history("DE", 2025)["periods"]],
                             ["2025-S1", "2025-S2"])
            # 2026-S2 is not published yet, so it is absent rather than estimated.
            self.assertEqual(household_price_history("DE", 2026)["periods"],
                             [{"period": "2026-S1", "retail_eur_per_kwh": 0.36}])


class HouseholdBenchmarkRouteTests(unittest.TestCase):
    def test_route_returns_benchmark_and_rejects_unknown_geo(self):
        app = create_app()
        route = next(r.endpoint for r in app.routes
                     if getattr(r, "path", None) == "/api/household-benchmark")
        expected = {"geography": "DE", "retail_eur_per_kwh": 0.3618, "retail_period": "2026-S1"}
        with patch("eurogrid.api.household_benchmark", return_value=expected):
            self.assertEqual(route("DE"), expected)
        with self.assertRaises(HTTPException) as error:
            route("XX")
        self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

"""Tests for Eurostat household benchmark parsing and API behavior."""

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from eurogrid.api import create_app
from eurogrid.household_service import eurostat_series


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


class HouseholdBenchmarkRouteTests(unittest.TestCase):
    def test_route_returns_benchmark_and_rejects_unknown_geo(self):
        app = create_app()
        route = next(r.endpoint for r in app.routes
                     if getattr(r, "path", None) == "/api/household-benchmark")
        expected = {"geography": "DE", "daily_kwh_per_person": 4.3}
        with patch("eurogrid.api.household_benchmark", return_value=expected):
            self.assertEqual(route("DE"), expected)
        with self.assertRaises(HTTPException) as error:
            route("XX")
        self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

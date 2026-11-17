"""Small date and geography checks for the dashboard's refresh script."""

from datetime import date
import unittest

from scripts.phase5_seed_markets import MARKETS, latest_complete_month


class Phase5SeedTests(unittest.TestCase):
    def test_latest_complete_month_handles_year_boundary(self):
        self.assertEqual(latest_complete_month(date(2026, 1, 17)), ("2025-12-01", "2025-12-31"))

    def test_seed_geographies_include_aggregates_and_full_named_markets(self):
        by_country = {market.country_code: market for market in MARKETS}
        self.assertIn("all", by_country)
        self.assertIn("eu", by_country)
        self.assertIn("uk", by_country)
        self.assertEqual(by_country["de"].name, "Germany")
        self.assertNotIn("it", by_country)


if __name__ == "__main__":
    unittest.main()

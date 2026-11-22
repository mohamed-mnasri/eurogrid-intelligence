import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from eurogrid.normalize import Normalizer, classify, parse_timestamp


def price_sample(times):
    return {"provider": "energy-charts", "dataset": "day_ahead_price", "endpoint": "/v2/price",
            "request_parameters": {"bzn": "DE-LU"}, "metadata": {"timezone": "Europe/Berlin", "interval_minutes": 60,
            "unit": "EUR / MWh", "geography": {"bidding_zone": "DE-LU"}},
            "payload": {"country": "de", "bidding_zone": "DE-LU", "timezone": "Europe/Berlin", "interval_minutes": 60,
            "unit": "EUR / MWh", "data": [{"timestamp": t, "values": {"day_ahead_price": 0 if i == 0 else 50}} for i, t in enumerate(times)]}}


def hours(day, start, end):
    return [f"{day}T{hour:02d}:00" for hour in range(start, end)]


class NormalizeTests(unittest.TestCase):
    def test_dst_fall_day_preserves_two_repeated_local_hours_as_distinct_utc_intervals(self):
        values = hours("2025-10-26", 0, 2) + ["2025-10-26T02:00", "2025-10-26T02:00"] + hours("2025-10-26", 3, 24)
        previous = None
        parsed = []
        for value in values:
            utc, _zone, error = parse_timestamp(value, "Europe/Berlin", previous)
            self.assertIsNone(error)
            parsed.append(utc)
            previous = utc
        self.assertEqual(len(parsed), 25)
        self.assertEqual(len(set(parsed)), 25)
        self.assertEqual(parsed[2].hour, 0)
        self.assertEqual(parsed[3].hour, 1)
        with tempfile.TemporaryDirectory() as tmp:
            norm = Normalizer(Path(tmp) / "dst.duckdb")
            try:
                self.assertEqual(norm.load_prices(price_sample(values)), 25)
                rows = norm.db.execute("SELECT count(*),count(DISTINCT interval_start_utc) FROM fct_market_price").fetchone()
                self.assertEqual(rows, (25, 25))
            finally:
                norm.close()

    def test_dst_spring_day_has_23_intervals_and_rejects_nonexistent_hour(self):
        values = hours("2025-03-30", 0, 2) + hours("2025-03-30", 3, 24)
        previous = None
        parsed = []
        for value in values:
            utc, _zone, error = parse_timestamp(value, "Europe/Berlin", previous)
            self.assertIsNone(error)
            parsed.append(utc)
            previous = utc
        self.assertEqual(len(parsed), 23)
        with tempfile.TemporaryDirectory() as tmp:
            norm = Normalizer(Path(tmp) / "dst.duckdb")
            try:
                self.assertEqual(norm.load_prices(price_sample(values)), 23)
                self.assertEqual(norm.db.execute("SELECT count(*) FROM fct_market_price").fetchone()[0], 23)
            finally:
                norm.close()
        utc, _zone, error = parse_timestamp("2025-03-30T02:00", "Europe/Berlin")
        self.assertIsNone(utc)
        self.assertEqual(error, "nonexistent_local_time")

    def test_ordinary_day_normalizes_and_joins_price_weather_by_utc(self):
        with tempfile.TemporaryDirectory() as tmp:
            norm = Normalizer(Path(tmp) / "grid.duckdb")
            try:
                timestamps = hours("2025-01-15", 0, 24)
                price = price_sample(timestamps)
                norm.load_prices(price, "price-hash")
                weather = {"provider": "open-meteo", "dataset": "historical_weather", "metadata": {
                    "timezone": "UTC", "interval_minutes": 60, "geography": {"latitude": 52.5, "longitude": 13.4}},
                    "payload": {"timezone": "UTC", "latitude": 52.5, "longitude": 13.4,
                    "hourly_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h", "shortwave_radiation": "W/m²"},
                    "hourly": {"time": [f"2025-01-14T{h:02d}:00" for h in range(23, 24)] + [f"2025-01-15T{h:02d}:00" for h in range(24)] + ["2025-01-16T00:00"],
                    "temperature_2m": [4] * 26, "wind_speed_10m": [3.6] * 26, "shortwave_radiation": [0] * 26}}}
                norm.load_weather(weather, "weather-hash")
                count = norm.db.execute("""SELECT count(*) FROM fct_market_price p
                    JOIN fct_weather w USING (interval_start_utc)""").fetchone()[0]
                self.assertEqual(count, 24 * 3)
                self.assertEqual(norm.db.execute("SELECT count(DISTINCT interval_start_utc) FROM fct_market_price").fetchone()[0], 24)
                self.assertEqual(norm.db.execute("SELECT value FROM fct_weather WHERE variable='wind_speed' LIMIT 1").fetchone()[0], 1.0)
                norm.load_prices(price, "price-hash")
                self.assertEqual(norm.db.execute("SELECT count(*) FROM fct_market_price").fetchone()[0], 24)
            finally:
                norm.close()

    def _ingest_versions(self, root, values):
        """Fetch the same day twice with different provider values, in the given order."""
        from eurogrid.ingestion import IngestionStore
        store = IngestionStore(root)
        identity = {"provider": "energy-charts", "dataset": "day_ahead_price", "endpoint": "/v2/price",
                    "request_parameters": {"bzn": "DE-LU", "start": "2025-01-15"}}
        for value in values:
            sample = price_sample(hours("2025-01-15", 0, 24))
            for row in sample["payload"]["data"]:
                row["values"]["day_ahead_price"] = value
            store.ingest(lambda sample=sample: sample, identity=identity, force_refresh=True)
        return store

    def test_most_recently_fetched_version_wins_whatever_the_file_names(self):
        for order in ((50.0, 60.0), (60.0, 50.0)):         # both orders, so file-name sorting cannot decide
            with self.subTest(order=order), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                store = self._ingest_versions(root, order)
                norm = Normalizer(root / "grid.duckdb")
                try:
                    counts = norm.load_directory(root / "raw", store.db_path)
                    self.assertEqual(counts["files"], 2)
                    values = norm.db.execute("SELECT DISTINCT value FROM fct_market_price").fetchall()
                    self.assertEqual(values, [(order[-1],)])
                    full = norm.load_directory(root / "raw", store.db_path, full=True)
                    self.assertEqual(full["files"], 2)
                    self.assertEqual(norm.db.execute("SELECT DISTINCT value FROM fct_market_price").fetchall(),
                                     [(order[-1],)])
                finally:
                    norm.close()

    def test_reruns_only_load_new_files_and_count_unreadable_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._ingest_versions(root, (50.0,))
            (root / "raw" / "broken.json").write_text("{not json", encoding="utf-8")
            norm = Normalizer(root / "grid.duckdb")
            try:
                first = norm.load_directory(root / "raw", store.db_path)
                self.assertEqual((first["files"], first["unreadable"]), (1, 1))
                again = norm.load_directory(root / "raw", store.db_path)
                self.assertEqual((again["files"], again["already_loaded"], again["market_price"]), (0, 1, 0))
                self._ingest_versions(root, (70.0,))
                newer = norm.load_directory(root / "raw", store.db_path)
                self.assertEqual((newer["files"], newer["already_loaded"]), (1, 1))
                self.assertEqual(norm.db.execute("SELECT DISTINCT value FROM fct_market_price").fetchall(), [(70.0,)])
            finally:
                norm.close()

    def test_quality_distinguishes_missing_zero_invalid_and_stale(self):
        self.assertEqual(classify(None), (None, "missing"))
        self.assertEqual(classify(0), (0.0, "valid"))
        self.assertEqual(classify("NaN"), (None, "invalid"))
        self.assertEqual(classify(12, stale=True), (None, "stale"))

    def test_public_power_keeps_demand_and_cross_border_series_for_separate_marts(self):
        with tempfile.TemporaryDirectory() as tmp:
            norm = Normalizer(Path(tmp) / "series.duckdb")
            sample = {"provider": "energy-charts", "dataset": "public_power", "metadata": {
                "timezone": "UTC", "interval_minutes": 60, "unit": "MW",
                "geography": {"country": "de"}, "series": [
                    {"id": "solar"}, {"id": "load"}, {"id": "cross_border_electricity_trading"},
                    {"id": "renewable_share_of_load"}, {"id": "battery_consumption"}]},
                "payload": {"country": "de", "unit": "MW", "timezone": "UTC", "interval_minutes": 60,
                    "data": [{"timestamp": "2025-01-01T00:00Z", "values": {"solar": 2, "load": 10,
                    "cross_border_electricity_trading": -1, "renewable_share_of_load": 20,
                    "battery_consumption": 0}}]}}
            try:
                self.assertEqual(norm.load_generation(sample), 3)
                self.assertEqual(norm.db.execute("SELECT production_type FROM fct_generation ORDER BY production_type").fetchall(),
                                 [("cross_border_electricity_trading",), ("load",), ("solar",)])
            finally:
                norm.close()


if __name__ == "__main__":
    unittest.main()

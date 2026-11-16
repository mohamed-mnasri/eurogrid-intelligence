import tempfile
import unittest
from pathlib import Path

from eurogrid.ingestion import IngestionStore
from eurogrid.cli import _run_chunks


def sample(value=1):
    return {"provider": "energy-charts", "dataset": "day_ahead_price", "endpoint": "/v2/price",
            "requested_at": "2025-01-01T00:00:00+00:00", "request_parameters": {"bzn": "DE-LU", "start": "2025-01-01", "end": "2025-01-01"},
            "metadata": {"geography": {"country": "de", "bidding_zone": "DE-LU"}, "period": {"start": "2025-01-01T00:00:00+01:00"}, "license": "CC BY 4.0"},
            "payload": {"data": [value], "license": "CC BY 4.0"}}


class IngestionTests(unittest.TestCase):
    def test_cache_avoids_second_fetch_and_logs_each_run_without_duplicate_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IngestionStore(Path(tmp))
            calls = []
            identity = {"provider": "energy-charts", "dataset": "day_ahead_price", "endpoint": "/v2/price", "request_parameters": sample()["request_parameters"]}
            fetch = lambda: calls.append(1) or sample()
            first = store.ingest(fetch, identity=identity)
            second = store.ingest(fetch, identity=identity)
            self.assertEqual(len(calls), 1)
            self.assertFalse(first.cache_hit)
            self.assertTrue(second.cache_hit)
            self.assertEqual(first.path, second.path)
            self.assertEqual(list((Path(tmp) / "raw").rglob("response_*.json")), [first.path])
            self.assertEqual(store.stats(), {"runs": 2, "successful_runs": 2, "raw_versions": 1})

    def test_changed_response_preserves_old_raw_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IngestionStore(Path(tmp))
            identity = {"provider": "energy-charts", "dataset": "day_ahead_price", "endpoint": "/v2/price", "request_parameters": sample()["request_parameters"]}
            old = store.ingest(lambda: sample(1), identity=identity)
            new = store.ingest(lambda: sample(2), identity=identity, force_refresh=True)
            self.assertNotEqual(old.raw_sha256, new.raw_sha256)
            self.assertTrue(old.path.exists())
            self.assertTrue(new.path.exists())
            self.assertEqual(len(list((Path(tmp) / "raw").rglob("response_*.json"))), 2)

    def test_fetch_error_is_written_to_run_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IngestionStore(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "offline"):
                store.ingest(lambda: (_ for _ in ()).throw(RuntimeError("offline")))
            with store.connection() as db:
                status, error = db.execute("SELECT status,error_details FROM ingestion_run").fetchone()
            self.assertEqual(status, "failed")
            self.assertIn("offline", error)

    def test_backfill_splits_inclusive_bounded_chunks(self):
        seen = []
        class Result:
            pass
        _run_chunks("2025-01-01", "2025-01-05", 2, lambda a, b: seen.append((a, b)) or Result())
        self.assertEqual(seen, [("2025-01-01", "2025-01-02"), ("2025-01-03", "2025-01-04"), ("2025-01-05", "2025-01-05")])
        with self.assertRaisesRegex(ValueError, "limited"):
            _run_chunks("2025-01-01", "2025-02-15", 1, lambda *_: Result())


if __name__ == "__main__":
    unittest.main()

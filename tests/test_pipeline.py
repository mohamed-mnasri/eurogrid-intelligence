"""Pipeline runner: stage order, failure handling, incremental windows and the run ledger."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from eurogrid.ingestion import IngestionStore
from eurogrid.normalize import Normalizer
from eurogrid.pipeline import (DEFAULT_STAGES, PipelineConfig, PipelineStore, incremental_window, normalize_stage,
                               run_pipeline)
from tests.test_normalize import hours, price_sample


def fake_ingest(context):
    """Writes one raw price file through the real ingestion store (no network)."""
    store = IngestionStore(context.config.data_dir)
    identity = {"provider": "energy-charts", "dataset": "day_ahead_price", "endpoint": "/v2/price",
                "request_parameters": {"bzn": "DE-LU", "start": "2025-01-15"}}
    result = store.ingest(lambda: price_sample(hours("2025-01-15", 0, 24)), identity=identity)
    return {"status": "success", "requests": 1, "rows_received": result.rows}


def ok_dbt(context):
    return {"status": "success", "counts": {"success": 12, "pass": 24}}


def failing_dbt(context):
    return {"status": "failed", "failing": ["unique_gold_market_price_fact_key"]}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = PipelineConfig(database=root / "grid.duckdb", data_dir=root, start=date(2025, 1, 15),
                                     end=date(2025, 1, 15), wait_seconds=0, detect=False)

    def tearDown(self):
        self.temp.cleanup()

    def stages(self, **overrides):
        return {**DEFAULT_STAGES, "ingest": fake_ingest, "dbt": ok_dbt, **overrides}

    def test_successful_run_records_every_stage_and_loads_data(self):
        result = run_pipeline(self.config, self.stages())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["stages"]["normalize"]["market_price"], 24)
        self.assertEqual(result["stages"]["detect"]["status"], "skipped")
        runs = PipelineStore(self.config.ledger).runs()
        self.assertEqual(runs[0]["run_id"], result["run_id"])
        self.assertEqual([stage["stage"] for stage in runs[0]["stages"]], ["ingest", "normalize", "dbt", "detect"])
        self.assertTrue(all(stage["duration_s"] is not None for stage in runs[0]["stages"]))
        rerun = run_pipeline(self.config, self.stages())          # same window again: nothing new to load
        self.assertEqual(rerun["stages"]["normalize"]["files"], 0)
        self.assertEqual(rerun["stages"]["normalize"]["already_loaded"], 1)

    def test_a_failed_stage_stops_the_run(self):
        def broken_ingest(context):
            raise ConnectionError("provider unreachable")
        result = run_pipeline(self.config, self.stages(ingest=broken_ingest))
        self.assertEqual(result["status"], "failed")
        stages = {stage["stage"]: stage for stage in PipelineStore(self.config.ledger).runs()[0]["stages"]}
        self.assertEqual(stages["ingest"]["status"], "failed")
        self.assertIn("provider unreachable", stages["ingest"]["error"])
        self.assertEqual({stages[name]["status"] for name in ("normalize", "dbt", "detect")}, {"not_run"})

    def test_failing_dbt_tests_fail_the_run_and_block_detection(self):
        result = run_pipeline(self.config, self.stages(dbt=failing_dbt))
        self.assertEqual(result["status"], "failed")
        stages = {stage["stage"]: stage["status"] for stage in PipelineStore(self.config.ledger).runs()[0]["stages"]}
        self.assertEqual((stages["dbt"], stages["detect"]), ("failed", "not_run"))

    def test_incremental_window_starts_after_the_newest_price(self):
        self.assertEqual(incremental_window(self.config.database, date(2025, 2, 1)),
                         (date(2025, 1, 2), date(2025, 1, 31)))          # empty database: last 30 days
        fake_ingest(type("Context", (), {"config": self.config})())
        normalize_stage(type("Context", (), {"config": self.config, "results": {}})())
        self.assertEqual(incremental_window(self.config.database, date(2025, 1, 20)),
                         (date(2025, 1, 16), date(2025, 1, 19)))
        self.assertIsNone(incremental_window(self.config.database, date(2025, 1, 16)))   # already up to date

    def test_up_to_date_run_skips_ingestion_and_dbt(self):
        run_pipeline(self.config, self.stages())
        config = PipelineConfig(database=self.config.database, data_dir=self.config.data_dir, wait_seconds=0,
                                detect=False, today=date(2025, 1, 16))
        result = run_pipeline(config, {**DEFAULT_STAGES, "dbt": ok_dbt})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["stages"]["ingest"]["status"], "skipped")
        self.assertIsNone(result["window"])

    def test_status_is_empty_before_any_run(self):
        norm = Normalizer(self.config.database)
        norm.close()
        self.assertEqual(PipelineStore(self.config.ledger).runs(), [])


if __name__ == "__main__":
    unittest.main()

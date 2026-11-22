"""Phase 6 anomaly scoring tests with hand-calculated expected values."""

import unittest
from datetime import datetime, timedelta, timezone

from eurogrid.anomaly import DetectorConfig, HourlyObservation, group_events, score_series

UTC = timezone.utc
NO_FLOOR = DetectorConfig(window_days=28, min_observations=8, threshold=3.5, mad_floor=0.0)


def weekdays_at_noon(values, first=datetime(2026, 1, 5, 12, tzinfo=UTC)):
    """One observation per weekday (Mon–Fri) at 12:00 UTC, starting Monday 5 Jan 2026."""
    result, day = [], first
    for value in values:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        result.append(HourlyObservation(day, float(value)))
        day += timedelta(days=1)
    return result


class RobustScoreTests(unittest.TestCase):
    def test_score_matches_hand_calculation(self):
        # Reference 10..17 → median 13.5; |deviations| 3.5,2.5,1.5,.5,.5,1.5,2.5,3.5 → MAD 2.0.
        # score = 0.6745 * (40 - 13.5) / 2.0 = 8.937
        scored = score_series(weekdays_at_noon([10, 11, 12, 13, 14, 15, 16, 17, 40]), "UTC", NO_FLOOR)
        last = scored[-1]
        self.assertEqual(last.status, "scored")
        self.assertEqual(last.reference_count, 8)
        self.assertAlmostEqual(last.baseline_median, 13.5)
        self.assertAlmostEqual(last.baseline_mad, 2.0)
        self.assertAlmostEqual(last.score, 0.6745 * 26.5 / 2.0, places=6)
        self.assertTrue(last.is_anomalous)

    def test_minimum_data_requirement(self):
        scored = score_series(weekdays_at_noon([10, 11, 12, 40]), "UTC", NO_FLOOR)
        self.assertTrue(all(hour.status == "insufficient_history" for hour in scored))
        self.assertFalse(any(hour.is_anomalous for hour in scored))

    def test_mad_floor_limits_scores_for_flat_references(self):
        flat = weekdays_at_noon([0, 0, 0, 0, 0, 0, 0, 0, 9])
        self.assertEqual(score_series(flat, "UTC", NO_FLOOR)[-1].status, "flat_baseline")
        floored = score_series(flat, "UTC", DetectorConfig(min_observations=8, threshold=3.5, mad_floor=5.0))[-1]
        self.assertAlmostEqual(floored.score, 0.6745 * 9 / 5.0)
        self.assertFalse(floored.is_anomalous)

    def test_no_lookahead_and_window_excludes_old_values(self):
        values = weekdays_at_noon([10, 11, 12, 13, 14, 15, 16, 17, 40, 1000])
        scored = score_series(values, "UTC", NO_FLOOR)
        # The 1000 at the end must not influence the earlier hour's baseline.
        self.assertAlmostEqual(scored[8].baseline_median, 13.5)
        # With a 7-day window only the last five weekdays count, below the 8-value minimum.
        short = score_series(values, "UTC", DetectorConfig(window_days=7, min_observations=8, mad_floor=0))
        self.assertEqual(short[-1].status, "insufficient_history")

    def test_weekend_hours_are_compared_only_with_weekend_hours(self):
        start = datetime(2026, 1, 5, 12, tzinfo=UTC)
        observations = [HourlyObservation(start + timedelta(days=offset), 50.0 if (start + timedelta(days=offset)).weekday() >= 5 else 10.0 + offset % 3)
                        for offset in range(40)]
        scored = score_series(observations, "UTC", DetectorConfig(min_observations=3, mad_floor=1.0))
        weekend = [hour for hour in scored if hour.interval_start_utc.weekday() >= 5 and hour.status == "scored"]
        self.assertTrue(weekend)
        self.assertTrue(all(hour.baseline_median == 50.0 and not hour.is_anomalous for hour in weekend))

    def test_local_hour_uses_market_timezone(self):
        # 11:00 UTC is 12:00 in Berlin in winter; both series should land in the same bucket.
        observations = [HourlyObservation(datetime(2026, 1, day, 11, tzinfo=UTC), 10.0) for day in range(5, 10)]
        scored = score_series(observations, "Europe/Berlin", DetectorConfig(min_observations=3, mad_floor=1))
        self.assertEqual(scored[-1].reference_count, 4)

    def test_rejects_duplicates_and_naive_timestamps(self):
        stamp = datetime(2026, 1, 5, 12, tzinfo=UTC)
        with self.assertRaises(ValueError):
            score_series([HourlyObservation(stamp, 1), HourlyObservation(stamp, 2)], "UTC")
        with self.assertRaises(ValueError):
            score_series([HourlyObservation(datetime(2026, 1, 5, 12), 1)], "UTC")
        with self.assertRaises(ValueError):
            DetectorConfig(min_observations=1)


class GroupEventTests(unittest.TestCase):
    def test_consecutive_flagged_hours_form_one_event_with_stable_id(self):
        config = DetectorConfig(min_observations=8, threshold=3.5, mad_floor=1.0)
        start = datetime(2026, 2, 2, tzinfo=UTC)
        observations = []
        for hour in range(24 * 30):
            stamp = start + timedelta(hours=hour)
            value = 50.0 + (hour % 3)
            if stamp.date() == datetime(2026, 2, 25).date() and stamp.hour in (17, 18, 19):
                value = 400.0
            observations.append(HourlyObservation(stamp, value, ("raw1",)))
        scored = score_series(observations, "UTC", config)
        events = group_events(scored, "DE-LU", "day_ahead_price", "EUR/MWh", config)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.direction, "spike")
        self.assertEqual(event.hour_count, 3)
        self.assertEqual(event.start_utc, "2026-02-25T17:00:00+00:00")
        self.assertEqual(event.end_utc, "2026-02-25T20:00:00+00:00")
        self.assertEqual(event.source_ids, ["raw1"])
        again = group_events(score_series(observations, "UTC", config), "DE-LU", "day_ahead_price", "EUR/MWh", config)
        self.assertEqual(again[0].anomaly_id, event.anomaly_id)
        self.assertEqual(again[0].max_abs_score, event.max_abs_score)


if __name__ == "__main__":
    unittest.main()

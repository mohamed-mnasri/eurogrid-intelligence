"""Phase 6 robust anomaly scoring for hourly market series.

This module is pure Python: it receives already-validated hourly observations
and returns scores and grouped events. It never reads the database, so the same
inputs always produce the same scores and the maths can be unit tested with
hand-calculated values.

Method (handbook section 9.1)
-----------------------------
For each observed hour we build a *comparable-hour* reference: earlier
observations at the same local hour of day and the same day type
(weekday/weekend) inside a trailing window (default 28 days). The current hour
and anything after it are never part of its own baseline.

    median   = median(reference values)
    MAD      = median(|reference value - median|)
    score    = 0.6745 * (observed - median) / max(MAD, mad_floor)   (robust z-score)

0.6745 rescales MAD so the score is comparable to a normal z-score
(Iglewicz & Hoaglin). An hour is anomalous when |score| >= threshold and the
reference has at least ``min_observations`` values. When MAD is zero (a flat
reference) ``mad_floor`` (default 10 EUR/MWh) keeps tiny absolute moves from
scoring as extreme; if both are zero the hour is reported as not scored rather
than given an infinite score.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Iterable
from zoneinfo import ZoneInfo

DETECTOR_VERSION = "robust-mad-comparable-hour-v1"
MAD_SCALE = 0.6745


@dataclass(frozen=True)
class DetectorConfig:
    """Tunable detector settings; all are stored with every event."""

    window_days: int = 28
    min_observations: int = 8
    threshold: float = 5.0
    # Smallest MAD used in the score, in the series unit (EUR/MWh for prices).
    # Midday solar hours can sit near 0 EUR/MWh for weeks, giving a MAD close to
    # zero; without a floor a 10 EUR/MWh move would score in the hundreds.
    mad_floor: float = 10.0
    # Merge flagged hours separated by at most this many unflagged hours.
    max_gap_hours: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.window_days <= 365:
            raise ValueError("window_days must be between 1 and 365")
        if self.min_observations < 3:
            raise ValueError("min_observations must be at least 3")
        if self.threshold <= 0:
            raise ValueError("threshold must be positive")
        if self.mad_floor < 0:
            raise ValueError("mad_floor cannot be negative")
        if self.max_gap_hours < 0:
            raise ValueError("max_gap_hours cannot be negative")


@dataclass(frozen=True)
class HourlyObservation:
    """One valid hourly value in UTC plus the raw source hashes behind it."""

    interval_start_utc: datetime
    value: float
    source_ids: tuple[str, ...] = ()


@dataclass
class ScoredHour:
    """An observation with its comparable-hour baseline and robust score."""

    interval_start_utc: datetime
    value: float
    baseline_median: float | None
    baseline_mad: float | None
    reference_count: int
    score: float | None
    status: str  # scored | insufficient_history | flat_baseline
    source_ids: tuple[str, ...] = ()
    is_anomalous: bool = False


@dataclass
class AnomalyEvent:
    """A run of consecutive anomalous hours in one direction for one series."""

    anomaly_id: str
    geography: str
    metric: str
    unit: str
    direction: str  # spike | dip
    start_utc: str
    end_utc: str
    hour_count: int
    peak_interval_utc: str
    observed_value: float
    baseline_median: float
    deviation: float
    max_abs_score: float
    mean_observed_value: float
    mean_baseline_median: float
    detector_version: str
    detector_config: dict
    source_ids: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _day_type(local: datetime) -> str:
    return "weekend" if local.weekday() >= 5 else "weekday"


def score_series(observations: Iterable[HourlyObservation], timezone_name: str,
                 config: DetectorConfig = DetectorConfig(),
                 score_from: datetime | None = None) -> list[ScoredHour]:
    """Score each observation against its trailing comparable-hour reference.

    Observations before ``score_from`` are used only as history and are not
    returned. Duplicate timestamps are rejected because they would silently
    double-weight an hour.
    """
    zone = ZoneInfo(timezone_name)
    ordered = sorted(observations, key=lambda item: item.interval_start_utc)
    seen: set[datetime] = set()
    buckets: dict[tuple[int, str], tuple[list[datetime], list[float]]] = {}
    keyed: list[tuple[HourlyObservation, tuple[int, str]]] = []
    for item in ordered:
        if item.interval_start_utc.tzinfo is None:
            raise ValueError("interval_start_utc must be timezone-aware")
        if item.interval_start_utc in seen:
            raise ValueError(f"duplicate observation at {item.interval_start_utc.isoformat()}")
        seen.add(item.interval_start_utc)
        local = item.interval_start_utc.astimezone(zone)
        key = (local.hour, _day_type(local))
        times, values = buckets.setdefault(key, ([], []))
        times.append(item.interval_start_utc)
        values.append(item.value)
        keyed.append((item, key))

    window = timedelta(days=config.window_days)
    results: list[ScoredHour] = []
    for item, key in keyed:
        if score_from is not None and item.interval_start_utc < score_from:
            continue
        times, values = buckets[key]
        end = bisect_left(times, item.interval_start_utc)          # strictly earlier
        start = bisect_left(times, item.interval_start_utc - window)
        reference = values[start:end]
        if len(reference) < config.min_observations:
            results.append(ScoredHour(item.interval_start_utc, item.value, None, None, len(reference),
                                      None, "insufficient_history", item.source_ids))
            continue
        center = median(reference)
        mad = median(abs(value - center) for value in reference)
        scale = max(mad, config.mad_floor)
        if scale == 0:
            results.append(ScoredHour(item.interval_start_utc, item.value, center, 0.0, len(reference),
                                      None, "flat_baseline", item.source_ids))
            continue
        score = MAD_SCALE * (item.value - center) / scale
        results.append(ScoredHour(item.interval_start_utc, item.value, center, mad, len(reference),
                                  score, "scored", item.source_ids, abs(score) >= config.threshold))
    return results


def anomaly_id(geography: str, metric: str, start_utc: str, config: DetectorConfig) -> str:
    """Deterministic identifier so re-running detection updates, not duplicates."""
    identity = json.dumps({"geography": geography, "metric": metric, "start_utc": start_utc,
                           "detector": DETECTOR_VERSION, "config": asdict(config)}, sort_keys=True)
    return "anm_" + hashlib.sha256(identity.encode()).hexdigest()[:16]


def group_events(scored: list[ScoredHour], geography: str, metric: str, unit: str,
                 config: DetectorConfig = DetectorConfig()) -> list[AnomalyEvent]:
    """Merge nearby anomalous hours with the same direction into events."""
    flagged = [hour for hour in scored if hour.is_anomalous]
    groups: list[list[ScoredHour]] = []
    for hour in flagged:
        direction = 1 if hour.score > 0 else -1
        if groups:
            last = groups[-1][-1]
            gap = (hour.interval_start_utc - last.interval_start_utc) / timedelta(hours=1) - 1
            if (1 if last.score > 0 else -1) == direction and gap <= config.max_gap_hours:
                groups[-1].append(hour)
                continue
        groups.append([hour])

    events = []
    for group in groups:
        peak = max(group, key=lambda hour: abs(hour.score))
        start = group[0].interval_start_utc.isoformat()
        end = (group[-1].interval_start_utc + timedelta(hours=1)).isoformat()
        sources = sorted({source for hour in group for source in hour.source_ids if source})
        events.append(AnomalyEvent(
            anomaly_id=anomaly_id(geography, metric, start, config),
            geography=geography, metric=metric, unit=unit,
            direction="spike" if peak.score > 0 else "dip",
            start_utc=start, end_utc=end, hour_count=len(group),
            peak_interval_utc=peak.interval_start_utc.isoformat(),
            observed_value=round(peak.value, 4),
            baseline_median=round(peak.baseline_median, 4),
            deviation=round(peak.value - peak.baseline_median, 4),
            max_abs_score=round(abs(peak.score), 3),
            mean_observed_value=round(sum(hour.value for hour in group) / len(group), 4),
            mean_baseline_median=round(sum(hour.baseline_median for hour in group) / len(group), 4),
            detector_version=DETECTOR_VERSION, detector_config=asdict(config), source_ids=sources,
        ))
    return events

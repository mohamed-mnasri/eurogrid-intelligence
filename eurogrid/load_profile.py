"""Phase 7 hourly household load profiles.

When a household has no smart-meter data, a **synthetic** profile is built and
labelled as such in every result. It is a constructed shape, not a published
standard load profile:

* hour-of-day weights with a morning peak (07–09) and a larger evening peak
  (18–21), lower overnight use;
* weekends flatter and later in the morning;
* a seasonal factor peaking in mid-winter (+25%) and lowest in mid-summer (−20%);
* scaled so the year sums exactly to the stated annual consumption.

Measured hourly data, when supplied, is used as-is.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

WEEKDAY_SHAPE = [0.45, 0.38, 0.35, 0.34, 0.35, 0.45, 0.75, 1.10, 1.05, 0.80, 0.70, 0.70,
                 0.75, 0.70, 0.65, 0.70, 0.85, 1.10, 1.45, 1.55, 1.40, 1.15, 0.85, 0.60]
WEEKEND_SHAPE = [0.50, 0.42, 0.38, 0.36, 0.36, 0.40, 0.50, 0.70, 0.95, 1.10, 1.10, 1.05,
                 1.05, 0.95, 0.85, 0.85, 0.95, 1.15, 1.40, 1.45, 1.35, 1.15, 0.90, 0.65]
PROFILE_SOURCE = "synthetic: constructed weekday/weekend daily shape with seasonal factor, scaled to annual kWh"


def year_hours(year: int) -> list[datetime]:
    """Every UTC hour start in a calendar year (8760 or 8784)."""
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    count = int((datetime(year + 1, 1, 1, tzinfo=timezone.utc) - start) / timedelta(hours=1))
    return [start + timedelta(hours=offset) for offset in range(count)]


def synthetic_load(hours: list[datetime], annual_kwh: float, timezone_name: str) -> list[float]:
    """Build a synthetic hourly profile over ``hours`` that sums to ``annual_kwh`` (pro rata for partial years)."""
    zone = ZoneInfo(timezone_name)
    weights = []
    for hour in hours:
        local = hour.astimezone(zone)
        shape = WEEKEND_SHAPE if local.weekday() >= 5 else WEEKDAY_SHAPE
        day_of_year = local.timetuple().tm_yday
        seasonal = 1 + 0.225 * math.cos(2 * math.pi * (day_of_year - 15) / 365.25) + 0.025
        weights.append(shape[local.hour] * seasonal)
    total_weight = sum(weights)
    target = annual_kwh * len(hours) / (8784 if len(hours) == 8784 else 8760)
    return [weight / total_weight * target for weight in weights]

"""Phase 7 hourly PV production.

Two paths, both producing kWh per UTC hour for a given system:

1. **PVGIS reference** (``PVGISProvider``): the official JRC ``seriescalc``
   hourly series for the location, tilt, azimuth and losses. Responses go
   through the Phase 2 raw store and cache, so each location/setting is fetched
   once. PVGIS SARAH3 hourly data currently covers 2005–2023.

2. **Recent-weather model** (``WeatherModelProvider`` + ``pv_from_weather``):
   Open-Meteo hourly global horizontal irradiance (GHI) and air temperature,
   converted with a transparent model:

   * sun position — NOAA simplified equations;
   * diffuse split — Erbs et al. (1982) correlation on the clearness index;
   * plane-of-array irradiance — isotropic sky + ground reflection (albedo 0.2);
   * cell temperature — NOCT model (NOCT 45 °C);
   * DC power — ``P = kWp × POA/1000 × (1 + γ (T_cell − 25))`` with γ = −0.4 %/°C,
     then × (1 − system loss).

   Open-Meteo values are means over the *preceding* hour, so a value stamped
   ``T`` is assigned to the hour starting ``T − 1h`` and the sun position is
   evaluated at ``T − 30 min``. This model is an estimate; compare its annual
   total with PVGIS for the same site before relying on it.

PVGIS annual totals are never spread evenly over 8,760 hours (handbook 10.2).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from eurogrid.connectors import OpenMeteoConnector, PVGISConnector
from eurogrid.http import HttpJsonClient
from eurogrid.ingestion import IngestionStore
from eurogrid.simulation_models import Location, PVSystem

UTC = timezone.utc
SOLAR_CONSTANT = 1367.0
ALBEDO = 0.2
NOCT_C = 45.0
TEMP_COEFFICIENT = -0.004
PVGIS_LAST_YEAR = 2023


class PVDataError(Exception):
    """Raised when PV source data is missing or malformed."""


# ------------------------------------------------------------------ PVGIS path
def parse_pvgis_hourly(payload: dict[str, Any], peak_kw_requested: float = 1.0) -> dict[datetime, float]:
    """Map a PVGIS seriescalc payload to {UTC hour start: kWh per kWp}."""
    hourly = payload.get("outputs", {}).get("hourly")
    if not isinstance(hourly, list) or not hourly:
        raise PVDataError("PVGIS response has no hourly output")
    result: dict[datetime, float] = {}
    for row in hourly:
        stamp = datetime.strptime(row["time"], "%Y%m%d:%H%M").replace(tzinfo=UTC)
        hour = stamp.replace(minute=0)
        power_w = float(row.get("P", 0.0) or 0.0)
        result[hour] = max(0.0, power_w) / 1000 / peak_kw_requested   # mean W over the hour → kWh, per kWp
    return result


class PVGISProvider:
    """Fetch and cache PVGIS hourly reference series per site/orientation/loss."""

    def __init__(self, store: IngestionStore, client_factory=HttpJsonClient) -> None:
        self.store = store
        self.client_factory = client_factory
        self.metadata: dict[str, Any] = {}

    def per_kwp(self, location: Location, system: PVSystem, year: int) -> dict[datetime, float]:
        if not 2005 <= year <= PVGIS_LAST_YEAR:
            raise PVDataError(f"PVGIS hourly data covers 2005–{PVGIS_LAST_YEAR}")
        params = {"lat": location.latitude, "lon": location.longitude, "startyear": year, "endyear": year,
                  "pvcalculation": 1, "peakpower": 1.0, "loss": system.system_loss_pct,
                  "angle": system.tilt_deg, "aspect": system.azimuth_deg, "outputformat": "json"}
        identity = {"provider": "pvgis", "dataset": "pv_hourly_reference", "endpoint": "/seriescalc",
                    "request_parameters": params}

        def fetch():
            with self.client_factory() as client:
                return PVGISConnector(client).hourly_pv_series(
                    location.latitude, location.longitude, year, 1.0, system.system_loss_pct,
                    angle=system.tilt_deg, aspect=system.azimuth_deg)

        result = self.store.ingest(fetch, identity=identity)
        sample = json.loads(result.path.read_text(encoding="utf-8"))
        self.metadata = {"source": "PVGIS 5.3 seriescalc", "raw_sha256": result.raw_sha256,
                         "radiation_database": sample.get("metadata", {}).get("radiation_database"),
                         "attribution": "European Commission JRC, PVGIS 5.3"}
        return parse_pvgis_hourly(sample["payload"])


# ----------------------------------------------------------- weather model path
def solar_position(when_utc: datetime, latitude: float, longitude: float) -> tuple[float, float]:
    """Return (zenith°, azimuth° clockwise from north) using NOAA's simplified equations."""
    day = when_utc.timetuple().tm_yday
    hour = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600
    gamma = 2 * math.pi / 365 * (day - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    true_solar_minutes = hour * 60 + eqtime + 4 * longitude
    hour_angle = math.radians(true_solar_minutes / 4 - 180)
    lat = math.radians(latitude)
    cos_zenith = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    zenith = math.acos(max(-1.0, min(1.0, cos_zenith)))
    azimuth = math.degrees(math.atan2(math.sin(hour_angle),
                                      math.cos(hour_angle) * math.sin(lat) - math.tan(decl) * math.cos(lat))) + 180
    return math.degrees(zenith), azimuth % 360


def plane_of_array(ghi: float, zenith_deg: float, sun_azimuth_deg: float, tilt_deg: float,
                   panel_azimuth_pvgis: float, day_of_year: int) -> float:
    """Erbs diffuse split + isotropic transposition; returns W/m² on the panel."""
    if ghi <= 0 or zenith_deg >= 90:
        return 0.0
    cos_z = math.cos(math.radians(zenith_deg))
    extraterrestrial = SOLAR_CONSTANT * (1 + 0.033 * math.cos(2 * math.pi * day_of_year / 365))
    kt = min(ghi / (extraterrestrial * max(cos_z, 0.065)), 1.0)
    if kt <= 0.22:
        kd = 1 - 0.09 * kt
    elif kt <= 0.80:
        kd = 0.9511 - 0.1604 * kt + 4.388 * kt ** 2 - 16.638 * kt ** 3 + 12.336 * kt ** 4
    else:
        kd = 0.165
    dhi = kd * ghi
    dni = (ghi - dhi) / max(cos_z, 0.065)
    tilt = math.radians(tilt_deg)
    panel_azimuth_north = math.radians((180 + panel_azimuth_pvgis) % 360)
    cos_aoi = (cos_z * math.cos(tilt) + math.sin(math.radians(zenith_deg)) * math.sin(tilt)
               * math.cos(math.radians(sun_azimuth_deg) - panel_azimuth_north))
    return max(0.0, dni * max(cos_aoi, 0.0) + dhi * (1 + math.cos(tilt)) / 2 + ghi * ALBEDO * (1 - math.cos(tilt)) / 2)


def pv_from_weather(weather: list[tuple[datetime, float, float]], location: Location, system: PVSystem,
                    per_kwp: bool = True) -> dict[datetime, float]:
    """Convert (Open-Meteo stamp, GHI W/m², air °C) rows to {UTC hour start: kWh}."""
    scale = 1.0 if per_kwp else system.peak_kw
    result: dict[datetime, float] = {}
    for stamp, ghi, air_temp in weather:
        hour_start = stamp - timedelta(hours=1)
        mid = stamp - timedelta(minutes=30)
        zenith, azimuth = solar_position(mid, location.latitude, location.longitude)
        poa = plane_of_array(ghi, zenith, azimuth, system.tilt_deg, system.azimuth_deg, mid.timetuple().tm_yday)
        cell_temp = air_temp + poa / 800 * (NOCT_C - 20)
        power_kw = scale * poa / 1000 * (1 + TEMP_COEFFICIENT * (cell_temp - 25)) * (1 - system.system_loss_pct / 100)
        result[hour_start] = max(0.0, power_kw)
    return result


class WeatherModelProvider:
    """PV from Open-Meteo archive weather for any year it covers (cached via the raw store)."""

    def __init__(self, store: IngestionStore, client_factory=HttpJsonClient) -> None:
        self.store = store
        self.client_factory = client_factory
        self.metadata: dict[str, Any] = {}

    def per_kwp(self, location: Location, system: PVSystem, year: int) -> dict[datetime, float]:
        # Values are stamped at the END of their hour, so the year's last hour
        # (23:00–24:00 on 31 Dec) arrives stamped 1 Jan 00:00 of the next year.
        start, end = f"{year}-01-01", f"{year + 1}-01-01"
        variables = ("temperature_2m", "shortwave_radiation")
        params = {"latitude": location.latitude, "longitude": location.longitude, "start_date": start,
                  "end_date": end, "hourly": ",".join(variables), "timezone": "UTC"}
        identity = {"provider": "open-meteo", "dataset": "historical_weather", "endpoint": "/v1/archive",
                    "request_parameters": params}

        def fetch():
            with self.client_factory() as client:
                return OpenMeteoConnector(client).historical(location.latitude, location.longitude, start, end, variables)

        result = self.store.ingest(fetch, identity=identity)
        sample = json.loads(result.path.read_text(encoding="utf-8"))
        hourly = sample["payload"]["hourly"]
        rows = []
        for stamp, temp, ghi in zip(hourly["time"], hourly["temperature_2m"], hourly["shortwave_radiation"]):
            if temp is None or ghi is None:
                continue
            rows.append((datetime.fromisoformat(stamp).replace(tzinfo=UTC), float(ghi), float(temp)))
        self.metadata = {"source": "Open-Meteo archive + EuroGrid PV model (NOAA sun position, Erbs, isotropic sky, NOCT)",
                         "raw_sha256": result.raw_sha256, "attribution": "Open-Meteo.com (CC BY 4.0)"}
        modelled = pv_from_weather(rows, location, system)
        return {hour: value for hour, value in modelled.items() if hour.year == year}

    def per_kwp_window(self, location: Location, system: PVSystem, start_utc: datetime,
                       end_utc: datetime) -> dict[datetime, float]:
        """PV per kWp for hour starts in [start_utc, end_utc), from actual weather for just those days.

        Used by the event replay: a whole-year request would fail for the current year, and only the event
        days are needed. End-of-hour stamps are handled by requesting one extra day on each side.
        """
        start = (start_utc - timedelta(days=1)).date().isoformat()
        end = (end_utc + timedelta(days=1)).date().isoformat()
        variables = ("temperature_2m", "shortwave_radiation")
        params = {"latitude": location.latitude, "longitude": location.longitude, "start_date": start,
                  "end_date": end, "hourly": ",".join(variables), "timezone": "UTC"}
        identity = {"provider": "open-meteo", "dataset": "historical_weather", "endpoint": "/v1/archive",
                    "request_parameters": params}

        def fetch():
            with self.client_factory() as client:
                return OpenMeteoConnector(client).historical(location.latitude, location.longitude, start, end, variables)

        result = self.store.ingest(fetch, identity=identity)
        hourly = json.loads(result.path.read_text(encoding="utf-8"))["payload"]["hourly"]
        rows = [(datetime.fromisoformat(stamp).replace(tzinfo=UTC), float(ghi), float(temp))
                for stamp, temp, ghi in zip(hourly["time"], hourly["temperature_2m"], hourly["shortwave_radiation"])
                if temp is not None and ghi is not None]
        self.metadata = {"source": "Open-Meteo archive + EuroGrid PV model (NOAA sun position, Erbs, isotropic sky, NOCT)",
                         "raw_sha256": result.raw_sha256, "attribution": "Open-Meteo.com (CC BY 4.0)",
                         "window": f"{start} to {end}"}
        modelled = pv_from_weather(rows, location, system)
        return {hour: value for hour, value in modelled.items() if start_utc <= hour < end_utc}

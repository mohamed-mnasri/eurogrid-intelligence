"""Synthetic Gold-layer DuckDB with injected, known conditions (handbook 13.2–13.3).

Used by the tests, the Scenario Lab evaluation suite and chaos mode: hourly UTC
data for DE-LU (``de``) and FR (``fr``) from 2026-02-01 to 2026-03-12. Column and
provider names match production so the same tools and SQL run unchanged; every
database built here lives in its own file and is never mixed with real data.

Baseline: prices ≈ 50 EUR/MWh with a small daily pattern, wind 20 GW, solar
3 GW, gas 15 GW, load 50 GW, net trading +1 GW, wind speed 6 m/s. During the
event window (2026-03-05 17:00–20:00 UTC) the DE-LU price jumps to
``price_spike`` and the other series take the values in :class:`Conditions`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

EVENT_START = "2026-03-05 17:00:00+00"
EVENT_END = "2026-03-05 20:00:00+00"
DATA_START = "2026-02-01 00:00:00+00"
DATA_END = "2026-03-12 23:00:00+00"


@dataclass(frozen=True)
class Conditions:
    """Values of the German series inside the event window; ``None`` keeps the baseline."""

    price_spike: float = 400.0
    wind_mw: float | None = 2000.0          # baseline 20,000 MW
    demand_mw: float | None = 55000.0       # baseline 50,000 MW
    net_trading_mw: float | None = None     # baseline +1,000 MW (provider sign)
    wind_speed_ms: float | None = 1.0       # baseline 6 m/s
    neighbour_price: float | None = None    # FR price in the window; None = calm
    missing_generation: bool = False        # delete German generation in the window
    missing_load: bool = False              # delete German load in the window
    missing_price: bool = False             # delete DE-LU prices in the window after detection (delete_event_prices)


def _value(override: float | None, baseline: float) -> str:
    return str(baseline if override is None else override)


def build_synthetic_database(path: Path, conditions: Conditions = Conditions()) -> Path:
    """Write the synthetic Gold tables to ``path`` and return it."""
    c = conditions
    db = duckdb.connect(str(path))
    db.execute("SET TimeZone='UTC'")
    db.execute(f"""CREATE TEMP TABLE hours AS
        SELECT ts FROM generate_series(TIMESTAMPTZ '{DATA_START}', TIMESTAMPTZ '{DATA_END}', INTERVAL 1 HOUR) t(ts)""")
    db.execute("""CREATE TEMP TABLE zones AS SELECT * FROM (VALUES
        ('DE-LU','de','geo-de','Europe/Berlin'), ('FR','fr','geo-fr','Europe/Paris')) z(zone,country,geo,tz)""")
    in_event = f"(h.ts >= TIMESTAMPTZ '{EVENT_START}' AND h.ts < TIMESTAMPTZ '{EVENT_END}')"
    fr_price = (f"WHEN z.zone='FR' AND {in_event} THEN {c.neighbour_price}" if c.neighbour_price is not None else "")

    db.execute(f"""CREATE TABLE gold_market_price AS SELECT
        'p-'||z.zone||'-'||epoch(h.ts) AS fact_key, z.geo AS geography_id, z.country AS country_code,
        z.zone AS bidding_zone, h.ts AS interval_start_utc, h.ts + INTERVAL 1 HOUR AS interval_end_utc,
        60 AS interval_minutes, CAST(h.ts AS VARCHAR) AS original_timestamp, z.tz AS source_timezone,
        0.0 AS original_value, 'EUR/MWh' AS original_unit,
        CASE WHEN z.zone='DE-LU' AND {in_event} THEN {c.price_spike} {fr_price}
             ELSE 50.0 + (extract(hour FROM h.ts) % 3) + (extract(day FROM h.ts) % 2) END AS price_eur_per_mwh,
        'valid' AS data_status, 'energy-charts' AS provider, 'day_ahead_price' AS dataset,
        'raw-price-'||z.country AS raw_sha256
        FROM hours h CROSS JOIN zones z""")
    db.execute("""CREATE TABLE gold_price_hourly_baseline AS SELECT geography_id, country_code, bidding_zone,
        extract(hour FROM interval_start_utc)::INTEGER AS utc_hour, avg(price_eur_per_mwh) AS mean_price_eur_per_mwh,
        count(*) AS baseline_observation_count FROM gold_market_price GROUP BY ALL""")
    db.execute("""CREATE TABLE gold_market_coverage AS SELECT country_code, bidding_zone,
        min(interval_start_utc) AS first_interval_utc, max(interval_end_utc) AS last_interval_utc,
        min(substr(original_timestamp,1,10)) AS first_source_date, max(substr(original_timestamp,1,10)) AS last_source_date,
        min(source_timezone) AS source_timezone, count(*) AS price_interval_count, count(*) AS valid_price_interval_count,
        0 AS flagged_price_interval_count, 'energy-charts' AS provider, 'day_ahead_price' AS dataset
        FROM gold_market_price GROUP BY ALL""")

    db.execute(f"""CREATE TABLE gold_generation_mix AS SELECT
        'g-'||z.country||'-'||t.kind||'-'||epoch(h.ts) AS fact_key, z.geo AS geography_id, z.country AS geography_code,
        z.country AS country_code, t.kind AS production_type, h.ts AS interval_start_utc,
        h.ts + INTERVAL 1 HOUR AS interval_end_utc, 60 AS interval_minutes, CAST(h.ts AS VARCHAR) AS original_timestamp,
        z.tz AS source_timezone, 0.0 AS original_value, 'MW' AS original_unit,
        CASE WHEN t.kind='wind_onshore' AND z.country='de' AND {in_event} THEN {_value(c.wind_mw, 20000.0)}
             WHEN t.kind='wind_onshore' THEN 20000.0
             WHEN t.kind='solar' THEN 3000.0
             ELSE 15000.0 END AS generation_mw,
        'valid' AS data_status, 'energy-charts' AS provider, 'public_power' AS dataset, 'raw-power-'||z.country AS raw_sha256
        FROM hours h CROSS JOIN zones z CROSS JOIN (VALUES ('wind_onshore'),('solar'),('fossil_gas')) t(kind)""")
    db.execute(f"""CREATE TABLE gold_demand AS SELECT
        'd-'||z.country||'-'||epoch(h.ts) AS fact_key, z.geo AS geography_id, z.country AS geography_code,
        z.country AS country_code, h.ts AS interval_start_utc, h.ts + INTERVAL 1 HOUR AS interval_end_utc,
        60 AS interval_minutes, CAST(h.ts AS VARCHAR) AS original_timestamp, z.tz AS source_timezone,
        0.0 AS original_value, 'MW' AS original_unit,
        CASE WHEN z.country='de' AND {in_event} THEN {_value(c.demand_mw, 50000.0)} ELSE 50000.0 END AS demand_mw,
        'valid' AS data_status, 'energy-charts' AS provider, 'public_power' AS dataset, 'raw-power-'||z.country AS raw_sha256
        FROM hours h CROSS JOIN zones z""")
    db.execute(f"""CREATE TABLE gold_cross_border_flow AS SELECT
        'f-'||z.country||'-'||epoch(h.ts) AS fact_key, z.geo AS geography_id, z.country AS geography_code,
        z.country AS country_code, h.ts AS interval_start_utc, h.ts + INTERVAL 1 HOUR AS interval_end_utc,
        60 AS interval_minutes, CAST(h.ts AS VARCHAR) AS original_timestamp, z.tz AS source_timezone,
        0.0 AS original_value, 'MW' AS original_unit,
        CASE WHEN z.country='de' AND {in_event} THEN {_value(c.net_trading_mw, 1000.0)} ELSE 1000.0 END
          AS net_cross_border_trading_mw,
        'valid' AS data_status, 'energy-charts' AS provider, 'public_power' AS dataset, 'raw-power-'||z.country AS raw_sha256
        FROM hours h CROSS JOIN zones z""")
    db.execute(f"""CREATE TABLE gold_weather AS SELECT
        'w-'||z.country||'-'||v.var||'-'||epoch(h.ts) AS fact_key, 'wx-'||z.country AS geography_id, z.country AS country_code,
        52.5 AS latitude, 13.4 AS longitude, h.ts AS interval_start_utc, h.ts + INTERVAL 1 HOUR AS interval_end_utc,
        60 AS interval_minutes, CAST(h.ts AS VARCHAR) AS original_timestamp, 'UTC' AS source_timezone, v.var AS variable,
        0.0 AS original_value, v.unit AS original_unit,
        CASE WHEN v.var='wind_speed' AND z.country='de' AND {in_event} THEN {_value(c.wind_speed_ms, 6.0)}
             WHEN v.var='wind_speed' THEN 6.0 WHEN v.var='temperature' THEN 5.0 ELSE 100.0 END AS weather_value,
        v.unit AS weather_unit, 'valid' AS data_status, 'open-meteo' AS provider, 'historical_weather' AS dataset,
        'raw-weather-'||z.country AS raw_sha256
        FROM hours h CROSS JOIN zones z CROSS JOIN (VALUES ('temperature','°C'),('wind_speed','m/s'),
            ('shortwave_radiation','W/m²')) v(var, unit)""")
    window = f"interval_start_utc >= TIMESTAMPTZ '{EVENT_START}' AND interval_start_utc < TIMESTAMPTZ '{EVENT_END}'"
    if c.missing_generation:
        db.execute(f"DELETE FROM gold_generation_mix WHERE country_code='de' AND {window}")
    if c.missing_load:
        db.execute(f"DELETE FROM gold_demand WHERE country_code='de' AND {window}")
    db.close()
    return path


def delete_event_prices(path: Path, zone: str = "DE-LU") -> None:
    """Remove a zone's prices inside the event window (used after detection to test the blocked path)."""
    with duckdb.connect(str(path)) as db:
        db.execute(f"""DELETE FROM gold_market_price WHERE bidding_zone=?
            AND interval_start_utc >= TIMESTAMPTZ '{EVENT_START}' AND interval_start_utc < TIMESTAMPTZ '{EVENT_END}'""",
                   [zone])

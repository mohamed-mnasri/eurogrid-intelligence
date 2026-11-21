"""Builds a small synthetic Gold-layer DuckDB file for service-level tests.

Contents (all UTC, hourly, 2026-02-01 .. 2026-03-12):

* DE-LU (country ``de``) and FR (``fr``) day-ahead prices: a calm daily pattern
  around 50 EUR/MWh, plus an injected DE-LU spike to 400 EUR/MWh on
  2026-03-05 17:00–20:00 UTC. FR stays calm, so the event is local.
* Generation (wind_onshore, solar, fossil_gas), load, net trading and weather
  for both countries. During the injected spike German wind drops sharply and
  load rises 10%, so the rule-based report has known expected findings.
* ``missing_generation=True`` deletes German generation in the event window to
  exercise fail-closed / limited-report behaviour.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

EVENT_START = "2026-03-05 17:00:00+00"
EVENT_END = "2026-03-05 20:00:00+00"


def build_sample_database(path: Path, missing_generation: bool = False) -> Path:
    db = duckdb.connect(str(path))
    db.execute("SET TimeZone='UTC'")
    db.execute("""CREATE TEMP TABLE hours AS
        SELECT ts FROM generate_series(TIMESTAMPTZ '2026-02-01 00:00:00+00',
                                       TIMESTAMPTZ '2026-03-12 23:00:00+00', INTERVAL 1 HOUR) t(ts)""")
    db.execute("""CREATE TEMP TABLE zones AS SELECT * FROM (VALUES
        ('DE-LU','de','geo-de','Europe/Berlin'), ('FR','fr','geo-fr','Europe/Paris')) z(zone,country,geo,tz)""")
    in_event = f"(h.ts >= TIMESTAMPTZ '{EVENT_START}' AND h.ts < TIMESTAMPTZ '{EVENT_END}')"

    db.execute(f"""CREATE TABLE gold_market_price AS SELECT
        'p-'||z.zone||'-'||epoch(h.ts) AS fact_key, z.geo AS geography_id, z.country AS country_code,
        z.zone AS bidding_zone, h.ts AS interval_start_utc, h.ts + INTERVAL 1 HOUR AS interval_end_utc,
        60 AS interval_minutes, CAST(h.ts AS VARCHAR) AS original_timestamp, z.tz AS source_timezone,
        0.0 AS original_value, 'EUR/MWh' AS original_unit,
        CASE WHEN z.zone='DE-LU' AND {in_event} THEN 400.0
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
        CASE WHEN t.kind='wind_onshore' AND z.country='de' AND {in_event} THEN 2000.0
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
        CASE WHEN z.country='de' AND {in_event} THEN 55000.0 ELSE 50000.0 END AS demand_mw,
        'valid' AS data_status, 'energy-charts' AS provider, 'public_power' AS dataset, 'raw-power-'||z.country AS raw_sha256
        FROM hours h CROSS JOIN zones z""")
    db.execute("""CREATE TABLE gold_cross_border_flow AS SELECT
        'f-'||z.country||'-'||epoch(h.ts) AS fact_key, z.geo AS geography_id, z.country AS geography_code,
        z.country AS country_code, h.ts AS interval_start_utc, h.ts + INTERVAL 1 HOUR AS interval_end_utc,
        60 AS interval_minutes, CAST(h.ts AS VARCHAR) AS original_timestamp, z.tz AS source_timezone,
        0.0 AS original_value, 'MW' AS original_unit, 1000.0 AS net_cross_border_trading_mw,
        'valid' AS data_status, 'energy-charts' AS provider, 'public_power' AS dataset, 'raw-power-'||z.country AS raw_sha256
        FROM hours h CROSS JOIN zones z""")
    db.execute(f"""CREATE TABLE gold_weather AS SELECT
        'w-'||z.country||'-'||v.var||'-'||epoch(h.ts) AS fact_key, 'wx-'||z.country AS geography_id, z.country AS country_code,
        52.5 AS latitude, 13.4 AS longitude, h.ts AS interval_start_utc, h.ts + INTERVAL 1 HOUR AS interval_end_utc,
        60 AS interval_minutes, CAST(h.ts AS VARCHAR) AS original_timestamp, 'UTC' AS source_timezone, v.var AS variable,
        0.0 AS original_value, v.unit AS original_unit,
        CASE WHEN v.var='wind_speed' AND z.country='de' AND {in_event} THEN 1.0
             WHEN v.var='wind_speed' THEN 6.0 WHEN v.var='temperature' THEN 5.0 ELSE 100.0 END AS weather_value,
        v.unit AS weather_unit, 'valid' AS data_status, 'open-meteo' AS provider, 'historical_weather' AS dataset,
        'raw-weather-'||z.country AS raw_sha256
        FROM hours h CROSS JOIN zones z CROSS JOIN (VALUES ('temperature','°C'),('wind_speed','m/s'),
            ('shortwave_radiation','W/m²')) v(var, unit)""")
    if missing_generation:
        db.execute(f"""DELETE FROM gold_generation_mix WHERE country_code='de'
            AND interval_start_utc >= TIMESTAMPTZ '{EVENT_START}' AND interval_start_utc < TIMESTAMPTZ '{EVENT_END}'""")
    db.close()
    return path

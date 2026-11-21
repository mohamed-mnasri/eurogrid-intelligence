# Data dictionary

All timestamps are interval starts/ends in UTC (`TIMESTAMP WITH TIME ZONE`). Every fact row keeps:

- its original timestamp text, source timezone, original value and unit;
- a `data_status`: `valid`, `missing`, `invalid` or `stale`;
- its provider and dataset;
- the SHA-256 of the raw file it came from.

## Source tables (Phase 3, `data/eurogrid.duckdb`)

| Table | Grain | Notes |
|---|---|---|
| `dim_geography` | one row per geography | `geography_type` is `country`, `bidding_zone`, `aggregate` or `coordinate`. Also holds `code`, `name`, `country_code`, `bidding_zone`, and `latitude`/`longitude` for weather points. |
| `fct_market_price` | one row per price interval and bidding zone | Day-ahead price normalized to EUR/MWh. Intervals are 15 or 60 minutes. |
| `fct_generation` | one row per interval, geography and `production_type` | Public-power series in MW. Includes `load` and `cross_border_electricity_trading`, which the Gold models split out. |
| `fct_weather` | one row per interval, coordinate and `variable` | Covers `temperature` (°C), `wind_speed` (m/s) and `shortwave_radiation` (W/m²). |

## Gold models (dbt, `eurogrid_analytics/models/marts`)

| Model | Key columns | Measure | Tests |
|---|---|---|---|
| `gold_market_price` | `fact_key`; `country_code`, `bidding_zone`, `interval_start_utc` | `price_eur_per_mwh` | `fact_key` unique and not null; zone and start not null |
| `gold_price_hourly_baseline` | `geography_id`, `utc_hour` | `mean_price_eur_per_mwh` and `baseline_observation_count` (valid rows only) | keys not null |
| `gold_market_coverage` | `bidding_zone` | first and last interval and source date; interval counts (total, valid, flagged) | zone unique and not null |
| `gold_generation_mix` | `fact_key`; `country_code`, `production_type`, interval | `generation_mw`; excludes load, trading, shares, storage consumption | `fact_key` unique and not null |
| `gold_demand` | `fact_key`; `country_code`, interval | `demand_mw` (public-grid load) | `fact_key` unique and not null |
| `gold_cross_border_flow` | `fact_key`; `country_code`, interval | `net_cross_border_trading_mw` (net scheduled trading, provider sign) | `fact_key` unique and not null |
| `gold_weather` | `fact_key`; `country_code`, `latitude`, `longitude`, `variable`, interval | `weather_value` and `weather_unit` | `fact_key` unique and not null |
| `energy_market_hourly` | price interval × generation series × weather variable | wide exploratory join with `price_status`, `generation_status` and `weather_status` | statuses in the accepted set; keys not null |

## Application stores (`data/metadata/`, SQLite)

| File | Table | Contents |
|---|---|---|
| `ingestion_runs.sqlite3` | `ingestion_run` | One row per request: provider, dataset, endpoint, parameters, request and raw hashes, times, status, rows received, error, cache hit |
| `investigations.sqlite` | `anomaly_event` | Detected events (`anm_…`): zone, direction, window, scores, detector config, raw source IDs |
| | `evidence` | Tool-call records (`ev_…`): tool, parameters, Gold models read, status, result summary, source IDs |
| | `investigation_report` | Reports (`rpt_…`) from `rule-based-v1` or `langgraph-ollama:<model>` |
| | `agent_trace` | Agent activity: graph path, who chose each tool, the model's draft before checks |
| `simulations.sqlite` | `simulation_run` | Scenario, results and hourly flows per system (`sim_…`) |
| `replays.sqlite` | `replay` | Event-to-household replays (`rpl_…`) |
| `evaluations.sqlite` | `evaluation_run` | Scenario Lab runs per generator, with per-case checks |
| `chaos.sqlite` | `chaos_run` | Chaos experiments (`chs_…`); their database copies are in `data/chaos/<run>/` |

## Identifiers

Event, evidence, report, simulation and replay IDs are SHA-256 hashes of their defining inputs. Running the same thing twice gives the same ID. Chaos run IDs include the run time, because each run is a separate experiment.

## Units cheat sheet

| Quantity | Unit | Note |
|---|---|---|
| Wholesale price | EUR/MWh | Divide by 1,000 for €/kWh |
| Household price | €/kWh | Eurostat band 2,500–4,999 kWh/yr, all taxes |
| Grid load, generation, trading | MW (average over the interval) | Summing hourly MW gives MWh |
| Household energy | kWh per hour | |
| PV yield | kWh per kWp | Multiplied by system size |

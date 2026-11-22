# EuroGrid dbt project

dbt reads the normalized source tables in DuckDB and builds the views the API serves. Those sources are `dim_geography`, `fct_market_price`, `fct_generation` and `fct_weather`, written by `eurogrid/normalize.py`.

| Layer | Models |
|---|---|
| Staging (`models/staging`) | `stg_geography`, `stg_market_price`, `stg_generation`, `stg_weather`: column selection only |
| Gold (`models/marts`) | `gold_market_price`, `gold_generation_mix`, `gold_demand`, `gold_cross_border_flow`, `gold_weather`, `gold_market_coverage`, `gold_price_hourly_baseline`, plus the exploratory `energy_market_hourly` |

## Tests (42)

- **Keys:** `unique` and `not_null` on fact keys, and the required interval and zone fields.
- **Statuses:** `accepted_values` on every `data_status`: `valid`, `missing`, `invalid`, `stale`.
- **Relationships:** every fact's `geography_id` must exist in `stg_geography`.
- **Gaps:** `no_hourly_gaps` (`tests/generic/`) fails when a bidding zone or country misses whole hours of prices, generation or load inside its ingested range. It was added after chaos mode showed that a missing feed passed every schema test.
- **Freshness:** source freshness on the three fact tables warns after 3 days and errors after 10 days without new intervals.

## Run

Copy `profiles.example.yml` to `profiles.yml` (gitignored) and run these from the repository root:

```powershell
.\.venv-dbt\Scripts\dbt.exe build --project-dir eurogrid_analytics --profiles-dir eurogrid_analytics
```

```powershell
.\.venv-dbt\Scripts\dbt.exe source freshness --project-dir eurogrid_analytics --profiles-dir eurogrid_analytics
```

`python -m eurogrid.pipeline run` runs both as part of the pipeline. `python scripts/dbt_synthetic_check.py` builds and tests the project on synthetic data, with no production data needed.

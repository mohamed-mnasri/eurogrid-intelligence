# Phase 4: dbt models and data quality

Phase 4 makes the Phase 3 DuckDB tables easier to query and documents how the outputs are built. dbt does not call the source APIs: it reads the normalized tables already in `data/eurogrid.duckdb` and creates SQL views in DuckDB.

## The model layers

- `models/sources.yml` names the four existing Phase 3 objects in DuckDB's `main` schema.
- `models/staging/` contains one small `stg_*.sql` model per source. These select useful columns without repeating the Phase 3 normalization.
- `models/marts/` contains the Gold analytical views: price, generation mix, demand, net cross-border trading, weather, market coverage, and a UTC-hour price baseline.
- `energy_market_hourly` is an exploratory wide join. Its row grain is one price interval crossed with available generation series and weather variables. For charts, use the separate Gold views so each dataset keeps its own natural grain.
- `energy_market_hourly.yml` describes models and includes dbt quality tests.

The source-aware coverage view selects only bidding zones with observed price rows. It currently discovers `DE-LU`, `FR`, `ES`, `IT-North`, and `NL`. New zones appear after ingestion and a dbt run; the list is not a hard-coded UI list.

Demand is the provider's public `load` power series in MW, not interval energy in MWh. The flow model preserves Energy-Charts' net scheduled-trading series and its sign as received; it does not claim a border-by-border physical flow. Generation geography, price bidding zones, and weather coordinate locations remain distinct.

## Check or rebuild the models

From the repository root in the VS Code terminal (with the `.venv-dbt` environment active):

```powershell
dbt debug --project-dir eurogrid_analytics
dbt parse --project-dir eurogrid_analytics
dbt run --project-dir eurogrid_analytics
dbt test --project-dir eurogrid_analytics
```

The dbt profile stays in `%USERPROFILE%\.dbt\profiles.yml` and points to the local DuckDB file. It is machine-specific and is not part of the repository. At implementation time, all 12 models ran and all 24 dbt data tests passed. The tests check documented keys, required interval fields, accepted quality statuses, and coverage uniqueness.

`gold_price_hourly_baseline` is the mean price for each UTC hour of the day across the currently ingested valid observations for that zone. With the Phase 5 sample, that means a one-month January 2025 reference curve; it is a simple chart baseline, not a multi-year market forecast or anomaly detector.

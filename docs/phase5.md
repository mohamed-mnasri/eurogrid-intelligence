# Phase 5: European Market Overview

Phase 5 builds the first working market product: a React page asks a local FastAPI service for records, and the service reads tested dbt Gold views in DuckDB. The browser never opens DuckDB directly.

## What is included

- Market selector built from `gold_market_coverage`, so only zones with saved price observations appear.
- Start/end date controls for ranges up to 31 days.
- Price history with a same-UTC-hour mean reference curve.
- Country-level generation-by-source and a separately calculated hourly renewable-share chart.
- Public load/demand and the provider's net scheduled cross-border trading series.
- A weather chart with a variable selector.
- Data coverage, missing/flagged/stale counts, source-model lineage, and the SHA-256 hashes of the exact raw responses used.

The first sample covers the handbook's five validated market groups from 1–31 January 2025: Germany (`DE-LU`), France (`FR`), Spain (`ES`), Italy (`IT-North`), and the Netherlands (`NL`). Prices come from the zone-specific Energy-Charts endpoint; public power and load are country-scoped. Weather is for one reference coordinate per country: Berlin, Paris, Madrid, Milan, and Amsterdam. These points do not represent whole countries or bidding zones.

The renewable share is calculated from valid observations for wind, solar, hydro run-of-river/reservoir, biomass, geothermal, and other renewables divided by reported generation for that hour. Missing/invalid records are excluded and are visible in quality counts. Storage discharge is not labeled as renewable generation. Cross-border trading keeps the provider's sign unchanged and is not a physical-border flow breakdown.

## Rebuild or refresh the data

Install the project's Python dependencies in the root `.venv`, then run:

```powershell
python -m scripts.phase5_seed_markets --start 2025-01-01 --end 2025-01-31
python -m eurogrid.normalize
```

The fetch script makes public read-only calls, observes Energy-Charts' low request rate, and writes the original response wrappers to gitignored `data/raw/`. The Bronze responses are retained; the normalizer adds/upserts their facts in the local DuckDB file.

Rebuild and validate the Gold views with the separate `.venv-dbt` environment:

```powershell
dbt run --project-dir eurogrid_analytics
dbt test --project-dir eurogrid_analytics
```

## Run the local application

Open **two VS Code terminals** from the repository root.

Terminal 1 starts the read-only API:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn eurogrid.api:app --reload --host 127.0.0.1 --port 8000
```

The API's interactive OpenAPI page is at `http://127.0.0.1:8000/docs` while it is running. It provides `/api/health`, `/api/coverage`, and `/api/overview`.

Terminal 2 starts the React page:

```powershell
cd frontend
npm install
npm run dev
```

Open the local address printed by Vite, usually `http://localhost:5173`. To stop either server, focus its terminal and press **Ctrl+C**.

## Reading the charts and their limits

- The price baseline is an average by UTC hour over the saved sample window. It is a visual reference, not a causal explanation or forecast.
- Generation, demand and trade are power measurements in MW. They should not be described as energy volumes in MWh.
- Price geography is a bidding zone; generation geography is a country; weather geography is a city coordinate. The dashboard displays those differences.
- The hourly joins are by UTC timestamp only. A same-time weather observation is context, not proof that weather caused a market change.
- Gap estimates count expected local market intervals using the zone timezone, including 23/25-hour daylight-saving days. Missing observations stay missing.
- Raw JSON, the SQLite ingestion log, the DuckDB file, dbt build artifacts, and frontend dependencies are local and ignored by Git.

The initial page is a Phase 5 overview rather than the full Phase 10 application. It does not include anomaly investigations, household simulation, AI, or the remaining application pages.

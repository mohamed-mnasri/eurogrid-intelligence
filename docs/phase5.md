# Phase 5: European Market Overview

The React dashboard asks a local FastAPI service for records. The API reads tested dbt Gold views in DuckDB; the browser never opens the database directly.

## What the dashboard shows

- The country selector lists only geographies that have been fetched into the local database. It begins with All Europe, then named countries. The overlapping EU-only aggregate is hidden. Italy is not shown because the saved Italian series represents only northern Italy, not the country.
- The month selector opens at the newest month available for the selected area. Previous/next buttons and the month input let you move through saved history.
- Price charts appear only for bidding zones with saved prices. There is no single Europe-wide day-ahead price, and the configured Energy-Charts catalogue has no UK price series. Power data can still be shown for UK.
- Prices, generation, demand, cross-border trading, and reference-location weather are shown when that source is available. Sub-hourly observations are averaged into UTC-hour buckets before comparisons.
- Coverage and source lineage are available below the charts.

The seeding script supports All Europe (`country=all`), Germany, France, Spain, Poland, the Netherlands, and the United Kingdom. Prices are fetched only for available zones (DE-LU, FR, ES, PL, NL). For All Europe, the dashboard computes a simple average of the available zone prices for each hour. It is an indicative comparison, not an official Europe-wide market price, and it weights each available market equally. Weather is one reference point per country, not a national average. The provider-supplied Europe power aggregate cannot be split into north/east/west regions after the fact; a regional roll-up requires country-by-country data and a defined grouping.

The summary converts wholesale price to €/kWh for a familiar scale, while also showing its original €/MWh value. Power comparison charts use GW (1 GW = 1,000 MW). The generation chart starts with all reported sources combined beside public-grid load. Its selector lets you compare a single source with that load; the summary shows the selected source as a percentage of average load for matching hours. These are grid statistics, not household usage. Weather is a historical reference point; it does not calculate solar-panel output or battery coverage. That estimate needs solar-system, battery, location, and household-use assumptions.

## Public load and generation are not a full balance

The charts use Energy-Charts' **public-power** series. Public generation can be lower than public-grid load without implying an outage: imports and exports, storage, industrial self-supply, and source coverage all affect the relationship. The dashboard therefore describes the two series separately and does not call their difference reserve capacity or a blackout indicator. Energy-Charts publishes a separate total-power series that includes industrial self-supply; it is not currently part of this dashboard's Gold models.

## Refresh or backfill data

The script defaults to the latest complete calendar month. For historical navigation, pass an inclusive range (up to 731 days):

```powershell
.\.venv\Scripts\python.exe -m scripts.phase5_seed_markets
.\.venv\Scripts\python.exe -m eurogrid.normalize
.\.venv-dbt\Scripts\dbt.exe run --project-dir eurogrid_analytics
.\.venv-dbt\Scripts\dbt.exe test --project-dir eurogrid_analytics
```

For example, this fetches Jan 2025 through Oct 2026:

```powershell
.\.venv\Scripts\python.exe -m scripts.phase5_seed_markets --start 2025-01-01 --end 2026-10-31
```

The fetch script makes public read-only calls sequentially, waits to respect the provider's request limit, and saves responses under gitignored `data/raw/`. The normalizer upserts facts into local DuckDB. Never commit the local database or raw provider responses.

## Run in VS Code

Open two terminals at the repository root. In Terminal 1 start the API:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn eurogrid.api:app --reload --host 127.0.0.1 --port 8000
```

In Terminal 2 start the web page:

```powershell
cd frontend
npm run dev
```

Open the Vite address (usually `http://localhost:5173`). Stop each process with **Ctrl+C**. The API docs are at `http://127.0.0.1:8000/docs`.

## Reading limits

- MW is a rate of power at an instant; MWh is an amount of energy over time; €/MWh is wholesale price, not a household tariff.
- The dashboard shows power comparisons in GW (1 GW = 1,000 MW) and also gives wholesale prices per kWh for a more familiar scale. The per-kWh number is not a household bill.
- Generation is reported output, not installed capacity. Demand is provider-reported public-grid load. These alone do not describe a complete national electricity balance.
- Price geography is a bidding zone; generation geography is the selected country or aggregate; weather is one coordinate. A same-hour association is context, not proof of cause.
- The price baseline is the mean for the same UTC hour across currently saved valid observations. It is a visual reference, not a forecast.
- Missing readings remain blank; blank does not mean zero.

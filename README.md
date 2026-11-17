# EuroGrid Intelligence

European Grid-to-Home Energy Intelligence Platform

EuroGrid will combine European electricity-market data with transparent household solar and battery scenarios. Its market analysis and energy calculations will be deterministic; AI will later investigate verified results and explain evidence with clear limitations.

## Current stage

**Phase 1: API connector proof of concept.** The project now has a timeout/retry-aware HTTPX client, read-only connectors for Energy-Charts, Open-Meteo, PVGIS 5.3 and Eurostat, offline contract tests, and a bounded sample runner. There is no database, transformation layer, analytics, AI agent or frontend yet.

See [the Phase 1 sample record](docs/phase1.md) for the observed response shapes, intervals, units and licensing notes.

The architecture is pan-European. Coverage is discovered from provider metadata and each endpoint's live response; it can differ by data family and date. Germany, France, Spain, Italy and the Netherlands are the first validation markets in the handbook, not the geographic limit. Energy-Charts public generation supports `country=all`, which the Phase 0 probe has verified. Day-ahead prices require one bidding zone per request and have zone-specific licensing, so an all-Europe price request is not available from that API. See `docs/sources.md`.

## Project setup in Visual Studio Code

1. Install Python 3.11 or newer if needed (check the Python Launcher with `py -0p`), then open this folder in VS Code.
2. Open **Terminal → New Terminal** and create the environment:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

   If PowerShell blocks activation, use `\.venv\Scripts\python.exe` directly in the following commands.
3. In VS Code, choose the `.venv` interpreter (`Ctrl+Shift+P` → **Python: Select Interpreter**).
4. Run the initial public-source checks:

   ```powershell
   python scripts/phase0_probe.py
   ```

   The probes make a small number of read-only requests, print response metadata and a short sample, and do not save provider data. They honor the Energy-Charts low request rate by making at most one request per endpoint on each run. See `docs/sources.md` for the source and licence inventory.

## Run the offline tests

In the VS Code terminal, activate `.venv` as above and run the offline unit tests:

```powershell
python -m unittest discover -s tests -v
```

The tests cover the implemented phases, including request parameters, response validation, retry behavior, source metadata, idempotent ingestion, timezone transitions and normalized facts. They do not call any API. To repeat the Phase 0 connectivity checks separately, run `python scripts/phase0_probe.py` (this makes public read-only API requests).

## Run Phase 1 connector tests and sample

Fetch the bounded Phase 1 sample set and save raw JSON plus its source/coverage/licence manifest locally:

```powershell
python -m eurogrid.sample
```

By default, the sample covers 1–2 January 2025 for market/weather endpoints and PVGIS hourly output for 2020. It requests DE-LU day-ahead prices, all-Europe public generation (`country=all`), Berlin weather, Paris annual and hourly solar output, and the selected Eurostat household-price dimensions across all geographies the dataset returns. Responses are saved under `data/raw/phase1/`, which is gitignored because source data may carry redistribution conditions. The command runs requests sequentially and prints each response's geography, period, unit, resolution and licence note. Use `--output`, `--start`, `--end` and `--pvgis-year` to change the sample.

The HTTP client only performs GET requests. It retries timeouts, network errors and transient 429/5xx responses a bounded number of times, honoring `Retry-After` when supplied. It does not retry ordinary 4xx errors.

## Phase 2 ingestion

Use `python -m eurogrid.cli --help` or follow the [Phase 2 guide](docs/phase2.md) for single-connector ingestion, bounded backfills, request caching, raw content-addressed storage and the SQLite run ledger.

## Phase 3 normalization

Install dependencies with `python -m pip install -r requirements.txt`, then follow the [Phase 3 guide](docs/phase3.md) to create and inspect the local DuckDB model.

## Phase 4 dbt models

The dbt sources, staging views, Gold marts, and quality checks are described in the [Phase 4 guide](docs/phase4.md).

## Phase 5 market overview

The React dashboard, FastAPI read endpoints, five-market sample, and startup steps are in the [Phase 5 guide](docs/phase5.md).

## Data and security rules

- Treat source terms and returned licence metadata as authoritative for each dataset.
- Keep API tokens and private household data out of Git. `.env` is ignored; `.env.example` contains names only.
- Missing data stays missing; it is never silently changed to zero.
- Wholesale prices are not household retail tariffs.
- Simulated household profiles and financial assumptions must be labeled as such.

## Current limits

The market overview is currently validated for DE-LU, FR, ES, IT-North, and NL over a bounded January 2025 sample. This is not complete pan-European data coverage. Generation/load and net trading availability varies by country and provider series. ENTSO-E still requires a separately requested user token and is not included. The database and sample responses remain local; review source terms before redistributing them.

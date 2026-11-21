# EuroGrid Intelligence

**European Grid-to-Home Energy Intelligence Platform.** It ingests public European electricity-market data and finds unusual price hours. It investigates them with evidence, using a deterministic engine or a local AI model whose every statement is checked by code. It also shows what an event would have meant for a household with solar and a battery. It runs locally, on free, keyless public sources.

| Page | What it does |
|---|---|
| Market Overview | Prices, household-price reference, grid use, generation and weather for a country and month |
| Market Analytics | Country comparison, month × hour price heatmap, detected anomalies |
| AI Investigations | Cases with evidence-linked findings, activity trace, Markdown/JSON export; rule-based or local LLM agent |
| Renewable Planner | Hourly PV/battery simulation, bills, payback, NPV, sensitivity, battery dispatch |
| Event Replay | A market event replayed against a simulated household: tariffs, systems, usual vs actual prices |
| Data Operations | Connector runs, dbt tests and lineage, coverage gaps, licence register, chaos mode |
| Scenario Lab | Synthetic cases with known causes, scored check by check |

See [architecture](docs/architecture.md), [data dictionary](docs/data_dictionary.md), [sources and licences](docs/sources.md) and [known limitations](docs/limitations.md).

## Quick start: synthetic demo (no credentials, no data download)

Requires Windows, macOS or Linux with **Python 3.12 or 3.13** (both tested in CI) and **Node.js 24**. The commands below are PowerShell; `py -3` picks your newest Python (use `py -3.13` to choose one). On macOS/Linux use `python3 -m venv .venv` and `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`.

```powershell
py -3 -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

Optional, so the demo is built by the real dbt project; without it the demo writes ready-made tables:

```powershell
py -3 -m venv .venv-dbt
```

```powershell
.\.venv-dbt\Scripts\python.exe -m pip install -r requirements-dbt.txt
```

Build the demo. It writes `data/demo/` and prints the next commands:

```powershell
.\.venv\Scripts\python.exe -m eurogrid.demo
```

Start the API on the demo data:

```powershell
$env:EUROGRID_DB_PATH="data/demo/eurogrid_demo.duckdb"; $env:EUROGRID_DATA_DIR="data/demo"; $env:EUROGRID_DBT_DIR="data/demo/dbt"; .\.venv\Scripts\python.exe -m uvicorn eurogrid.api:app --host 127.0.0.1 --port 8000
```

In a second terminal, install and start the frontend, then open http://localhost:5173:

```powershell
npm --prefix frontend ci
```

```powershell
npm --prefix frontend run dev
```

The app shows a **Synthetic demo data** banner. The demo contains invented data for Germany and France (Feb–Mar 2026) with a known injected price spike on 5 March. The Planner and Event Replay also call PVGIS, Open-Meteo and Eurostat (free, no key).

**Optional AI agent:** install [Ollama](https://ollama.com/download), then run:

```powershell
ollama pull qwen3.5:4b
```

Use **Run AI agent** on a case. Without Ollama, the API returns a clear 503 and everything else keeps working.

## Full setup with real data

1. Create both environments as above.
2. Copy `eurogrid_analytics/profiles.example.yml` to `eurogrid_analytics/profiles.yml`. That file is gitignored and needs no credentials.
3. Ingest and model the data. The seed script respects Energy-Charts' rate limits; see [Phase 5](docs/phase5.md).

```powershell
.\.venv\Scripts\python.exe -m scripts.phase5_seed_markets --start 2025-01-01 --end 2026-10-31
```

```powershell
.\.venv\Scripts\python.exe -m eurogrid.normalize
```

```powershell
.\.venv-dbt\Scripts\dbt.exe build --project-dir eurogrid_analytics --profiles-dir eurogrid_analytics
```

4. Start the API without the demo variables, then start the frontend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn eurogrid.api:app --reload --host 127.0.0.1 --port 8000
```

5. Detect anomalies from **Market Analytics**, or with the CLI:

```powershell
.\.venv\Scripts\python.exe -m eurogrid.investigate_cli detect --zone DE-LU --start 2026-01-01 --end 2026-10-31
```

API documentation is at http://127.0.0.1:8000/docs.

## Tests and CI

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

```powershell
.\.venv\Scripts\python.exe scripts\dbt_synthetic_check.py
```

```powershell
npm --prefix frontend run lint
```

```powershell
npm --prefix frontend run build
```

All tests run offline. LLM and PV calls use fakes; chaos tests use synthetic data and the real dbt project when `.venv-dbt` exists. GitHub Actions runs the same checks (see `.github/workflows/ci.yml`).

## Evaluations and experiments

Run the Scenario Lab suite with the rule-based investigator:

```powershell
.\.venv\Scripts\python.exe -m eurogrid.evaluation
```

Run it with the local AI agent:

```powershell
.\.venv\Scripts\python.exe -m eurogrid.evaluation --generator agent
```

Run a chaos experiment against a detected anomaly:

```powershell
.\.venv\Scripts\python.exe -m eurogrid.chaos --fault missing_feed --anomaly-id <anm_id>
```

## How it was built, phase by phase

| Phase | Guide |
|---|---|
| 0–1 Sources and connectors | [phase1](docs/phase1.md), [sources](docs/sources.md) |
| 2 Ingestion, raw store, run ledger | [phase2](docs/phase2.md) |
| 3 Normalization into DuckDB | [phase3](docs/phase3.md) |
| 4 dbt models and tests | [phase4](docs/phase4.md) |
| 5 Market overview dashboard | [phase5](docs/phase5.md) |
| 6 Anomaly detection and rule-based investigations | [phase6](docs/phase6.md) |
| 7 Household PV and battery simulation | [phase7](docs/phase7.md) |
| 8 API service layer | [phase8](docs/phase8.md) |
| 9 Local LLM agent (LangGraph + Ollama) | [phase9](docs/phase9.md) |
| 10 Web application and Scenario Lab | [phase10](docs/phase10.md) |
| 11 Event replay and chaos mode | [phase11](docs/phase11.md) |
| 12 CI, pinned dependencies, demo, docs | [phase12](docs/phase12.md) |

## Data and security rules

- Each dataset's source terms, and the licence returned with each response, are authoritative. Raw and modelled data under `data/` is never committed.
- No secrets are needed. If you add an ENTSO-E token later, keep it in `.env` (gitignored); `.env.example` lists names only.
- Missing data stays missing; it is never silently filled with zero.
- Wholesale prices are not household tariffs. Simulations, replays and synthetic data are labelled as such.
- The AI model cannot run SQL, choose geographies or dates, or mark a cause as proven.

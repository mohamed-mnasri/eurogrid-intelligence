# Phase 12: Hardening, packaging and portfolio release

## What is in place

| Handbook item | Delivered |
|---|---|
| CI | `.github/workflows/ci.yml`: Python unit/service/API/agent/chaos tests; `dbt build` + 24 tests on synthetic sources; frontend lint + build |
| Pinned dependencies | `requirements-lock.txt` (exact, tested), `requirements-dbt.txt` (separate dbt environment), `frontend/package-lock.json`; `requirements.txt` keeps compatible ranges |
| Exact setup commands | README quick start (synthetic demo) and full setup (real data) |
| Redistributable sample | `python -m eurogrid.demo`: generated synthetic data (no third-party data, so no licence restrictions) built by the real dbt project, with detected events, investigations and an evaluation run; the app labels it **Synthetic demo data** |
| Docker Compose | Not added; see below |
| Architecture, data dictionary, licences, limitations | `docs/architecture.md`, `docs/data_dictionary.md`, `docs/sources.md` + `eurogrid/source_register.py` (shown on Data Operations), `docs/limitations.md` |
| Demo video and LinkedIn case study | Outline in `docs/demo_script.md`; draft article in `docs/case_study.md`. Recording and publishing are left to the author. |

## Exit check

The handbook's exit check: *a new developer can follow the README, use the sample data and run the core demo without obtaining every optional API credential.*

- The demo needs no credentials at all.
- Market Overview, Analytics, Investigations (rule-based), Data Operations and Scenario Lab work entirely offline.
- The Planner and Replay call PVGIS, Open-Meteo and Eurostat, which are free and keyless.
- Only the optional AI agent needs Ollama.

## CI

The backend job:

1. installs `requirements-lock.txt`;
2. creates `.venv-dbt` from `requirements-dbt.txt`;
3. runs `scripts/dbt_synthetic_check.py`, which builds synthetic sources and runs `dbt build`, failing on any model or test failure;
4. runs every unittest suite. The real-dbt chaos test runs because `.venv-dbt` exists.

The frontend job runs `npm ci`, `npm run lint` and `npm run build`. CI never needs production data, network APIs or Ollama.

Run the same checks locally:

```powershell
.\.venv\Scripts\python.exe scripts\dbt_synthetic_check.py
```

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

```powershell
npm --prefix frontend run lint
```

```powershell
npm --prefix frontend run build
```

## Why there is no Docker Compose

The handbook says to add Compose *only if it simplifies startup*. Here it would not:

- Ollama is the heaviest dependency. On Windows it runs natively with GPU access; inside Docker it needs GPU passthrough and a model volume.
- dbt runs in its own virtual environment for a few seconds, only when data changes.
- The API and Vite dev server already start with one command each.

Compose would add a Docker Desktop requirement and a second way to run everything, without removing a step. This can be revisited if the project gains a hosted deployment.

# Phase 10: React user interface

The dashboard is now a six-page application with a sidebar (`frontend/src/App.tsx`). Every page reads only from the API.

| Page | Route | What it shows | Endpoints |
|---|---|---|---|
| Market Overview | `#/overview` | The Phase 5 dashboard: prices, household reference, grid use, generation, weather | `/api/overview`, `/api/price-history`, `/api/household-*` |
| Market Analytics | `#/analytics` | Monthly price by country, average/range/negative-price table, month × local-hour heatmap, anomaly scatter and list; run detection for a year | `/api/analytics/price-comparison`, `/api/analytics/price-profile`, `/api/anomalies`, `POST /api/anomalies/detect` |
| AI Investigations | `#/investigations/<anomaly_id>` | Case list with filters and paging; case detail; run rule-based or the local AI agent; findings with clickable evidence; activity trace (graph path, who chose each tool, the model's raw draft); evidence records; Markdown/JSON export | `/api/anomalies/{id}/investigations`, `POST /api/investigations`, `/api/investigations/{id}/trace`, `/api/investigations/{id}/export`, `/api/evidence/{id}` |
| Renewable Planner | `#/planner` | Location, use, solar data, tariff (fixed or dynamic) and finance inputs; editable installer costs; configuration comparison with a recommendation by objective; monthly energy balance; cumulative cash flow; sensitivity; one-week battery dispatch | `POST /api/simulations`, `/api/simulations/{id}/monthly`, `/api/simulations/{id}/hourly` |
| Data Operations | `#/operations` | Connector health and recent runs, dbt test results, lineage (which models a failure affects), coverage per country × domain with missing hours, source and licence register | `/api/operations/*` |
| Scenario Lab | `#/lab` | Synthetic evaluation: each case × check matrix for rule-based and agent runs, case definitions, scoring rules, run history | `/api/evaluations`, `/api/evaluations/history`, `POST /api/evaluations/run` |

## States

Every page has a loading, empty and error state (`components/States.tsx`). An error boundary keeps one failing page from blanking the app. Stale data is flagged in two places:

- the sidebar badge, from the newest coverage date;
- a stale notice on Market Overview when data is more than 7 days old.

## Scenario Lab and the agent check it led to

The evaluation suite (`eurogrid/evaluation.py`, synthetic data from `eurogrid/synthetic.py`) runs 7 cases with known injected conditions:

- wind drop;
- demand spike;
- import reduction;
- shared regional spike;
- no clear explanation;
- missing generation feed;
- missing price feed.

It scores each case pass/fail on 9 checks. There is no blended accuracy number.

The first agent run passed **1/7** cases. The local model filed unchanged series as "coinciding relationships". Code now re-files any coinciding or plausible finding whose cited evidence shows no material change, using the rule-based thresholds (`material_change` in `eurogrid/investigation.py`). The re-run passed **7/7**. Both runs are kept in the run history.

```powershell
.\.venv\Scripts\python.exe -m eurogrid.evaluation
```

```powershell
.\.venv\Scripts\python.exe -m eurogrid.evaluation --generator agent
```

## Run

```powershell
.\.venv\Scripts\python.exe -m uvicorn eurogrid.api:app --reload --host 127.0.0.1 --port 8000
```

```powershell
npm --prefix frontend run dev
```

Open http://localhost:5173. The AI agent button needs Ollama running with `qwen3.5:4b` pulled. Without it the API returns a clear 503 and the rule-based investigation still works.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_phase10_api -v
```

```powershell
npm --prefix frontend run lint
```

```powershell
npm --prefix frontend run build
```

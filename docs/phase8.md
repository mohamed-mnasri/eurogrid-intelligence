# Phase 8: FastAPI service layer

Phase 8 exposes the market data, anomaly detection, investigations and household simulations through documented HTTP endpoints. Open **http://127.0.0.1:8000/docs** (Swagger UI) or **/redoc** while the API is running.

## Structure

Route handlers in `eurogrid/api.py` only validate inputs, call a service and return a typed response. All database and calculation work lives in the service modules:

| Service | Module | Covers |
|---|---|---|
| `MarketService` | `eurogrid/market_service.py` | coverage, dashboard overview, paginated market series, monthly price history |
| household prices | `eurogrid/household_service.py` | Eurostat H1/H2 household price references |
| `AnomalyService` | `eurogrid/anomaly_service.py` | detection, saved events, investigations, evidence, approved tools |
| `SimulationService` | `eurogrid/simulation_service.py` | running and storing household simulations, hourly pages |

- Request and response models are in `eurogrid/api_models.py`. Engine models (investigation reports, evidence, tool results, scenarios and simulation results) are reused directly, so the API and the engines can't drift apart.
- Services raise `NotFoundError` (404), `InvalidRequestError` (422) or `UnavailableError` (503) from `eurogrid/service_errors.py`, and one handler turns these into HTTP responses.
- Every error body has a single `detail` field, which the dashboard already shows.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | process check and API version |
| GET | `/api/coverage` | countries/zones present in Gold |
| GET | `/api/overview` | all dashboard measures, ≤ 31 days |
| GET | `/api/market-series` | one measure (`price`, `generation`, `load`, `cross_border_flows`, `weather`), ≤ 31 days, `limit` ≤ 2000 with `offset` |
| GET | `/api/price-history` | monthly wholesale averages for a year |
| GET | `/api/household-benchmark`, `/api/household-price-history` | Eurostat references |
| GET | `/api/anomalies/zones` | zones approved for detection/tools |
| POST | `/api/anomalies/detect` | run detection (≤ 366 days); saves events, repeatable IDs |
| GET | `/api/anomalies` | saved events; filter by zone/date/direction; `limit` ≤ 200 |
| GET | `/api/anomalies/{anomaly_id}` | one event |
| POST | `/api/investigations` | `{"anomaly_id": ...}` → rule-based report |
| GET | `/api/anomalies/{anomaly_id}/investigation` | latest report for an anomaly |
| GET | `/api/investigations/{report_id}` | a stored report |
| GET | `/api/evidence/{evidence_id}` | the tool call behind an evidence ID |
| GET | `/api/tools` | approved tool catalogue |
| POST | `/api/tools/{tool_name}` | run one approved tool (UTC window ≤ 31 days); evidence is stored |
| GET | `/api/simulations/default-systems` | the three illustrative configurations |
| POST | `/api/simulations` | body = household scenario → `simulation_id` + results |
| GET | `/api/simulations`, `/api/simulations/{simulation_id}` | stored runs |
| GET | `/api/simulations/{simulation_id}/hourly` | hourly flows for one system, ≤ 744 rows per page |

## Validation and limits

- **Date and time windows:** overview and series are limited to 31 days, tools to 31 days, and detection to 366 days. Tool timestamps must include a timezone (for example `Z`).
- **Allowed values:** zones must be in the approved list. Tool names must be in the allowlist; there is no endpoint that accepts SQL. Path IDs are checked against their format (`anm_…`, `rpt_…`, `ev_…`, `sim_…`).
- **Page sizes:** every list or series is capped by `limit`, and responses include `total` and `next_offset`.
- **Fail closed:** a dynamic tariff without complete wholesale prices for the year returns 422 with the reason, rather than estimating the missing hours.

## Local storage

All stored data is gitignored under `data/metadata/`:

- `investigations.sqlite` holds anomaly events, evidence and reports.
- `simulations.sqlite` holds scenarios, results and hourly rows.

Set `EUROGRID_DB_PATH` / `EUROGRID_DATA_DIR` to point the API somewhere else.

## Try it

Start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn eurogrid.api:app --reload --host 127.0.0.1 --port 8000
```

Then use Swagger UI's **Try it out** buttons, or call it from PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/anomalies/detect -ContentType 'application/json' -Body '{"bidding_zone":"DE-LU","start_date":"2026-09-01","end_date":"2026-09-30"}'
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_api_service -v
```

The tests build the synthetic sample DuckDB (`tests/sample_db.py`) and call the API over HTTP with FastAPI's test client, using an offline PV provider. They check:

- OpenAPI coverage;
- pagination and range limits;
- 404/422 errors;
- detect → list → investigate → evidence round trips;
- tool allowlist and input checks;
- repeatable simulation IDs and hourly paging;
- the refusal to run a dynamic tariff with missing prices.

The React dashboard keeps calling the same endpoints as before. No frontend code reads DuckDB.

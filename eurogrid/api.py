"""EuroGrid HTTP API (Phase 5 market overview + Phase 8 service layer).

Route handlers stay thin: they validate inputs with Pydantic/Query rules, call a
service module, and return a typed response. All database and calculation work
lives in the services:

* ``MarketService``      – coverage, overview, market series, price history
* ``household_service``  – Eurostat household price references
* ``AnomalyService``     – anomaly detection, investigations, evidence, approved tools
* ``SimulationService``  – household PV/battery simulations
* ``OperationsService``  – connector runs, dbt tests, freshness, lineage, source register
* ``evaluation``         – Scenario Lab synthetic evaluation suite
* ``ReplayService``      – Grid-to-Home event replay (Phase 11)
* ``chaos``              – test-only failure injection on copies of the data (Phase 11)

Interactive documentation: http://127.0.0.1:8000/docs (Swagger UI) and /redoc.
The browser never reads DuckDB directly.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import Body, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from eurogrid.anomaly_service import MAX_PAGE_SIZE, AnomalyService, default_llm_factory
from eurogrid.chaos import FAULTS, ChaosStore, DbtRunner, run_chaos, run_dbt
from eurogrid.evaluation import EvaluationStore, run_suite
from eurogrid.api_models import (AnomalyEventModel, AnomalyPage, ApprovedZone, ChaosRequest, CoverageResponse,
                                 DetectRequest, ReplayRequest,
                                 DetectResponse, ErrorResponse, Health, InvestigationRequest, MarketSeriesResponse,
                                 SimulationHourPage, SimulationPage, SimulationRecord, SimulationResponse, ToolInfo,
                                 ToolRequest)
from eurogrid.household_service import household_benchmark, household_price_history
from eurogrid.investigation import InvestigationReport
from eurogrid.investigation_tools import APPROVED_TOOLS, Evidence, InvestigationTools, ToolResult
from eurogrid.llm import LLM
from eurogrid.market_service import MarketDataError, MarketService
from eurogrid.operations_service import OperationsService
from eurogrid.replay import WindowPVProvider
from eurogrid.replay_service import ReplayService
from eurogrid.service_errors import ServiceError
from eurogrid.simulation import PVProvider
from eurogrid.simulation_models import HouseholdScenario, SystemConfig, default_systems
from eurogrid.simulation_service import MAX_HOURLY_PAGE, SimulationService, default_provider_factory

API_VERSION = "0.11.0"
EUROSTAT_GEOS = {"EU27_2020", "DE", "FR", "ES", "PL", "NL", "UK"}
ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}
TAGS = [
    {"name": "markets", "description": "Coverage, overview and bounded market series from tested Gold models."},
    {"name": "household prices", "description": "Eurostat half-yearly household electricity price references."},
    {"name": "anomalies", "description": "Robust comparable-hour price anomaly detection (Phase 6)."},
    {"name": "investigations", "description": "Rule-based investigation reports and their evidence records."},
    {"name": "tools", "description": "The approved, read-only investigation tools. No SQL input is accepted."},
    {"name": "simulations", "description": "Household PV and battery simulations (Phase 7)."},
    {"name": "analytics", "description": "Cross-country price comparison and seasonal price profiles."},
    {"name": "operations", "description": "Connector runs, dbt test results, freshness, lineage and source licences."},
    {"name": "evaluation", "description": "Scenario Lab: synthetic cases with known injected conditions."},
    {"name": "replay", "description": "Grid-to-Home: replay a market event against a stored household simulation."},
    {"name": "chaos", "description": "Test-only failure injection on a copy of the data; production is never modified."},
]


def create_app(database_path: Path | None = None, data_dir: Path | None = None,
               pv_provider_factory: Callable[[str], PVProvider] | None = None,
               llm_factory: Callable[[str], LLM] | None = None, dbt_dir: Path | None = None,
               window_pv_factory: Callable[[], WindowPVProvider] | None = None,
               dbt_runner: DbtRunner | None = run_dbt) -> FastAPI:
    """Create the API app. Paths, the PV provider and the LLM are injectable for tests."""
    db_path = database_path or Path(os.environ.get("EUROGRID_DB_PATH", "data/eurogrid.duckdb"))
    data_root = data_dir or Path(os.environ.get("EUROGRID_DATA_DIR", "data"))
    dbt_root = dbt_dir or Path(os.environ.get("EUROGRID_DBT_DIR", "eurogrid_analytics"))
    app = FastAPI(title="EuroGrid Intelligence API", version=API_VERSION, openapi_tags=TAGS,
                  description="Read-only market analytics, deterministic anomaly investigations and household "
                              "PV/battery simulations backed by tested dbt Gold models.")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])

    def anomalies() -> AnomalyService:
        return AnomalyService(db_path, data_root / "metadata" / "investigations.sqlite",
                              data_root / "investigations", llm_factory)

    def operations() -> OperationsService:
        return OperationsService(db_path, data_root, dbt_root)

    def evaluations() -> EvaluationStore:
        return EvaluationStore(data_root / "metadata" / "evaluations.sqlite")

    def replays() -> ReplayService:
        return ReplayService(db_path, data_root, simulations(), window_pv_factory)

    def chaos_store() -> ChaosStore:
        return ChaosStore(data_root / "metadata" / "chaos.sqlite")

    def simulations() -> SimulationService:
        return SimulationService(db_path, data_root / "metadata" / "simulations.sqlite",
                                 pv_provider_factory or default_provider_factory(data_root))

    @app.exception_handler(ServiceError)
    def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    # ------------------------------------------------------------ markets
    @app.get("/api/health", response_model=Health, tags=["markets"])
    def health() -> dict[str, str]:
        """Confirm the API is responding, and say whether it serves real local data or the synthetic demo."""
        return {"status": "ok", "version": API_VERSION, "dataset": MarketService(db_path).dataset_kind()}

    @app.get("/api/coverage", response_model=CoverageResponse, responses=ERRORS, tags=["markets"])
    def coverage() -> dict[str, Any]:
        """List the countries/zones discovered in Gold, so the UI does not invent options."""
        try:
            return {"markets": MarketService(db_path).coverage()}
        except MarketDataError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/overview", responses=ERRORS, tags=["markets"])
    def overview(
        bidding_zone: str = Query(min_length=2, max_length=32),
        start_date: date = Query(),
        end_date: date = Query(),
    ) -> dict[str, Any]:
        """All dashboard measures for one zone and at most 31 days."""
        try:
            return MarketService(db_path).overview(bidding_zone, start_date, end_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MarketDataError as exc:
            status = 404 if str(exc).startswith(("No ingested price coverage", "No ingested data")) else 503
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.get("/api/market-series", response_model=MarketSeriesResponse, responses=ERRORS, tags=["markets"])
    def market_series(
        bidding_zone: str = Query(min_length=2, max_length=32, examples=["DE-LU"]),
        measure: Literal["price", "generation", "load", "cross_border_flows", "weather"] = Query(),
        start_date: date = Query(),
        end_date: date = Query(description="Inclusive; at most 31 days after start_date"),
        limit: int = Query(default=500, ge=1, le=MarketService.MAX_SERIES_PAGE),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """One measure for one zone, paginated with ``limit``/``offset``."""
        try:
            return MarketService(db_path).series(bidding_zone, measure, start_date, end_date, limit, offset)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MarketDataError as exc:
            status = 404 if str(exc).startswith("No ingested data") else 503
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.get("/api/price-history", responses=ERRORS, tags=["markets"])
    def price_history(
        bidding_zone: str = Query(min_length=2, max_length=32),
        year: int = Query(ge=2000, le=2100),
    ) -> dict[str, Any]:
        """Monthly wholesale averages for one zone/year, using observed hours only."""
        try:
            return MarketService(db_path).price_history(bidding_zone, year)
        except MarketDataError as exc:
            status = 404 if str(exc).startswith("No ingested data") else 503
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    # --------------------------------------------------- household prices
    @app.get("/api/household-benchmark", responses=ERRORS, tags=["household prices"])
    def household_reference(geo: str = Query(min_length=2, max_length=16)) -> dict[str, Any]:
        """Eurostat's latest tax-inclusive household electricity price."""
        if geo not in EUROSTAT_GEOS:
            raise HTTPException(status_code=404, detail="No Eurostat benchmark configured for this geography")
        try:
            return household_benchmark(geo)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Eurostat benchmark data is unavailable: {exc}") from exc

    @app.get("/api/household-price-history", responses=ERRORS, tags=["household prices"])
    def household_price_series(
        geo: str = Query(min_length=2, max_length=16),
        year: int = Query(ge=2007, le=2100),
    ) -> dict[str, Any]:
        """Official H1/H2 household prices without filling missing periods."""
        if geo not in EUROSTAT_GEOS:
            raise HTTPException(status_code=404, detail="No Eurostat benchmark configured for this geography")
        try:
            return household_price_history(geo, year)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Eurostat price history is unavailable: {exc}") from exc

    # ---------------------------------------------------------- anomalies
    @app.get("/api/anomalies/zones", response_model=list[ApprovedZone], responses=ERRORS, tags=["anomalies"])
    def anomaly_zones() -> list[dict[str, Any]]:
        """Bidding zones approved for detection and tools (those with ingested prices)."""
        return anomalies().zones()

    @app.post("/api/anomalies/detect", response_model=DetectResponse, responses=ERRORS, tags=["anomalies"])
    def detect(request: DetectRequest) -> dict[str, Any]:
        """Score a zone's prices and save the anomaly events (re-running updates, never duplicates)."""
        config = request.config()
        events = anomalies().detect(request.bidding_zone, request.start_date, request.end_date, config)
        return {"bidding_zone": request.bidding_zone, "start_date": request.start_date, "end_date": request.end_date,
                "detector_config": config.__dict__, "detected": len(events), "events": events}

    @app.get("/api/anomalies", response_model=AnomalyPage, responses=ERRORS, tags=["anomalies"])
    def list_anomalies(
        bidding_zone: str | None = Query(default=None, max_length=16),
        start_date: date | None = Query(default=None, description="Events starting on/after this UTC date"),
        end_date: date | None = Query(default=None, description="Events starting before this UTC date"),
        direction: Literal["spike", "dip"] | None = None,
        limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """Saved anomaly events, newest first."""
        items, total = anomalies().list(bidding_zone, start_date, end_date, direction, limit, offset)
        return {"total": total, "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < total else None, "items": items}

    @app.get("/api/anomalies/{anomaly_id}", response_model=AnomalyEventModel, responses=ERRORS, tags=["anomalies"])
    def get_anomaly(anomaly_id: str = PathParam(pattern=r"^anm_[0-9a-f]{16}$")) -> dict[str, Any]:
        return anomalies().get(anomaly_id)

    # ----------------------------------------------------- investigations
    @app.post("/api/investigations", response_model=InvestigationReport, responses=ERRORS, tags=["investigations"])
    def create_investigation(request: InvestigationRequest) -> Any:
        """Investigate one saved anomaly: rule-based (instant, deterministic) or the Phase 9 agent with a local
        Ollama model (takes tens of seconds; 503 if Ollama is not running)."""
        if request.generator == "agent":
            return anomalies().investigate_with_agent(request.anomaly_id, request.model, request.max_tool_calls)
        return anomalies().investigate(request.anomaly_id)

    @app.get("/api/anomalies/{anomaly_id}/investigations", responses=ERRORS, tags=["investigations"])
    def list_investigations(anomaly_id: str = PathParam(pattern=r"^anm_[0-9a-f]{16}$")) -> list[dict[str, Any]]:
        """Every stored report for an anomaly (rule-based and agent), newest first."""
        return anomalies().reports(anomaly_id)

    @app.get("/api/investigations/{report_id}/trace", responses=ERRORS, tags=["investigations"])
    def investigation_trace(report_id: str = PathParam(pattern=r"^rpt_[0-9a-f]{16}$")) -> dict[str, Any]:
        """Activity trace: graph path, each tool call and who chose it, and the model's draft before checking."""
        return anomalies().trace(report_id)

    @app.get("/api/investigations/{report_id}/export", responses=ERRORS, tags=["investigations"])
    def export_investigation(report_id: str = PathParam(pattern=r"^rpt_[0-9a-f]{16}$"),
                             format: Literal["markdown", "json"] = Query(default="markdown")) -> Any:
        """Download a report as Markdown or JSON."""
        service = anomalies()
        if format == "json":
            return JSONResponse(service.report(report_id),
                                headers={"Content-Disposition": f'attachment; filename="{report_id}.json"'})
        return PlainTextResponse(service.markdown(report_id), media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{report_id}.md"'})

    @app.get("/api/anomalies/{anomaly_id}/investigation", response_model=InvestigationReport, responses=ERRORS,
             tags=["investigations"])
    def latest_investigation(anomaly_id: str = PathParam(pattern=r"^anm_[0-9a-f]{16}$")) -> dict[str, Any]:
        return anomalies().latest_report(anomaly_id)

    @app.get("/api/investigations/{report_id}", response_model=InvestigationReport, responses=ERRORS,
             tags=["investigations"])
    def get_investigation(report_id: str = PathParam(pattern=r"^rpt_[0-9a-f]{16}$")) -> dict[str, Any]:
        return anomalies().report(report_id)

    @app.get("/api/evidence/{evidence_id}", response_model=Evidence, responses=ERRORS, tags=["investigations"])
    def get_evidence(evidence_id: str = PathParam(pattern=r"^ev_[0-9a-f]{16}$")) -> dict[str, Any]:
        """The stored tool call behind an evidence ID: parameters, models, result and raw source IDs."""
        return anomalies().evidence(evidence_id)

    # -------------------------------------------------------------- tools
    @app.get("/api/tools", response_model=list[ToolInfo], tags=["tools"])
    def list_tools() -> list[dict[str, str]]:
        """The approved tool catalogue (the same allowlist the agent uses)."""
        return [{"name": name, "description": (getattr(InvestigationTools, name).__doc__ or "").strip().splitlines()[0]}
                for name in APPROVED_TOOLS]

    @app.post("/api/tools/{tool_name}", response_model=ToolResult, responses=ERRORS, tags=["tools"])
    def run_tool(tool_name: str, request: ToolRequest) -> ToolResult:
        """Run one approved tool; its evidence record is stored and retrievable by ID."""
        return anomalies().run_tool(tool_name, request.bidding_zone, request.start_utc, request.end_utc,
                                    request.options())

    # -------------------------------------------------------- simulations
    @app.get("/api/simulations/default-systems", response_model=list[SystemConfig], tags=["simulations"])
    def simulation_defaults() -> list[SystemConfig]:
        """The three illustrative system configurations (costs are placeholders)."""
        return default_systems()

    @app.post("/api/simulations", response_model=SimulationResponse, responses=ERRORS, tags=["simulations"])
    def create_simulation(scenario: HouseholdScenario = Body(examples=[{
        "scenario_id": "berlin-3500", "location": {"latitude": 52.52, "longitude": 13.405, "timezone": "Europe/Berlin"},
        "annual_consumption_kwh": 3500, "simulation_year": 2023, "pv_source": "pvgis",
        "tariff": {"kind": "fixed", "import_eur_per_kwh": 0.36, "export_eur_per_kwh": 0.08},
        "systems": [system.model_dump() for system in default_systems()]}])) -> dict[str, Any]:
        """Simulate every system in the scenario; the same scenario always returns the same ID."""
        simulation_id, result = simulations().run(scenario)
        return {"simulation_id": simulation_id, "result": result}

    @app.get("/api/simulations", response_model=SimulationPage, tags=["simulations"])
    def list_simulations(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        items, total = simulations().list(limit, offset)
        return {"total": total, "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < total else None, "items": items}

    @app.get("/api/simulations/{simulation_id}", response_model=SimulationRecord, responses=ERRORS,
             tags=["simulations"])
    def get_simulation(simulation_id: str = PathParam(pattern=r"^sim_[0-9a-f]{16}$")) -> dict[str, Any]:
        return simulations().get(simulation_id)

    @app.get("/api/simulations/{simulation_id}/hourly", response_model=SimulationHourPage, responses=ERRORS,
             tags=["simulations"])
    def simulation_hourly(
        simulation_id: str = PathParam(pattern=r"^sim_[0-9a-f]{16}$"),
        system_id: str = Query(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$"),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=168, ge=1, le=MAX_HOURLY_PAGE),
    ) -> dict[str, Any]:
        """Hourly energy flows and prices for one system, at most 31 days per page."""
        return simulations().hourly(simulation_id, system_id, offset, limit)

    @app.get("/api/simulations/{simulation_id}/monthly", responses=ERRORS, tags=["simulations"])
    def simulation_monthly(
        simulation_id: str = PathParam(pattern=r"^sim_[0-9a-f]{16}$"),
        system_id: str = Query(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$"),
    ) -> dict[str, Any]:
        """Seasonal energy balance: hourly flows summed by local month, with import cost and export revenue."""
        return simulations().monthly(simulation_id, system_id)

    # ---------------------------------------------------------- analytics
    @app.get("/api/analytics/price-comparison", responses=ERRORS, tags=["analytics"])
    def price_comparison(year: int = Query(ge=2015, le=2100)) -> dict[str, Any]:
        """Monthly mean/min/max day-ahead price for every priced country zone."""
        try:
            return MarketService(db_path).price_comparison(year)
        except MarketDataError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/analytics/price-profile", responses=ERRORS, tags=["analytics"])
    def price_profile(bidding_zone: str = Query(min_length=2, max_length=16),
                      year: int = Query(ge=2015, le=2100)) -> dict[str, Any]:
        """Average price by local month x local hour of day (seasonality heatmap)."""
        try:
            return MarketService(db_path).price_profile(bidding_zone, year)
        except MarketDataError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # --------------------------------------------------------- operations
    @app.get("/api/operations/ingestion", tags=["operations"])
    def operations_ingestion(recent: int = Query(default=25, ge=1, le=200)) -> dict[str, Any]:
        """Connector health from the ingestion ledger, plus the most recent runs."""
        return operations().ingestion(recent)

    @app.get("/api/operations/dbt", tags=["operations"])
    def operations_dbt() -> dict[str, Any]:
        """Latest dbt test results and model lineage (from dbt's run_results.json and manifest.json)."""
        return operations().dbt()

    @app.get("/api/operations/freshness", responses=ERRORS, tags=["operations"])
    def operations_freshness() -> dict[str, Any]:
        """Rows, latest interval, missing hours and status per geography and domain in the Gold models."""
        return operations().freshness()

    @app.get("/api/operations/sources", tags=["operations"])
    def operations_sources() -> list[dict[str, str]]:
        """Source and licence register."""
        return operations().sources()

    # --------------------------------------------------------- evaluation
    @app.get("/api/evaluations", tags=["evaluation"])
    def latest_evaluations() -> list[dict[str, Any]]:
        """The latest Scenario Lab run for each generator (rule-based, and agent runs made from the CLI)."""
        return evaluations().latest()

    @app.get("/api/evaluations/history", tags=["evaluation"])
    def evaluation_history(limit: int = Query(default=30, ge=1, le=200)) -> list[dict[str, Any]]:
        """Past Scenario Lab runs (all generators), newest first, with the checks each failing case missed."""
        return evaluations().history(limit)

    @app.post("/api/evaluations/run", tags=["evaluation"])
    def run_evaluation() -> dict[str, Any]:
        """Run the deterministic rule-based suite now (a few seconds). Agent runs are CLI-only because each case
        calls the local model several times: `python -m eurogrid.evaluation --generator agent`."""
        payload = run_suite("rule-based")
        evaluations().save(payload)
        return payload

    # ------------------------------------------------------------- replay
    @app.post("/api/replays", responses=ERRORS, tags=["replay"])
    def create_replay(request: ReplayRequest) -> dict[str, Any]:
        """Replay a saved anomaly's local day(s) against a stored household simulation: actual prices and weather,
        each configuration under the household's tariff and an illustrative dynamic tariff, versus usual prices."""
        return replays().run(request.anomaly_id, request.simulation_id)

    @app.get("/api/replays", tags=["replay"])
    def list_replays(limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
        return replays().list(limit)

    @app.get("/api/replays/{replay_id}", responses=ERRORS, tags=["replay"])
    def get_replay(replay_id: str = PathParam(pattern=r"^rpl_[0-9a-f]{16}$")) -> dict[str, Any]:
        return replays().get(replay_id)

    @app.get("/api/replays/{replay_id}/export", responses=ERRORS, tags=["replay"])
    def export_replay(replay_id: str = PathParam(pattern=r"^rpl_[0-9a-f]{16}$"),
                      format: Literal["markdown", "json"] = Query(default="markdown")) -> Any:
        """The event-to-household report as Markdown, or the full result as JSON."""
        service = replays()
        if format == "json":
            return JSONResponse(service.get(replay_id),
                                headers={"Content-Disposition": f'attachment; filename="{replay_id}.json"'})
        return PlainTextResponse(service.markdown(replay_id), media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{replay_id}.md"'})

    # -------------------------------------------------------------- chaos
    @app.get("/api/chaos/faults", tags=["chaos"])
    def chaos_faults() -> dict[str, dict[str, str]]:
        """The faults chaos mode can inject."""
        return FAULTS

    @app.post("/api/chaos/runs", responses=ERRORS, tags=["chaos"])
    def create_chaos_run(request: ChaosRequest) -> dict[str, Any]:
        """Copy a slice of the data around an anomaly, inject the fault into the copy, re-run dbt build/tests, the
        quality gate and the investigation, and compare with a clean copy. Takes ~30 s (more with the agent)."""
        event = anomalies().get(request.anomaly_id)
        agent = None
        if request.include_agent:
            from eurogrid.agent import run_investigation

            llm = (llm_factory or default_llm_factory)(request.model)

            def agent(event_dict, tools, store, out_dir):
                return run_investigation(event_dict["anomaly_id"], tools, store, llm, out_dir)["report"]
        try:
            result = run_chaos(request.fault, event, db_path, data_root / "chaos", dbt_runner, agent)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        chaos_store().save(result)
        return result

    @app.get("/api/chaos/runs", tags=["chaos"])
    def list_chaos_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
        return chaos_store().list(limit)

    @app.get("/api/chaos/runs/{run_id}", responses=ERRORS, tags=["chaos"])
    def get_chaos_run(run_id: str = PathParam(pattern=r"^chs_[0-9a-f]{16}$")) -> dict[str, Any]:
        result = chaos_store().get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Unknown chaos run {run_id}")
        return result

    return app


app = create_app()

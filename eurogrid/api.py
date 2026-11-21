"""EuroGrid HTTP API (Phase 5 market overview + Phase 8 service layer).

Route handlers stay thin: they validate inputs with Pydantic/Query rules, call a
service module, and return a typed response. All database and calculation work
lives in the services:

* ``MarketService``      – coverage, overview, market series, price history
* ``household_service``  – Eurostat household price references
* ``AnomalyService``     – anomaly detection, investigations, evidence, approved tools
* ``SimulationService``  – household PV/battery simulations

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
from fastapi.responses import JSONResponse

from eurogrid.anomaly_service import MAX_PAGE_SIZE, AnomalyService
from eurogrid.api_models import (AnomalyEventModel, AnomalyPage, ApprovedZone, CoverageResponse, DetectRequest,
                                 DetectResponse, ErrorResponse, Health, InvestigationRequest, MarketSeriesResponse,
                                 SimulationHourPage, SimulationPage, SimulationRecord, SimulationResponse, ToolInfo,
                                 ToolRequest)
from eurogrid.household_service import household_benchmark, household_price_history
from eurogrid.investigation import InvestigationReport
from eurogrid.investigation_tools import APPROVED_TOOLS, Evidence, InvestigationTools, ToolResult
from eurogrid.market_service import MarketDataError, MarketService
from eurogrid.service_errors import ServiceError
from eurogrid.simulation import PVProvider
from eurogrid.simulation_models import HouseholdScenario, SystemConfig, default_systems
from eurogrid.simulation_service import MAX_HOURLY_PAGE, SimulationService, default_provider_factory

API_VERSION = "0.8.0"
EUROSTAT_GEOS = {"EU27_2020", "DE", "FR", "ES", "PL", "NL", "UK"}
ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}
TAGS = [
    {"name": "markets", "description": "Coverage, overview and bounded market series from tested Gold models."},
    {"name": "household prices", "description": "Eurostat half-yearly household electricity price references."},
    {"name": "anomalies", "description": "Robust comparable-hour price anomaly detection (Phase 6)."},
    {"name": "investigations", "description": "Rule-based investigation reports and their evidence records."},
    {"name": "tools", "description": "The approved, read-only investigation tools. No SQL input is accepted."},
    {"name": "simulations", "description": "Household PV and battery simulations (Phase 7)."},
]


def create_app(database_path: Path | None = None, data_dir: Path | None = None,
               pv_provider_factory: Callable[[str], PVProvider] | None = None) -> FastAPI:
    """Create the API app. Paths and the PV provider are injectable for tests."""
    db_path = database_path or Path(os.environ.get("EUROGRID_DB_PATH", "data/eurogrid.duckdb"))
    data_root = data_dir or Path(os.environ.get("EUROGRID_DATA_DIR", "data"))
    app = FastAPI(title="EuroGrid Intelligence API", version=API_VERSION, openapi_tags=TAGS,
                  description="Read-only market analytics, deterministic anomaly investigations and household "
                              "PV/battery simulations backed by tested dbt Gold models.")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])

    def anomalies() -> AnomalyService:
        return AnomalyService(db_path, data_root / "metadata" / "investigations.sqlite")

    def simulations() -> SimulationService:
        return SimulationService(db_path, data_root / "metadata" / "simulations.sqlite",
                                 pv_provider_factory or default_provider_factory(data_root))

    @app.exception_handler(ServiceError)
    def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    # ------------------------------------------------------------ markets
    @app.get("/api/health", response_model=Health, tags=["markets"])
    def health() -> dict[str, str]:
        """Confirm the API process is responding without querying market data."""
        return {"status": "ok", "version": API_VERSION}

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
    def create_investigation(request: InvestigationRequest) -> InvestigationReport:
        """Run the deterministic, rule-based investigation for one saved anomaly."""
        return anomalies().investigate(request.anomaly_id)

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

    return app


app = create_app()

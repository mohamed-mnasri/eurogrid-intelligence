"""Minimal FastAPI read API used by the Phase 5 market overview frontend."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from eurogrid.market_service import MarketDataError, MarketService
from eurogrid.household_service import household_benchmark


def create_app(database_path: Path | None = None) -> FastAPI:
    """Create the API app; accepting a database path makes routes easy to test."""
    db_path = database_path or Path(os.environ.get("EUROGRID_DB_PATH", "data/eurogrid.duckdb"))
    app = FastAPI(title="EuroGrid Market API", version="0.5.0",
                  description="Read-only market overview backed by tested dbt Gold models.")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_credentials=False, allow_methods=["GET"], allow_headers=["*"])

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Confirm the API process is responding without querying market data."""
        return {"status": "ok"}

    @app.get("/api/coverage")
    def coverage() -> dict[str, Any]:
        """List the zones discovered in Gold, so the UI does not invent options."""
        try:
            return {"markets": MarketService(db_path).coverage()}
        except MarketDataError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/household-benchmark")
    def household_reference(geo: str = Query(min_length=2, max_length=16)) -> dict[str, Any]:
        """Return Eurostat's latest tax-inclusive household electricity price."""
        allowed = {"EU27_2020", "DE", "FR", "ES", "PL", "NL", "UK"}
        if geo not in allowed:
            raise HTTPException(status_code=404, detail="No Eurostat benchmark configured for this geography")
        try:
            return household_benchmark(geo)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Eurostat benchmark data is unavailable: {exc}") from exc

    @app.get("/api/overview")
    def overview(
        bidding_zone: str = Query(min_length=2, max_length=32),
        start_date: date = Query(),
        end_date: date = Query(),
    ) -> dict[str, Any]:
        """Return a bounded zone/date payload for price, mix, demand, flow and weather charts."""
        try:
            return MarketService(db_path).overview(bidding_zone, start_date, end_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MarketDataError as exc:
            status = 404 if str(exc).startswith(("No ingested price coverage", "No ingested data")) else 503
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    return app


app = create_app()

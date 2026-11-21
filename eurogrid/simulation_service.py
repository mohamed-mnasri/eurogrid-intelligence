"""Phase 8 service for household simulations.

Runs a validated :class:`HouseholdScenario`, stores the summary and the hourly
results in ``data/metadata/simulations.sqlite``, and serves bounded pages of
hourly rows. The simulation ID is a hash of the scenario and engine version, so
submitting the same scenario again returns the same ID.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from eurogrid.ingestion import IngestionStore
from eurogrid.investigation_tools import InvestigationTools, ToolError
from eurogrid.pv import PVDataError, PVGISProvider, WeatherModelProvider
from eurogrid.service_errors import InvalidRequestError, NotFoundError, UnavailableError
from eurogrid.simulation import PVProvider, ScenarioResult, SimulationError, run_scenario
from eurogrid.simulation_models import ASSUMPTIONS_VERSION, DynamicTariff, HouseholdScenario

UTC = timezone.utc
MAX_HOURLY_PAGE = 744   # 31 days
HOURLY_FIELDS = ("load_kwh", "pv_kwh", "solar_direct_kwh", "battery_charge_kwh", "battery_discharge_kwh",
                 "grid_import_kwh", "grid_export_kwh", "battery_loss_kwh", "soc_end_kwh")


def default_provider_factory(data_dir: Path) -> Callable[[str], PVProvider]:
    store = IngestionStore(data_dir)
    return lambda source: PVGISProvider(store) if source == "pvgis" else WeatherModelProvider(store)


class SimulationService:
    def __init__(self, database_path: Path, store_path: Path,
                 provider_factory: Callable[[str], PVProvider]) -> None:
        self.database_path = database_path
        self.store_path = store_path
        self.provider_factory = provider_factory
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS simulation_run (
                simulation_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL, created_at TEXT NOT NULL,
                scenario TEXT NOT NULL, result TEXT NOT NULL, hourly TEXT NOT NULL)""")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.store_path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def simulation_id(scenario: HouseholdScenario) -> str:
        identity = json.dumps({"scenario": scenario.model_dump(mode="json"), "engine": ASSUMPTIONS_VERSION},
                              sort_keys=True)
        return "sim_" + hashlib.sha256(identity.encode()).hexdigest()[:16]

    def _wholesale(self, scenario: HouseholdScenario) -> dict[datetime, float] | None:
        if not isinstance(scenario.tariff, DynamicTariff):
            return None
        year = scenario.simulation_year
        try:
            hours, _ = InvestigationTools(self.database_path).price_hours(
                scenario.tariff.bidding_zone, datetime(year, 1, 1, tzinfo=UTC), datetime(year + 1, 1, 1, tzinfo=UTC))
        except ToolError as exc:
            raise InvalidRequestError(str(exc)) from exc
        return hours

    def run(self, scenario: HouseholdScenario) -> tuple[str, ScenarioResult]:
        try:
            result, runs = run_scenario(scenario, self.provider_factory(scenario.pv_source), self._wholesale(scenario))
        except SimulationError as exc:
            raise InvalidRequestError(str(exc)) from exc
        except PVDataError as exc:
            raise InvalidRequestError(str(exc)) from exc
        except (InvalidRequestError, NotFoundError):
            raise
        except Exception as exc:  # network/provider failures
            raise UnavailableError(f"PV or weather source is unavailable: {exc}") from exc
        simulation_id = self.simulation_id(scenario)
        hourly = {}
        for system_id, run in runs.items():
            columns = {field: [round(getattr(hour, field), 5) for hour in run.balance] for field in HOURLY_FIELDS}
            columns["import_price_eur_per_kwh"] = [round(price, 5) for price in run.import_prices]
            columns["export_price_eur_per_kwh"] = [round(price, 5) for price in run.export_prices]
            hourly[system_id] = columns
        hourly["_hours"] = [hour.isoformat() for hour in next(iter(runs.values())).hours]
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO simulation_run VALUES (?,?,?,?,?,?)",
                       (simulation_id, scenario.scenario_id, datetime.now(UTC).isoformat(),
                        scenario.model_dump_json(), result.model_dump_json(), json.dumps(hourly)))
        return simulation_id, result

    def _row(self, simulation_id: str, columns: str):
        with self._db() as db:
            row = db.execute(f"SELECT {columns} FROM simulation_run WHERE simulation_id=?", [simulation_id]).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown simulation {simulation_id}")
        return row

    def get(self, simulation_id: str) -> dict[str, Any]:
        scenario, result, created = self._row(simulation_id, "scenario, result, created_at")
        return {"simulation_id": simulation_id, "created_at": created, "scenario": json.loads(scenario),
                "result": json.loads(result)}

    def list(self, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        with self._db() as db:
            total = db.execute("SELECT count(*) FROM simulation_run").fetchone()[0]
            rows = db.execute("""SELECT simulation_id, scenario_id, created_at FROM simulation_run
                ORDER BY created_at DESC LIMIT ? OFFSET ?""", [limit, offset]).fetchall()
        return [{"simulation_id": a, "scenario_id": b, "created_at": c} for a, b, c in rows], total

    def hourly(self, simulation_id: str, system_id: str, offset: int, limit: int) -> dict[str, Any]:
        if not 1 <= limit <= MAX_HOURLY_PAGE:
            raise InvalidRequestError(f"limit must be between 1 and {MAX_HOURLY_PAGE}")
        if offset < 0:
            raise InvalidRequestError("offset cannot be negative")
        hourly = json.loads(self._row(simulation_id, "hourly")[0])
        if system_id not in hourly or system_id == "_hours":
            raise NotFoundError(f"System {system_id} is not part of simulation {simulation_id}")
        hours = hourly["_hours"]
        columns = hourly[system_id]
        end = min(offset + limit, len(hours))
        items = [{"interval_start_utc": hours[index], **{name: values[index] for name, values in columns.items()}}
                 for index in range(offset, end)]
        return {"simulation_id": simulation_id, "system_id": system_id, "total": len(hours), "offset": offset,
                "limit": limit, "next_offset": end if end < len(hours) else None, "unit": "kWh per hour; prices EUR/kWh",
                "items": items}

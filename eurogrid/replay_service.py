"""Phase 11 service: replay a saved anomaly against a stored household simulation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import duckdb

from eurogrid.ingestion import IngestionStore
from eurogrid.investigation import CaseStore
from eurogrid.investigation_tools import InvestigationTools
from eurogrid.pv import PVDataError, WeatherModelProvider
from eurogrid.replay import ReplayError, ReplayStore, WindowPVProvider, render_replay_markdown, run_replay
from eurogrid.service_errors import InvalidRequestError, NotFoundError, UnavailableError
from eurogrid.simulation import SimulationError
from eurogrid.simulation_models import HouseholdScenario
from eurogrid.simulation_service import SimulationService


def default_window_provider(data_dir: Path) -> Callable[[], WindowPVProvider]:
    store = IngestionStore(data_dir)
    return lambda: WeatherModelProvider(store)


class ReplayService:
    def __init__(self, database_path: Path, data_dir: Path, simulations: SimulationService,
                 provider_factory: Callable[[], WindowPVProvider] | None = None) -> None:
        self.tools = InvestigationTools(database_path)
        self.cases = CaseStore(data_dir / "metadata" / "investigations.sqlite")
        self.store = ReplayStore(data_dir / "metadata" / "replays.sqlite")
        self.simulations = simulations
        self.provider_factory = provider_factory or default_window_provider(data_dir)

    def run(self, anomaly_id: str, simulation_id: str) -> dict[str, Any]:
        event = self.cases.get_event(anomaly_id)
        if event is None:
            raise NotFoundError(f"Unknown anomaly {anomaly_id}; run detection first")
        record = self.simulations.get(simulation_id)   # raises NotFoundError
        scenario = HouseholdScenario.model_validate(record["scenario"])
        try:
            result = run_replay(event, scenario, simulation_id, self.tools, self.cases, self.provider_factory())
        except (ReplayError, SimulationError, PVDataError) as exc:
            raise InvalidRequestError(str(exc)) from exc
        except duckdb.Error as exc:
            raise UnavailableError(f"Gold models are missing or failed to query: {exc}") from exc
        except (InvalidRequestError, NotFoundError):
            raise
        except Exception as exc:   # weather provider / network
            raise UnavailableError(f"Weather source is unavailable for the replay: {exc}") from exc
        self.store.save(result)
        return result.model_dump()

    def get(self, replay_id: str) -> dict[str, Any]:
        result = self.store.get(replay_id)
        if result is None:
            raise NotFoundError(f"Unknown replay {replay_id}")
        return result

    def list(self, limit: int) -> list[dict[str, Any]]:
        return self.store.list(limit)

    def markdown(self, replay_id: str) -> str:
        return render_replay_markdown(self.get(replay_id))

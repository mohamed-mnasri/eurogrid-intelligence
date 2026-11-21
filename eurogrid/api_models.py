"""Phase 8 Pydantic request and response models for the HTTP API.

Engine models (investigation reports, evidence, tool results, scenario inputs
and results) are reused from their modules so the API and the engines cannot
drift apart. Models here add pagination envelopes and request bodies.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eurogrid.anomaly import DetectorConfig
from eurogrid.investigation_tools import MAX_TOOL_DAYS
from eurogrid.simulation import ScenarioResult


class ErrorResponse(BaseModel):
    """Every error uses FastAPI's ``detail`` field so the frontend can show one message."""

    detail: str | list[Any]


class Market(BaseModel):
    model_config = ConfigDict(extra="allow")
    country_code: str
    bidding_zone: str
    market_name: str
    is_aggregate: bool
    has_prices: bool
    first_source_date: str | None
    last_source_date: str | None
    source_timezone: str


class CoverageResponse(BaseModel):
    markets: list[Market]


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class MarketSeriesResponse(BaseModel):
    bidding_zone: str
    measure: str
    unit: str
    model: str
    window: dict[str, Any]
    total: int
    offset: int
    limit: int
    next_offset: int | None
    items: list[dict[str, Any]]
    lineage: str
    source_trace: list[dict[str, Any]]


class ApprovedZone(BaseModel):
    bidding_zone: str
    country_code: str
    timezone: str
    first_interval_utc: str | None
    last_interval_utc: str | None


class AnomalyEventModel(BaseModel):
    anomaly_id: str
    geography: str
    metric: str
    unit: str
    direction: Literal["spike", "dip"]
    start_utc: str
    end_utc: str
    hour_count: int
    peak_interval_utc: str
    observed_value: float
    baseline_median: float
    deviation: float
    max_abs_score: float
    mean_observed_value: float
    mean_baseline_median: float
    detector_version: str
    detector_config: dict[str, Any]
    source_ids: list[str]


class AnomalyPage(BaseModel):
    total: int
    offset: int
    limit: int
    next_offset: int | None
    items: list[AnomalyEventModel]


class DetectRequest(BaseModel):
    """Run detection for one zone over at most 366 days."""

    bidding_zone: str = Field(min_length=2, max_length=16, examples=["DE-LU"])
    start_date: date
    end_date: date
    window_days: int = Field(default=DetectorConfig.window_days, ge=1, le=365)
    min_observations: int = Field(default=DetectorConfig.min_observations, ge=3, le=500)
    threshold: float = Field(default=DetectorConfig.threshold, gt=0, le=100)
    mad_floor: float = Field(default=DetectorConfig.mad_floor, ge=0, le=1000)

    @model_validator(mode="after")
    def _range(self) -> "DetectRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days + 1 > 366:
            raise ValueError("detection is limited to 366 days per request")
        return self

    def config(self) -> DetectorConfig:
        return DetectorConfig(window_days=self.window_days, min_observations=self.min_observations,
                              threshold=self.threshold, mad_floor=self.mad_floor)


class DetectResponse(BaseModel):
    bidding_zone: str
    start_date: date
    end_date: date
    detector_config: dict[str, Any]
    detected: int
    events: list[AnomalyEventModel]


class InvestigationRequest(BaseModel):
    anomaly_id: str = Field(pattern=r"^anm_[0-9a-f]{16}$")
    generator: Literal["rule-based", "agent"] = Field(
        default="rule-based", description="rule-based (deterministic) or agent (Phase 9 LangGraph + local Ollama model)")
    model: str = Field(default="qwen3.5:4b", pattern=r"^[a-zA-Z0-9._:-]{1,64}$")
    max_tool_calls: int = Field(default=6, ge=1, le=10)


class ToolRequest(BaseModel):
    """Inputs for one approved tool call (UTC, at most 31 days)."""

    bidding_zone: str = Field(min_length=2, max_length=16, examples=["DE-LU"])
    start_utc: datetime = Field(examples=["2026-09-14T15:00:00Z"])
    end_utc: datetime = Field(examples=["2026-09-14T19:00:00Z"])
    variables: list[Literal["temperature", "wind_speed", "shortwave_radiation"]] | None = None
    required_domains: list[Literal["price", "generation", "load", "cross_border_flows", "weather", "outages"]] | None = None

    @model_validator(mode="after")
    def _window(self) -> "ToolRequest":
        if self.start_utc.tzinfo is None or self.end_utc.tzinfo is None:
            raise ValueError("start_utc and end_utc must include a timezone, e.g. 'Z'")
        if self.end_utc <= self.start_utc:
            raise ValueError("end_utc must be after start_utc")
        if (self.end_utc - self.start_utc).total_seconds() > MAX_TOOL_DAYS * 86400:
            raise ValueError(f"tool windows are limited to {MAX_TOOL_DAYS} days")
        return self

    def options(self) -> dict[str, Any]:
        return {name: value for name, value in (("variables", self.variables),
                                                ("required_domains", self.required_domains)) if value}


class ToolInfo(BaseModel):
    name: str
    description: str


class SimulationResponse(BaseModel):
    simulation_id: str
    result: ScenarioResult


class SimulationRecord(BaseModel):
    simulation_id: str
    created_at: str
    scenario: dict[str, Any]
    result: ScenarioResult


class SimulationSummary(BaseModel):
    simulation_id: str
    scenario_id: str
    created_at: str


class SimulationPage(BaseModel):
    total: int
    offset: int
    limit: int
    next_offset: int | None
    items: list[SimulationSummary]


class SimulationHourPage(BaseModel):
    simulation_id: str
    system_id: str
    total: int
    offset: int
    limit: int
    next_offset: int | None
    unit: str
    items: list[dict[str, Any]]

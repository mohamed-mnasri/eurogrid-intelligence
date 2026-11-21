"""Phase 7 household scenario schema and input validation (Pydantic).

A scenario describes one household (location, consumption, tariff) and one or
more system configurations (PV, optional battery, costs) to compare. Every
assumption is an explicit, editable field; defaults are illustrative and are
labelled as such in results.

Units: energy kWh, power kW, prices EUR/kWh (retail) or EUR/MWh (wholesale),
angles in degrees. PV azimuth follows the PVGIS ``aspect`` convention:
0 = south, -90 = east, 90 = west.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

ASSUMPTIONS_VERSION = "sim-v1"
SLUG = r"^[a-z0-9][a-z0-9_-]{0,47}$"


class Location(BaseModel):
    """Household location; Europe-only bounds keep requests within source coverage."""

    latitude: float = Field(ge=34, le=72)
    longitude: float = Field(ge=-25, le=45)
    timezone: str = "Europe/Berlin"
    label: str = Field(default="", max_length=80)
    approximate: bool = False

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value


class PVSystem(BaseModel):
    """Fixed-mount PV array."""

    peak_kw: float = Field(gt=0, le=50)
    tilt_deg: float = Field(default=35, ge=0, le=90)
    azimuth_deg: float = Field(default=0, ge=-180, le=180, description="0 = south, -90 = east, 90 = west")
    system_loss_pct: float = Field(default=14, ge=0, le=50)
    annual_degradation_pct: float = Field(default=0.5, ge=0, le=3)


class BatterySystem(BaseModel):
    """Home battery; charges only from PV surplus in this release (no grid charging)."""

    usable_capacity_kwh: float = Field(gt=0, le=100)
    min_soc_pct: float = Field(default=10, ge=0, lt=100)
    max_soc_pct: float = Field(default=100, gt=0, le=100)
    initial_soc_pct: float = Field(default=50, ge=0, le=100)
    max_charge_kw: float = Field(gt=0, le=50)
    max_discharge_kw: float = Field(gt=0, le=50)
    charge_efficiency: float = Field(default=0.95, gt=0, le=1)
    discharge_efficiency: float = Field(default=0.95, gt=0, le=1)
    replacement_year: int | None = Field(default=None, ge=1, le=40)
    replacement_cost_eur: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _soc_range(self) -> "BatterySystem":
        if self.min_soc_pct >= self.max_soc_pct:
            raise ValueError("min_soc_pct must be below max_soc_pct")
        if not self.min_soc_pct <= self.initial_soc_pct <= self.max_soc_pct:
            raise ValueError("initial_soc_pct must be between min_soc_pct and max_soc_pct")
        return self

    @property
    def min_soc_kwh(self) -> float:
        return self.usable_capacity_kwh * self.min_soc_pct / 100

    @property
    def max_soc_kwh(self) -> float:
        return self.usable_capacity_kwh * self.max_soc_pct / 100

    @property
    def initial_soc_kwh(self) -> float:
        return self.usable_capacity_kwh * self.initial_soc_pct / 100


class SystemConfig(BaseModel):
    """One configuration to compare, with its costs."""

    system_id: str = Field(pattern=SLUG)
    label: str = Field(max_length=80)
    pv: PVSystem
    battery: BatterySystem | None = None
    capex_eur: float = Field(ge=0, le=500_000)
    annual_maintenance_eur: float = Field(default=0, ge=0, le=20_000)
    cost_source: str = Field(default="illustrative default; replace with an installer quotation", max_length=200)


class FixedTariff(BaseModel):
    """The household's own contract: import price does not follow wholesale prices."""

    kind: Literal["fixed"] = "fixed"
    import_eur_per_kwh: float = Field(gt=0, le=2)
    export_eur_per_kwh: float = Field(default=0, ge=0, le=2)
    standing_charge_eur_per_year: float = Field(default=0, ge=0, le=5_000)


class DynamicTariff(BaseModel):
    """Illustrative dynamic tariff built from wholesale prices plus explicit components.

    retail = (max(wholesale, 0) if floor else wholesale) / 1000 + markup + grid + levies, then × (1 + VAT).
    Export compensation is a separate explicit rate; it is not inferred from wholesale.
    """

    kind: Literal["dynamic"] = "dynamic"
    bidding_zone: str = Field(min_length=2, max_length=16)
    supplier_markup_eur_per_kwh: float = Field(default=0.02, ge=0, le=1)
    grid_charges_eur_per_kwh: float = Field(default=0.10, ge=0, le=1)
    taxes_levies_eur_per_kwh: float = Field(default=0.05, ge=0, le=1)
    vat_pct: float = Field(default=19, ge=0, le=30)
    floor_energy_price_at_zero: bool = False
    export_eur_per_kwh: float = Field(default=0, ge=0, le=2)
    standing_charge_eur_per_year: float = Field(default=0, ge=0, le=5_000)


Tariff = Annotated[FixedTariff | DynamicTariff, Field(discriminator="kind")]


class FinanceAssumptions(BaseModel):
    horizon_years: int = Field(default=20, ge=1, le=40)
    discount_rate_pct: float = Field(default=4, ge=0, le=20)
    import_price_escalation_pct: float = Field(default=2, ge=-5, le=15)
    export_price_escalation_pct: float = Field(default=0, ge=-5, le=15)


class HouseholdScenario(BaseModel):
    """A complete, validated simulation request."""

    scenario_id: str = Field(pattern=SLUG)
    location: Location
    annual_consumption_kwh: float = Field(ge=500, le=50_000)
    hourly_load_kwh: list[float] | None = Field(
        default=None, description="Optional measured hourly load for the simulation year (8760/8784 values).")
    tariff: Tariff
    systems: list[SystemConfig] = Field(min_length=1, max_length=6)
    finance: FinanceAssumptions = FinanceAssumptions()
    simulation_year: int = Field(default=2023, ge=2005, le=2030)
    pv_source: Literal["pvgis", "weather_model"] = "pvgis"

    @model_validator(mode="after")
    def _checks(self) -> "HouseholdScenario":
        ids = [system.system_id for system in self.systems]
        if len(ids) != len(set(ids)):
            raise ValueError("system_id values must be unique")
        if self.hourly_load_kwh is not None:
            if any(value < 0 for value in self.hourly_load_kwh):
                raise ValueError("hourly_load_kwh cannot contain negative values")
            expected = (datetime(self.simulation_year + 1, 1, 1) - datetime(self.simulation_year, 1, 1)).days * 24
            if len(self.hourly_load_kwh) != expected:
                raise ValueError(f"hourly_load_kwh must have {expected} values for {self.simulation_year}")
        if self.pv_source == "pvgis" and self.simulation_year > 2023:
            raise ValueError("PVGIS hourly series currently end in 2023; use pv_source='weather_model' for later years")
        return self


def default_systems() -> list[SystemConfig]:
    """Three illustrative configurations (handbook section 2.3). Costs are placeholders."""
    return [
        SystemConfig(system_id="pv-4kw", label="Solar PV 4 kWp", pv=PVSystem(peak_kw=4),
                     capex_eur=6_000, annual_maintenance_eur=100),
        SystemConfig(system_id="pv-4kw-bat-5kwh", label="Solar PV 4 kWp + 5 kWh battery", pv=PVSystem(peak_kw=4),
                     battery=BatterySystem(usable_capacity_kwh=5, max_charge_kw=2.5, max_discharge_kw=2.5,
                                           replacement_year=12, replacement_cost_eur=2_500),
                     capex_eur=10_000, annual_maintenance_eur=130),
        SystemConfig(system_id="pv-8kw-bat-10kwh", label="Solar PV 8 kWp + 10 kWh battery", pv=PVSystem(peak_kw=8),
                     battery=BatterySystem(usable_capacity_kwh=10, max_charge_kw=5, max_discharge_kw=5,
                                           replacement_year=12, replacement_cost_eur=4_500),
                     capex_eur=18_000, annual_maintenance_eur=180),
    ]

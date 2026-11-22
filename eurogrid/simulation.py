"""Phase 7 household energy-balance and investment simulation engine.

Energy (handbook 10.3–10.4), per hour, all in kWh::

    solar_direct   = min(pv, load)
    surplus        = pv − solar_direct          shortfall = load − solar_direct
    battery charge (input)   ≤ surplus, ≤ max_charge_kw × 1h, ≤ (max_soc − soc) / η_charge
    battery discharge (out)  ≤ shortfall, ≤ max_discharge_kw × 1h, ≤ (soc − min_soc) × η_discharge
    soc_next       = soc + charge × η_charge − discharge / η_discharge
    grid_import    = shortfall − discharge      grid_export = surplus − charge
    battery_loss   = charge × (1 − η_charge) + discharge × (1/η_discharge − 1)

The battery never charges and discharges in the same hour, charges only from PV
surplus and only discharges to cover household load. Every hour is checked:

    pv + grid_import + soc_before  =  load + grid_export + battery_loss + soc_after

and the run fails if any hour is off by more than ``ENERGY_TOLERANCE_KWH``.

Money (handbook 10.5–10.6): bills are hourly import cost − export revenue plus
the standing charge. Annual net savings = bill without the system − bill with
it − maintenance. Year *t* savings apply PV degradation and price escalation to
the year-1 avoided-import and export-revenue parts separately (a documented
linear approximation instead of re-simulating every year). NPV discounts the
yearly net cash flows at the chosen rate and subtracts CAPEX.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol

from pydantic import BaseModel

from eurogrid.load_profile import PROFILE_SOURCE, synthetic_load, year_hours
from eurogrid.simulation_models import (ASSUMPTIONS_VERSION, BatterySystem, DynamicTariff, FixedTariff,
                                        FinanceAssumptions, HouseholdScenario, Location, PVSystem, SystemConfig)

ENERGY_TOLERANCE_KWH = 1e-6


class SimulationError(Exception):
    """Raised when inputs cannot be simulated honestly (e.g. missing prices)."""


# ---------------------------------------------------------------- energy layer
@dataclass
class HourBalance:
    load_kwh: float
    pv_kwh: float
    solar_direct_kwh: float
    battery_charge_kwh: float
    battery_discharge_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float
    battery_loss_kwh: float
    soc_start_kwh: float
    soc_end_kwh: float


def simulate_energy(load: list[float], pv: list[float], battery: BatterySystem | None = None) -> list[HourBalance]:
    """Run the hourly energy balance and verify conservation for every hour."""
    if len(load) != len(pv):
        raise SimulationError("load and PV series must cover the same hours")
    if any(value < 0 for value in load) or any(value < 0 for value in pv):
        raise SimulationError("load and PV must be non-negative")
    soc = battery.initial_soc_kwh if battery else 0.0
    hours = []
    for load_kwh, pv_kwh in zip(load, pv):
        direct = min(pv_kwh, load_kwh)
        surplus = pv_kwh - direct
        shortfall = load_kwh - direct
        charge = discharge = 0.0
        soc_start = soc
        if battery is not None:
            if surplus > 0:
                room_input = max(0.0, (battery.max_soc_kwh - soc) / battery.charge_efficiency)
                charge = min(surplus, battery.max_charge_kw, room_input)
            elif shortfall > 0:
                deliverable = max(0.0, (soc - battery.min_soc_kwh) * battery.discharge_efficiency)
                discharge = min(shortfall, battery.max_discharge_kw, deliverable)
            soc = soc + charge * battery.charge_efficiency - discharge / battery.discharge_efficiency
            # Remove floating-point dust at the limits; never allow the limits to be crossed.
            soc = min(max(soc, battery.min_soc_kwh), battery.max_soc_kwh)
            loss = charge * (1 - battery.charge_efficiency) + discharge * (1 / battery.discharge_efficiency - 1)
        else:
            loss = 0.0
        hour = HourBalance(load_kwh, pv_kwh, direct, charge, discharge, shortfall - discharge, surplus - charge,
                           loss, soc_start, soc)
        error = (pv_kwh + hour.grid_import_kwh + soc_start) - (load_kwh + hour.grid_export_kwh + loss + soc)
        if abs(error) > ENERGY_TOLERANCE_KWH:
            raise SimulationError(f"energy balance off by {error:.3e} kWh in hour {len(hours)}")
        hours.append(hour)
    return hours


class EnergySummary(BaseModel):
    load_kwh: float
    pv_kwh: float
    solar_direct_kwh: float
    battery_charge_kwh: float
    battery_discharge_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float
    battery_loss_kwh: float
    battery_soc_change_kwh: float
    self_consumption_pct: float | None
    self_sufficiency_pct: float | None
    battery_equivalent_full_cycles: float | None
    max_hourly_balance_error_kwh: float


def summarize_energy(hours: list[HourBalance], battery: BatterySystem | None) -> EnergySummary:
    def total(field: str) -> float:
        return sum(getattr(hour, field) for hour in hours)

    load, pv, imported, exported = total("load_kwh"), total("pv_kwh"), total("grid_import_kwh"), total("grid_export_kwh")
    max_error = max((abs((h.pv_kwh + h.grid_import_kwh + h.soc_start_kwh)
                         - (h.load_kwh + h.grid_export_kwh + h.battery_loss_kwh + h.soc_end_kwh)) for h in hours),
                    default=0.0)
    soc_change = (hours[-1].soc_end_kwh - hours[0].soc_start_kwh) if hours else 0.0
    return EnergySummary(
        load_kwh=round(load, 3), pv_kwh=round(pv, 3), solar_direct_kwh=round(total("solar_direct_kwh"), 3),
        battery_charge_kwh=round(total("battery_charge_kwh"), 3),
        battery_discharge_kwh=round(total("battery_discharge_kwh"), 3),
        grid_import_kwh=round(imported, 3), grid_export_kwh=round(exported, 3),
        battery_loss_kwh=round(total("battery_loss_kwh"), 3), battery_soc_change_kwh=round(soc_change, 3),
        self_consumption_pct=round((pv - exported) / pv * 100, 2) if pv > 0 else None,
        self_sufficiency_pct=round((load - imported) / load * 100, 2) if load > 0 else None,
        battery_equivalent_full_cycles=(round(total("battery_discharge_kwh") / battery.usable_capacity_kwh, 1)
                                        if battery else None),
        max_hourly_balance_error_kwh=max_error,
    )


# ---------------------------------------------------------------- tariff layer
def tariff_prices(tariff: FixedTariff | DynamicTariff, hours: list[datetime],
                  wholesale_eur_per_mwh: dict[datetime, float] | None) -> tuple[list[float], list[float]]:
    """Hourly (import, export) retail prices in EUR/kWh."""
    if isinstance(tariff, FixedTariff):
        return [tariff.import_eur_per_kwh] * len(hours), [tariff.export_eur_per_kwh] * len(hours)
    if wholesale_eur_per_mwh is None:
        raise SimulationError("a dynamic tariff needs wholesale prices for the simulation year")
    missing = [hour for hour in hours if hour not in wholesale_eur_per_mwh]
    if missing:
        raise SimulationError(f"wholesale prices are missing for {len(missing)} of {len(hours)} hours "
                              f"(first {missing[0].isoformat()}); the dynamic tariff is not estimated for gaps")
    imports = []
    for hour in hours:
        energy = wholesale_eur_per_mwh[hour] / 1000
        if tariff.floor_energy_price_at_zero:
            energy = max(energy, 0.0)
        net = energy + tariff.supplier_markup_eur_per_kwh + tariff.grid_charges_eur_per_kwh + tariff.taxes_levies_eur_per_kwh
        imports.append(net * (1 + tariff.vat_pct / 100))
    return imports, [tariff.export_eur_per_kwh] * len(hours)


@dataclass
class BillBreakdown:
    import_cost: float
    export_revenue: float
    standing_charge: float

    @property
    def total(self) -> float:
        return self.import_cost - self.export_revenue + self.standing_charge


def bill(imports_kwh: list[float], exports_kwh: list[float], import_prices: list[float],
         export_prices: list[float], standing_charge: float) -> BillBreakdown:
    return BillBreakdown(sum(q * p for q, p in zip(imports_kwh, import_prices)),
                         sum(q * p for q, p in zip(exports_kwh, export_prices)), standing_charge)


# ------------------------------------------------------------- finance layer
class CashflowYear(BaseModel):
    year: int
    savings_eur: float
    maintenance_eur: float
    replacement_eur: float
    net_cashflow_eur: float
    cumulative_cashflow_eur: float
    discounted_cashflow_eur: float


class FinancialSummary(BaseModel):
    capex_eur: float
    bill_without_system_eur: float
    bill_with_system_eur: float
    avoided_import_cost_eur: float
    export_revenue_eur: float
    annual_maintenance_eur: float
    annual_net_savings_eur: float
    simple_payback_years: float | None
    payback_note: str
    npv_eur: float
    cashflow: list[CashflowYear]


def financials(capex: float, avoided_import: float, export_revenue: float, maintenance: float,
               bill_without: float, bill_with: float, finance: FinanceAssumptions, degradation_pct: float,
               battery: BatterySystem | None) -> FinancialSummary:
    """Year-1 savings, simple payback, yearly cash flow and NPV."""
    net_year1 = avoided_import + export_revenue - maintenance
    rate = finance.discount_rate_pct / 100
    cumulative = -capex
    npv = -capex
    rows = []
    for year in range(1, finance.horizon_years + 1):
        yield_factor = (1 - degradation_pct / 100) ** (year - 1)
        savings = (avoided_import * (1 + finance.import_price_escalation_pct / 100) ** (year - 1)
                   + export_revenue * (1 + finance.export_price_escalation_pct / 100) ** (year - 1)) * yield_factor
        replacement = battery.replacement_cost_eur if battery and battery.replacement_year == year else 0.0
        net = savings - maintenance - replacement
        discounted = net / (1 + rate) ** year
        cumulative += net
        npv += discounted
        rows.append(CashflowYear(year=year, savings_eur=round(savings, 2), maintenance_eur=round(maintenance, 2),
                                 replacement_eur=round(replacement, 2), net_cashflow_eur=round(net, 2),
                                 cumulative_cashflow_eur=round(cumulative, 2),
                                 discounted_cashflow_eur=round(discounted, 2)))
    if net_year1 > 0:
        payback = capex / net_year1
        note = "CAPEX divided by first-year net savings; ignores discounting, escalation and degradation."
    else:
        payback = None
        note = "No payback: first-year net savings are zero or negative."
    return FinancialSummary(
        capex_eur=round(capex, 2), bill_without_system_eur=round(bill_without, 2),
        bill_with_system_eur=round(bill_with, 2), avoided_import_cost_eur=round(avoided_import, 2),
        export_revenue_eur=round(export_revenue, 2), annual_maintenance_eur=round(maintenance, 2),
        annual_net_savings_eur=round(net_year1, 2), simple_payback_years=None if payback is None else round(payback, 1),
        payback_note=note, npv_eur=round(npv, 2), cashflow=rows)


# ------------------------------------------------------------ scenario layer
class SensitivityCase(BaseModel):
    case: str
    npv_eur: float
    simple_payback_years: float | None
    annual_net_savings_eur: float


class SystemResult(BaseModel):
    system_id: str
    label: str
    pv_peak_kw: float
    battery_kwh: float | None
    cost_source: str
    energy: EnergySummary
    financial: FinancialSummary
    sensitivity: list[SensitivityCase]


class ScenarioResult(BaseModel):
    scenario_id: str
    assumptions_version: str
    simulation_year: int
    hours: int
    load_profile_source: str
    pv_source: dict[str, Any]
    tariff: dict[str, Any]
    tariff_note: str
    systems: list[SystemResult]
    labels: list[str]


@dataclass
class SystemRun:
    """Full-resolution outputs kept beside the summary (for hourly API views)."""
    hours: list[datetime]
    balance: list[HourBalance]
    import_prices: list[float]
    export_prices: list[float]


class PVProvider(Protocol):
    metadata: dict[str, Any]

    def per_kwp(self, location: Location, system: PVSystem, year: int) -> dict[datetime, float]: ...


def _evaluate(system: SystemConfig, load: list[float], pv_per_kwp: list[float], import_prices: list[float],
              export_prices: list[float], standing: float, finance: FinanceAssumptions,
              capex_factor: float = 1.0, import_factor: float = 1.0, export_factor: float = 1.0,
              yield_factor: float = 1.0, degradation_pct: float | None = None):
    pv = [value * system.pv.peak_kw * yield_factor for value in pv_per_kwp]
    balance = simulate_energy(load, pv, system.battery)
    imp = [price * import_factor for price in import_prices]
    exp = [price * export_factor for price in export_prices]
    without = bill(load, [0.0] * len(load), imp, exp, standing)
    with_system = bill([h.grid_import_kwh for h in balance], [h.grid_export_kwh for h in balance], imp, exp, standing)
    money = financials(system.capex_eur * capex_factor, without.import_cost - with_system.import_cost,
                       with_system.export_revenue, system.annual_maintenance_eur, without.total, with_system.total,
                       finance, system.pv.annual_degradation_pct if degradation_pct is None else degradation_pct,
                       system.battery)
    return balance, money


SENSITIVITY_CASES: list[tuple[str, dict[str, Any]]] = [
    ("CAPEX −20%", {"capex_factor": 0.8}), ("CAPEX +20%", {"capex_factor": 1.2}),
    ("Import price −20%", {"import_factor": 0.8}), ("Import price +20%", {"import_factor": 1.2}),
    ("Export rate −50%", {"export_factor": 0.5}), ("Export rate +50%", {"export_factor": 1.5}),
    ("Solar yield −10%", {"yield_factor": 0.9}), ("Solar yield +10%", {"yield_factor": 1.1}),
    ("No degradation", {"degradation_pct": 0.0}), ("Degradation 1%/yr", {"degradation_pct": 1.0}),
    ("Discount rate −2 pp", {"discount_delta": -2.0}), ("Discount rate +2 pp", {"discount_delta": 2.0}),
]


def simulate_scenario(scenario: HouseholdScenario, pv_per_kwp: Callable[[PVSystem], dict[datetime, float]],
                      pv_metadata: dict[str, Any] | None = None,
                      wholesale_eur_per_mwh: dict[datetime, float] | None = None,
                      ) -> tuple[ScenarioResult, dict[str, SystemRun]]:
    """Simulate every system in a scenario; returns summaries and hourly runs."""
    hours = year_hours(scenario.simulation_year)
    if scenario.hourly_load_kwh is not None:
        load = list(scenario.hourly_load_kwh)
        profile_source = "measured hourly load supplied with the scenario"
    else:
        load = synthetic_load(hours, scenario.annual_consumption_kwh, scenario.location.timezone)
        profile_source = PROFILE_SOURCE
    import_prices, export_prices = tariff_prices(scenario.tariff, hours, wholesale_eur_per_mwh)
    standing = scenario.tariff.standing_charge_eur_per_year

    results, runs = [], {}
    for system in scenario.systems:
        series = pv_per_kwp(system.pv)
        missing = [hour for hour in hours if hour not in series]
        if missing:
            raise SimulationError(f"PV series for {system.system_id} is missing {len(missing)} hour(s), "
                                  f"first {missing[0].isoformat()}")
        per_kwp = [series[hour] for hour in hours]
        balance, money = _evaluate(system, load, per_kwp, import_prices, export_prices, standing, scenario.finance)
        sensitivity = []
        for name, change in SENSITIVITY_CASES:
            finance = scenario.finance
            options = dict(change)
            if "discount_delta" in options:
                delta = options.pop("discount_delta")
                finance = finance.model_copy(update={"discount_rate_pct": max(0.0, finance.discount_rate_pct + delta)})
            _, alt = _evaluate(system, load, per_kwp, import_prices, export_prices, standing, finance, **options)
            sensitivity.append(SensitivityCase(case=name, npv_eur=alt.npv_eur, simple_payback_years=alt.simple_payback_years,
                                               annual_net_savings_eur=alt.annual_net_savings_eur))
        results.append(SystemResult(
            system_id=system.system_id, label=system.label, pv_peak_kw=system.pv.peak_kw,
            battery_kwh=system.battery.usable_capacity_kwh if system.battery else None,
            cost_source=system.cost_source, energy=summarize_energy(balance, system.battery),
            financial=money, sensitivity=sensitivity))
        runs[system.system_id] = SystemRun(hours, balance, import_prices, export_prices)

    dynamic = isinstance(scenario.tariff, DynamicTariff)
    labels = ["Simulated estimate, not a measured or guaranteed result."]
    if scenario.hourly_load_kwh is None:
        labels.append("Household load is a synthetic profile scaled to the stated annual consumption.")
    if dynamic:
        labels.append("Dynamic tariff is an illustrative model built from wholesale prices plus the stated components.")
    if any(system.cost_source.startswith("illustrative") for system in scenario.systems):
        labels.append("System costs are illustrative defaults; replace them with an installer quotation.")
    return ScenarioResult(
        scenario_id=scenario.scenario_id, assumptions_version=ASSUMPTIONS_VERSION,
        simulation_year=scenario.simulation_year, hours=len(hours), load_profile_source=profile_source,
        pv_source=pv_metadata or {}, tariff=scenario.tariff.model_dump(),
        tariff_note=("Illustrative dynamic tariff: wholesale day-ahead price plus explicit markup, grid charges, "
                     "taxes/levies and VAT. A negative wholesale price is passed through unless the floor is enabled."
                     if dynamic else "Fixed contract tariff; import price does not follow wholesale prices."),
        systems=results, labels=labels), runs


def run_scenario(scenario: HouseholdScenario, provider: PVProvider,
                 wholesale_eur_per_mwh: dict[datetime, float] | None = None):
    """Convenience wrapper: fetch PV per system through a provider, then simulate."""
    cache: dict[tuple, dict[datetime, float]] = {}

    def pv_series(pv: PVSystem) -> dict[datetime, float]:
        key = (pv.tilt_deg, pv.azimuth_deg, pv.system_loss_pct)
        if key not in cache:
            cache[key] = provider.per_kwp(scenario.location, pv, scenario.simulation_year)
        return cache[key]

    result, runs = simulate_scenario(scenario, pv_series, None, wholesale_eur_per_mwh)
    result.pv_source = dict(provider.metadata)  # filled in by the provider during the fetch
    return result, runs

"""Phase 11 Grid-to-Home event replay (handbook 2.4 and 10.7).

Replays a detected market event against a stored household scenario: the
same household (load profile, location, systems) is run hour by hour through
the event days using the actual day-ahead prices and actual weather of those
days. Each configuration, plus "no system", is costed under:

* the household's own tariff from the scenario (often fixed: the spike then does not reach the bill), and
* an illustrative dynamic tariff on the event's bidding zone (wholesale + stated components),

and compared with the same hours at *usual* wholesale prices (median of the same
UTC hour over the prior 28 days). The difference is the event premium.

This is a historical counterfactual simulation, not a claim about any actual
household bill. Assumptions are returned with every result:

* the window is the event's local calendar day(s); a 24-hour warm-up before it is simulated
  (so the battery's state of charge reflects the previous day) but not reported;
* the battery follows the same self-consumption rule as the planner: it charges from solar surplus and
  discharges to cover load; it is not optimised against prices;
* household load is the scenario's synthetic profile for the event year unless measured load was supplied
  for that year;
* PV is modelled from Open-Meteo weather at the scenario location for exactly those days.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from eurogrid.investigation import CaseStore, investigate
from eurogrid.investigation_tools import InvestigationTools, ToolError
from eurogrid.load_profile import synthetic_load, year_hours
from eurogrid.simulation import simulate_energy, tariff_prices
from eurogrid.simulation_models import DynamicTariff, FixedTariff, HouseholdScenario, Location, PVSystem

UTC = timezone.utc
REPLAY_VERSION = "replay-v1"
USUAL_PRICE_DAYS = 28
MIN_USUAL_OBSERVATIONS = 8
WARMUP_HOURS = 24


class ReplayError(Exception):
    """The replay cannot be computed honestly (missing prices, weather or scenario data)."""


class WindowPVProvider(Protocol):
    metadata: dict[str, Any]

    def per_kwp_window(self, location: Location, system: PVSystem, start_utc: datetime,
                       end_utc: datetime) -> dict[datetime, float]: ...


class TariffCost(BaseModel):
    tariff: str
    cost_actual_eur: float          # import cost − export revenue over the replay window, at actual prices
    cost_usual_eur: float           # same energy flows at usual wholesale prices (equal for a fixed tariff)
    event_premium_eur: float        # actual − usual
    event_hours_cost_actual_eur: float
    event_hours_cost_usual_eur: float


class OptionResult(BaseModel):
    option_id: str
    label: str
    pv_peak_kw: float
    battery_kwh: float | None
    load_kwh: float
    pv_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float
    battery_discharge_kwh: float
    event_hours_import_kwh: float
    event_hours_battery_discharge_kwh: float
    self_sufficiency_pct: float | None
    costs: list[TariffCost]


class ReplayResult(BaseModel):
    replay_id: str
    version: str
    anomaly: dict[str, Any]
    simulation_id: str
    scenario_id: str
    location: dict[str, Any]
    window: dict[str, Any]
    market: dict[str, Any]
    options: list[OptionResult]
    hourly: dict[str, Any]
    tariffs: dict[str, Any]
    assumptions: list[str]
    sources: dict[str, Any]
    labels: list[str]


def usual_prices(history: dict[datetime, float], hours: list[datetime]) -> dict[datetime, float]:
    """Median of the same UTC hour over the prior 28 days, for each replay hour (fails closed)."""
    usual = {}
    for hour in hours:
        values = [history[hour - timedelta(days=offset)] for offset in range(1, USUAL_PRICE_DAYS + 1)
                  if hour - timedelta(days=offset) in history]
        if len(values) < MIN_USUAL_OBSERVATIONS:
            raise ReplayError(f"only {len(values)} comparable prices before {hour.isoformat()}; "
                              f"at least {MIN_USUAL_OBSERVATIONS} are needed for a usual-price reference")
        usual[hour] = median(values)
    return usual


def replay_window(event: dict[str, Any], timezone_name: str) -> tuple[datetime, datetime]:
    """The event's local calendar day(s), as UTC bounds."""
    zone = ZoneInfo(timezone_name)
    start_local = datetime.fromisoformat(event["start_utc"]).astimezone(zone).date()
    end_local = (datetime.fromisoformat(event["end_utc"]) - timedelta(seconds=1)).astimezone(zone).date()
    start = datetime.combine(start_local, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(end_local + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return start, end


def market_summary(event: dict[str, Any], store: CaseStore, tools: InvestigationTools) -> dict[str, Any]:
    """The event's measured market behaviour: the latest saved investigation, or a fresh rule-based one."""
    report = store.latest_report(event["anomaly_id"])
    if report is None:
        built = investigate(event, tools)
        store.save_report(built)
        report = built.model_dump()
    return {"report_id": report["report_id"], "generator": report["generator"], "status": report["status"],
            "observations": [item["statement"] for item in report["observations"]],
            "coinciding": [item["statement"] for item in report["coinciding_relationships"]],
            "plausible": [item["statement"] for item in report["plausible_contributing_factors"]],
            "missing_evidence": report["missing_evidence"],
            "note": "From the investigation report: what was measured and what coincided. No cause is proven."}


def _cost(imports: list[float], exports: list[float], import_prices: list[float], export_prices: list[float],
          mask: list[bool] | None = None) -> float:
    return sum(q * p - e * x for index, (q, p, e, x) in enumerate(zip(imports, import_prices, exports, export_prices))
               if mask is None or mask[index])


def run_replay(event: dict[str, Any], scenario: HouseholdScenario, simulation_id: str, tools: InvestigationTools,
               store: CaseStore, pv_provider: WindowPVProvider) -> ReplayResult:
    zone_name = event["geography"]
    zones = tools.zones()
    if zone_name not in zones:
        raise ReplayError(f"{zone_name} is not an approved bidding zone")
    start, end = replay_window(event, zones[zone_name]["timezone"])
    warm_start = start - timedelta(hours=WARMUP_HOURS)
    hours = [warm_start + timedelta(hours=offset) for offset in range(int((end - warm_start) / timedelta(hours=1)))]
    reported = [hour >= start for hour in hours]
    event_start, event_end = datetime.fromisoformat(event["start_utc"]), datetime.fromisoformat(event["end_utc"])
    in_event = [event_start <= hour < event_end for hour in hours]

    # Prices: actual for the window (fail closed), usual from the prior 28 days.
    try:
        history, price_sources = tools.price_hours(zone_name, warm_start - timedelta(days=USUAL_PRICE_DAYS), end)
    except ToolError as exc:
        raise ReplayError(str(exc)) from exc
    missing = [hour for hour in hours if hour not in history]
    if missing:
        raise ReplayError(f"day-ahead prices are missing for {len(missing)} replay hour(s) (first "
                          f"{missing[0].isoformat()}); the replay is not estimated for gaps")
    actual = {hour: history[hour] for hour in hours}
    usual = usual_prices(history, hours)

    # Load: the scenario household for the event year, sliced to the replay hours.
    years = sorted({hour.year for hour in hours})
    load_by_hour: dict[datetime, float] = {}
    for year in years:
        if scenario.hourly_load_kwh is not None and year == scenario.simulation_year:
            values, source = scenario.hourly_load_kwh, "measured hourly load supplied with the scenario"
        else:
            values = synthetic_load(year_hours(year), scenario.annual_consumption_kwh, scenario.location.timezone)
            source = "synthetic profile scaled to the scenario's annual consumption (event year)"
        load_by_hour.update(zip(year_hours(year), values))
    load = [load_by_hour[hour] for hour in hours]

    # Tariffs: the household's own, and an illustrative dynamic tariff on the event zone.
    dynamic = DynamicTariff(bidding_zone=zone_name, export_eur_per_kwh=scenario.tariff.export_eur_per_kwh)
    own_label = ("Your tariff (fixed)" if isinstance(scenario.tariff, FixedTariff)
                 else f"Your tariff (dynamic, {scenario.tariff.bidding_zone})")
    tariffs = {"own": (own_label, scenario.tariff), "dynamic": (f"Illustrative dynamic tariff ({zone_name})", dynamic)}
    if isinstance(scenario.tariff, DynamicTariff) and scenario.tariff.bidding_zone == zone_name:
        tariffs.pop("dynamic")
    prices = {}
    for key, (_, tariff) in tariffs.items():
        if isinstance(tariff, DynamicTariff) and tariff.bidding_zone != zone_name:
            raise ReplayError(f"the scenario's dynamic tariff is on {tariff.bidding_zone}, not the event zone {zone_name}")
        prices[key] = {"actual": tariff_prices(tariff, hours, actual), "usual": tariff_prices(tariff, hours, usual)}

    # PV per orientation from actual weather for these days.
    pv_cache: dict[tuple, dict[datetime, float]] = {}
    pv_meta: dict[str, Any] = {}
    options: list[OptionResult] = []
    hourly_flows: dict[str, dict[str, list[float]]] = {}
    configs = [("no-system", "No solar or battery", None, None)] + \
              [(system.system_id, system.label, system.pv, system.battery) for system in scenario.systems]
    for option_id, label, pv_system, battery in configs:
        if pv_system is None:
            pv = [0.0] * len(hours)
        else:
            key = (pv_system.tilt_deg, pv_system.azimuth_deg, pv_system.system_loss_pct)
            if key not in pv_cache:
                pv_cache[key] = pv_provider.per_kwp_window(scenario.location, pv_system, warm_start, end)
                pv_meta = dict(pv_provider.metadata)
            gaps = [hour for hour in hours if hour not in pv_cache[key]]
            if gaps:
                raise ReplayError(f"weather-based PV is missing for {len(gaps)} hour(s) (first {gaps[0].isoformat()})")
            pv = [pv_cache[key][hour] * pv_system.peak_kw for hour in hours]
        balance = simulate_energy(load, pv, battery)
        imports = [h.grid_import_kwh for h in balance]
        exports = [h.grid_export_kwh for h in balance]
        mask_imports = [q if keep else 0.0 for q, keep in zip(imports, reported)]
        mask_exports = [e if keep else 0.0 for e, keep in zip(exports, reported)]
        costs = []
        for key, (tariff_label, _) in tariffs.items():
            (imp_a, exp_a), (imp_u, exp_u) = prices[key]["actual"], prices[key]["usual"]
            actual_cost = _cost(mask_imports, mask_exports, imp_a, exp_a)
            usual_cost = _cost(mask_imports, mask_exports, imp_u, exp_u)
            costs.append(TariffCost(tariff=tariff_label, cost_actual_eur=round(actual_cost, 3),
                                    cost_usual_eur=round(usual_cost, 3), event_premium_eur=round(actual_cost - usual_cost, 3),
                                    event_hours_cost_actual_eur=round(_cost(imports, exports, imp_a, exp_a, in_event), 3),
                                    event_hours_cost_usual_eur=round(_cost(imports, exports, imp_u, exp_u, in_event), 3)))
        window_load = sum(value for value, keep in zip(load, reported) if keep)
        window_import = sum(mask_imports)
        options.append(OptionResult(
            option_id=option_id, label=label, pv_peak_kw=pv_system.peak_kw if pv_system else 0.0,
            battery_kwh=battery.usable_capacity_kwh if battery else None, load_kwh=round(window_load, 3),
            pv_kwh=round(sum(value for value, keep in zip(pv, reported) if keep), 3),
            grid_import_kwh=round(window_import, 3), grid_export_kwh=round(sum(mask_exports), 3),
            battery_discharge_kwh=round(sum(h.battery_discharge_kwh for h, keep in zip(balance, reported) if keep), 3),
            event_hours_import_kwh=round(sum(q for q, flag in zip(imports, in_event) if flag), 3),
            event_hours_battery_discharge_kwh=round(sum(h.battery_discharge_kwh for h, flag in zip(balance, in_event) if flag), 3),
            self_sufficiency_pct=round((1 - window_import / window_load) * 100, 1) if window_load else None,
            costs=costs))
        hourly_flows[option_id] = {
            "load_kwh": [round(v, 4) for v, keep in zip(load, reported) if keep],
            "pv_kwh": [round(v, 4) for v, keep in zip(pv, reported) if keep],
            "grid_import_kwh": [round(h.grid_import_kwh, 4) for h, keep in zip(balance, reported) if keep],
            "grid_export_kwh": [round(h.grid_export_kwh, 4) for h, keep in zip(balance, reported) if keep],
            "battery_charge_kwh": [round(h.battery_charge_kwh, 4) for h, keep in zip(balance, reported) if keep],
            "battery_discharge_kwh": [round(h.battery_discharge_kwh, 4) for h, keep in zip(balance, reported) if keep],
            "soc_end_kwh": [round(h.soc_end_kwh, 4) for h, keep in zip(balance, reported) if keep]}

    window_hours = [hour for hour, keep in zip(hours, reported) if keep]
    dyn_key = "dynamic" if "dynamic" in prices else "own"
    hourly = {"hours_utc": [hour.isoformat() for hour in window_hours],
              "in_event": [flag for flag, keep in zip(in_event, reported) if keep],
              "wholesale_actual_eur_per_mwh": [round(actual[hour], 2) for hour in window_hours],
              "wholesale_usual_eur_per_mwh": [round(usual[hour], 2) for hour in window_hours],
              "dynamic_import_actual_eur_per_kwh": [round(p, 4) for p, keep in zip(prices[dyn_key]["actual"][0], reported) if keep],
              "dynamic_import_usual_eur_per_kwh": [round(p, 4) for p, keep in zip(prices[dyn_key]["usual"][0], reported) if keep],
              "flows": hourly_flows}
    assumptions = [
        "Historical counterfactual simulation of the configured household; not an actual or predicted bill.",
        f"Replay window: the event's local calendar day(s) in {zones[zone_name]['timezone']}; a {WARMUP_HOURS}-hour "
        "warm-up before it is simulated so the battery starts from a realistic charge, but is not costed.",
        f"Household load: {source}.",
        "PV: modelled from Open-Meteo weather at the scenario location for these exact days (not PVGIS averages).",
        "Battery: self-consumption only (charges from solar surplus, discharges to cover load); it is not "
        "optimised against prices, so a price-aware battery could do better on a dynamic tariff.",
        f"Usual prices: median day-ahead price for the same UTC hour over the prior {USUAL_PRICE_DAYS} days.",
        "Dynamic tariff: wholesale price + supplier markup €0.02 + grid charges €0.10 + taxes/levies €0.05 per kWh, "
        "then 19% VAT (illustrative components, not a supplier offer). Standing charges are excluded.",
    ]
    if isinstance(scenario.tariff, FixedTariff):
        assumptions.append("A fixed tariff does not pass wholesale prices through, so on that tariff the event changes "
                           "nothing in this window; exposure appears only on a dynamic tariff.")
    market = market_summary(event, store, tools)
    identity = json.dumps({"anomaly": event["anomaly_id"], "simulation": simulation_id, "version": REPLAY_VERSION},
                          sort_keys=True)
    return ReplayResult(
        replay_id="rpl_" + hashlib.sha256(identity.encode()).hexdigest()[:16], version=REPLAY_VERSION,
        anomaly=event, simulation_id=simulation_id, scenario_id=scenario.scenario_id,
        location=scenario.location.model_dump(),
        window={"start_utc": start.isoformat(), "end_utc": end.isoformat(), "hours": len(window_hours),
                "event_start_utc": event["start_utc"], "event_end_utc": event["end_utc"],
                "timezone": zones[zone_name]["timezone"]},
        market=market, options=options, hourly=hourly,
        tariffs={key: {"label": label, **tariff.model_dump()} for key, (label, tariff) in tariffs.items()},
        assumptions=assumptions,
        sources={"price_raw_sha256": sorted(price_sources), "pv": pv_meta, "simulation_id": simulation_id,
                 "investigation_report_id": market["report_id"]},
        labels=["Counterfactual simulation", "Synthetic household load" if scenario.hourly_load_kwh is None
                else "Measured household load", "Illustrative dynamic tariff"])


class ReplayStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS replay (replay_id TEXT PRIMARY KEY, anomaly_id TEXT NOT NULL,
                simulation_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL)""")

    def save(self, result: ReplayResult) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO replay VALUES (?,?,?,?,?)",
                       (result.replay_id, result.anomaly["anomaly_id"], result.simulation_id,
                        datetime.now(UTC).isoformat(), result.model_dump_json()))

    def get(self, replay_id: str) -> dict[str, Any] | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT payload FROM replay WHERE replay_id=?", [replay_id]).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("""SELECT replay_id, anomaly_id, simulation_id, created_at FROM replay
                ORDER BY created_at DESC LIMIT ?""", [limit]).fetchall()
        return [{"replay_id": a, "anomaly_id": b, "simulation_id": c, "created_at": d} for a, b, c, d in rows]


def render_replay_markdown(result: dict[str, Any]) -> str:
    """The event-to-household report (handbook Phase 11 deliverable)."""
    event, window, market = result["anomaly"], result["window"], result["market"]
    lines = [f"# Event-to-household replay {result['replay_id']}", "",
             "*Counterfactual simulation of a configured household. Not an actual or predicted bill.*", "",
             f"**Event:** {event['anomaly_id']} · {event['geography']} price {event['direction']} · "
             f"{event['start_utc']} → {event['end_utc']} · peak {event['observed_value']:.1f} EUR/MWh vs usual "
             f"{event['baseline_median']:.1f} EUR/MWh (score {event['max_abs_score']:.1f})",
             f"**Household:** scenario {result['scenario_id']} (simulation {result['simulation_id']}) at "
             f"{result['location'].get('label') or (str(result['location']['latitude']) + ', ' + str(result['location']['longitude']))}",
             f"**Replay window:** {window['start_utc']} → {window['end_utc']} ({window['hours']} h, {window['timezone']})", "",
             "## What the market did (measured)", "",
             f"From investigation {market['report_id']} ({market['generator']}, status {market['status']}):", ""]
    lines += [f"- {item}" for item in market["observations"] + market["coinciding"]] or ["- No findings recorded."]
    if market["plausible"]:
        lines += ["", "Plausible contributing factors (not proven):", ""] + [f"- {item}" for item in market["plausible"]]
    if market["missing_evidence"]:
        lines += ["", "Could not be checked:", ""] + [f"- {item}" for item in market["missing_evidence"]]
    lines += ["", "## What it would have meant for this household", ""]
    tariff_names = [cost["tariff"] for cost in result["options"][0]["costs"]]
    header = "| Configuration | Grid import (kWh) | Battery during event (kWh) | " + " | ".join(
        f"{name}: actual / usual / premium (€)" for name in tariff_names) + " |"
    lines += [header, "|" + "---|" * (3 + len(tariff_names))]
    for option in result["options"]:
        cells = [f"{c['cost_actual_eur']:.2f} / {c['cost_usual_eur']:.2f} / {c['event_premium_eur']:+.2f}" for c in option["costs"]]
        lines.append(f"| {option['label']} | {option['grid_import_kwh']:.2f} | "
                     f"{option['event_hours_battery_discharge_kwh']:.2f} | " + " | ".join(cells) + " |")
    lines += ["", "## Assumptions and limitations", ""] + [f"- {item}" for item in result["assumptions"]]
    lines += ["", "## Sources", "", f"- Day-ahead price raw files: {len(result['sources']['price_raw_sha256'])}",
              f"- PV/weather: {result['sources']['pv'].get('source', 'n/a')} "
              f"(raw {str(result['sources']['pv'].get('raw_sha256', ''))[:16]})", ""]
    return "\n".join(lines)


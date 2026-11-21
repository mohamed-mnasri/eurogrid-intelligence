"""Command line for the Phase 7 household simulation engine.

Example (Berlin, 3,500 kWh/year, fixed tariff, the three default systems)::

    python -m eurogrid.simulate_cli --lat 52.52 --lon 13.405 --annual-kwh 3500 \
        --year 2023 --import-price 0.36 --export-price 0.08

Add ``--pv-source weather_model`` to model PV from Open-Meteo weather instead of
PVGIS, ``--compare-pv`` to print both annual yields side by side, or
``--dynamic-zone DE-LU`` to use an illustrative dynamic tariff from stored
wholesale prices. Results are written to ``data/simulations/<scenario>.json``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from eurogrid.ingestion import IngestionStore
from eurogrid.investigation_tools import InvestigationTools
from eurogrid.pv import PVGISProvider, WeatherModelProvider
from eurogrid.simulation import run_scenario
from eurogrid.simulation_models import (DynamicTariff, FixedTariff, HouseholdScenario, Location, PVSystem,
                                        default_systems)


def wholesale_for_year(database: Path, zone: str, year: int) -> dict[datetime, float]:
    """Valid hourly wholesale prices for a full year from the Gold models."""
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    hours, _ = InvestigationTools(database).price_hours(zone, start, datetime(year + 1, 1, 1, tzinfo=timezone.utc))
    return hours


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario-id", default="cli-scenario")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--timezone", default="Europe/Berlin")
    parser.add_argument("--annual-kwh", type=float, required=True)
    parser.add_argument("--year", type=int, default=2023)
    parser.add_argument("--import-price", type=float, default=0.36)
    parser.add_argument("--export-price", type=float, default=0.08)
    parser.add_argument("--dynamic-zone", help="use an illustrative dynamic tariff for this bidding zone")
    parser.add_argument("--pv-source", choices=("pvgis", "weather_model"), default="pvgis")
    parser.add_argument("--compare-pv", action="store_true", help="print PVGIS vs weather-model yield for 1 kWp")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)

    store = IngestionStore(args.data_dir)
    location = Location(latitude=args.lat, longitude=args.lon, timezone=args.timezone)
    if args.compare_pv:
        reference = PVSystem(peak_kw=1)
        pvgis = sum(PVGISProvider(store).per_kwp(location, reference, args.year).values())
        model = sum(WeatherModelProvider(store).per_kwp(location, reference, args.year).values())
        print(f"{args.year} yield per kWp (35° tilt, south, 14% loss): PVGIS {pvgis:,.0f} kWh · "
              f"weather model {model:,.0f} kWh ({(model - pvgis) / pvgis * 100:+.1f}%)")

    tariff = (DynamicTariff(bidding_zone=args.dynamic_zone, export_eur_per_kwh=args.export_price)
              if args.dynamic_zone else FixedTariff(import_eur_per_kwh=args.import_price,
                                                    export_eur_per_kwh=args.export_price))
    scenario = HouseholdScenario(scenario_id=args.scenario_id, location=location,
                                 annual_consumption_kwh=args.annual_kwh, tariff=tariff, systems=default_systems(),
                                 simulation_year=args.year, pv_source=args.pv_source)
    provider = PVGISProvider(store) if args.pv_source == "pvgis" else WeatherModelProvider(store)
    wholesale = wholesale_for_year(args.data_dir / "eurogrid.duckdb", args.dynamic_zone, args.year) if args.dynamic_zone else None
    result, _ = run_scenario(scenario, provider, wholesale)

    print(f"\n{result.scenario_id} · {result.simulation_year} · PV source: {result.pv_source.get('source')}")
    print(f"Load: {result.load_profile_source}\nTariff: {result.tariff_note}\n")
    print(f"{'System':34} {'PV kWh':>8} {'Self-use':>8} {'Self-suff':>9} {'Savings/yr':>10} {'Payback':>8} {'NPV':>10}")
    for system in result.systems:
        energy, money = system.energy, system.financial
        payback = "none" if money.simple_payback_years is None else f"{money.simple_payback_years:.1f} y"
        print(f"{system.label:34} {energy.pv_kwh:8,.0f} {energy.self_consumption_pct or 0:7.1f}% "
              f"{energy.self_sufficiency_pct or 0:8.1f}% {money.annual_net_savings_eur:10,.0f} {payback:>8} "
              f"{money.npv_eur:10,.0f}")
    print("\n" + "\n".join(f"* {label}" for label in result.labels))
    out = args.data_dir / "simulations" / f"{result.scenario_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

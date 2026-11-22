"""Phase 7 tests: energy conservation, battery limits, tariffs, finance and PV inputs."""

import math
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from eurogrid.ingestion import IngestionStore
from eurogrid.load_profile import synthetic_load, year_hours
from eurogrid.pv import PVGISProvider, parse_pvgis_hourly, plane_of_array, pv_from_weather, solar_position
from eurogrid.simulation import (ENERGY_TOLERANCE_KWH, SimulationError, financials, simulate_energy,
                                 simulate_scenario, tariff_prices)
from eurogrid.simulation_models import (BatterySystem, DynamicTariff, FinanceAssumptions, FixedTariff,
                                        HouseholdScenario, Location, PVSystem, SystemConfig, default_systems)

UTC = timezone.utc
BERLIN = Location(latitude=52.52, longitude=13.405)


def battery(**overrides):
    values = dict(usable_capacity_kwh=10, min_soc_pct=10, max_soc_pct=100, initial_soc_pct=50,
                  max_charge_kw=2, max_discharge_kw=2, charge_efficiency=0.9, discharge_efficiency=0.9)
    values.update(overrides)
    return BatterySystem(**values)


def synthetic_pv_per_kwp(year=2023):
    """Deterministic clear-sky-like PV shape for offline scenario tests."""
    result = {}
    for hour in year_hours(year):
        zenith, _ = solar_position(hour + timedelta(minutes=30), BERLIN.latitude, BERLIN.longitude)
        result[hour] = max(0.0, math.cos(math.radians(zenith))) * 0.55
    return result


class EnergyBalanceTests(unittest.TestCase):
    def test_battery_steps_match_hand_calculation(self):
        hours = simulate_energy([1, 3], [5, 0], battery())
        first, second = hours
        # Surplus 4 kWh, limited by 2 kW charge power: SoC 5 + 2×0.9 = 6.8, export 2, loss 0.2.
        self.assertAlmostEqual(first.battery_charge_kwh, 2)
        self.assertAlmostEqual(first.soc_end_kwh, 6.8)
        self.assertAlmostEqual(first.grid_export_kwh, 2)
        self.assertAlmostEqual(first.battery_loss_kwh, 0.2)
        # Shortfall 3 kWh, limited by 2 kW discharge: SoC 6.8 − 2/0.9, import 1.
        self.assertAlmostEqual(second.battery_discharge_kwh, 2)
        self.assertAlmostEqual(second.soc_end_kwh, 6.8 - 2 / 0.9)
        self.assertAlmostEqual(second.grid_import_kwh, 1)
        self.assertAlmostEqual(second.battery_loss_kwh, 2 * (1 / 0.9 - 1))

    def test_battery_respects_capacity_and_minimum_state_of_charge(self):
        full = simulate_energy([0] * 10, [10] * 10, battery(max_charge_kw=50))
        self.assertAlmostEqual(max(h.soc_end_kwh for h in full), 10)
        empty = simulate_energy([10] * 10, [0] * 10, battery(max_discharge_kw=50))
        self.assertAlmostEqual(min(h.soc_end_kwh for h in empty), 1.0)   # 10% of 10 kWh
        self.assertAlmostEqual(empty[-1].battery_discharge_kwh, 0)

    def test_energy_reconciles_every_hour_on_random_inputs(self):
        rng = random.Random(42)
        load = [rng.uniform(0, 3) for _ in range(2000)]
        pv = [max(0.0, rng.uniform(-2, 6)) for _ in range(2000)]
        unit = battery(max_charge_kw=3, max_discharge_kw=2.5, charge_efficiency=0.93, discharge_efficiency=0.96)
        for hour in simulate_energy(load, pv, unit):
            balance = ((hour.pv_kwh + hour.grid_import_kwh + hour.soc_start_kwh)
                       - (hour.load_kwh + hour.grid_export_kwh + hour.battery_loss_kwh + hour.soc_end_kwh))
            self.assertLessEqual(abs(balance), ENERGY_TOLERANCE_KWH)
            self.assertFalse(hour.battery_charge_kwh > 0 and hour.battery_discharge_kwh > 0)
            self.assertLessEqual(hour.battery_charge_kwh, max(0.0, hour.pv_kwh - hour.load_kwh) + 1e-12)
            self.assertLessEqual(hour.battery_charge_kwh, 3 + 1e-12)
            self.assertLessEqual(hour.battery_discharge_kwh, 2.5 + 1e-12)
            self.assertGreaterEqual(hour.soc_end_kwh, unit.min_soc_kwh - 1e-9)
            self.assertLessEqual(hour.soc_end_kwh, unit.max_soc_kwh + 1e-9)
            self.assertGreaterEqual(min(hour.grid_import_kwh, hour.grid_export_kwh), -1e-12)

    def test_without_battery_import_and_export_follow_the_handbook_formula(self):
        hours = simulate_energy([2, 1], [1, 3])
        self.assertEqual([(h.grid_import_kwh, h.grid_export_kwh) for h in hours], [(1, 0), (0, 2)])

    def test_rejects_mismatched_or_negative_inputs(self):
        with self.assertRaises(SimulationError):
            simulate_energy([1, 2], [1])
        with self.assertRaises(SimulationError):
            simulate_energy([1], [-1])


class TariffTests(unittest.TestCase):
    hours = [datetime(2025, 1, 1, hour, tzinfo=UTC) for hour in range(2)]

    def test_dynamic_price_is_wholesale_plus_explicit_components(self):
        tariff = DynamicTariff(bidding_zone="DE-LU", supplier_markup_eur_per_kwh=0.02, grid_charges_eur_per_kwh=0.1,
                               taxes_levies_eur_per_kwh=0.05, vat_pct=20, export_eur_per_kwh=0.07)
        imports, exports = tariff_prices(tariff, self.hours, {self.hours[0]: 100.0, self.hours[1]: -50.0})
        self.assertAlmostEqual(imports[0], (0.1 + 0.17) * 1.2)
        self.assertAlmostEqual(imports[1], (-0.05 + 0.17) * 1.2)   # negative wholesale passes through
        self.assertEqual(exports, [0.07, 0.07])
        floored, _ = tariff_prices(tariff.model_copy(update={"floor_energy_price_at_zero": True}), self.hours,
                                   {self.hours[0]: 100.0, self.hours[1]: -50.0})
        self.assertAlmostEqual(floored[1], 0.17 * 1.2)

    def test_dynamic_tariff_fails_closed_on_missing_wholesale_hours(self):
        with self.assertRaises(SimulationError):
            tariff_prices(DynamicTariff(bidding_zone="DE-LU"), self.hours, {self.hours[0]: 80.0})

    def test_fixed_tariff_ignores_wholesale(self):
        imports, _ = tariff_prices(FixedTariff(import_eur_per_kwh=0.3), self.hours, {self.hours[0]: 9999.0})
        self.assertEqual(imports, [0.3, 0.3])


class FinanceTests(unittest.TestCase):
    def test_npv_and_payback_match_hand_calculation(self):
        finance = FinanceAssumptions(horizon_years=3, discount_rate_pct=10, import_price_escalation_pct=0)
        result = financials(1000, 300, 0, 0, 1000, 700, finance, 0, None)
        self.assertAlmostEqual(result.npv_eur, -1000 + 300 / 1.1 + 300 / 1.21 + 300 / 1.331, places=2)
        self.assertEqual(result.simple_payback_years, 3.3)
        self.assertEqual([row.cumulative_cashflow_eur for row in result.cashflow], [-700, -400, -100])

    def test_replacement_degradation_escalation_and_negative_savings(self):
        finance = FinanceAssumptions(horizon_years=2, discount_rate_pct=0, import_price_escalation_pct=10)
        unit = battery(replacement_year=2, replacement_cost_eur=500)
        result = financials(0, 100, 0, 0, 0, 0, finance, 50, unit)
        self.assertAlmostEqual(result.cashflow[1].savings_eur, 100 * 1.1 * 0.5)
        self.assertEqual(result.cashflow[1].replacement_eur, 500)
        losing = financials(1000, 50, 0, 80, 0, 0, finance, 0, None)
        self.assertIsNone(losing.simple_payback_years)
        self.assertIn("No payback", losing.payback_note)


class ScenarioTests(unittest.TestCase):
    def scenario(self, **overrides):
        values = dict(scenario_id="berlin", location=BERLIN, annual_consumption_kwh=3500,
                      tariff=FixedTariff(import_eur_per_kwh=0.36, export_eur_per_kwh=0.08), systems=default_systems())
        values.update(overrides)
        return HouseholdScenario(**values)

    def test_three_configurations_are_simulated_reproducibly(self):
        pv = synthetic_pv_per_kwp()
        first, runs = simulate_scenario(self.scenario(), lambda _: pv)
        second, _ = simulate_scenario(self.scenario(), lambda _: pv)
        self.assertEqual(first.model_dump(), second.model_dump())
        self.assertEqual([s.system_id for s in first.systems], ["pv-4kw", "pv-4kw-bat-5kwh", "pv-8kw-bat-10kwh"])
        pv_only, with_battery, _ = first.systems
        self.assertAlmostEqual(pv_only.energy.load_kwh, 3500, places=3)
        self.assertGreater(with_battery.energy.self_sufficiency_pct, pv_only.energy.self_sufficiency_pct)
        self.assertEqual(len(pv_only.sensitivity), 12)
        for system in first.systems:
            self.assertLessEqual(system.energy.max_hourly_balance_error_kwh, ENERGY_TOLERANCE_KWH)
            self.assertEqual(len(runs[system.system_id].balance), 8760)
        self.assertTrue(any("synthetic profile" in label for label in first.labels))

    def test_missing_pv_hours_fail_closed(self):
        pv = synthetic_pv_per_kwp()
        pv.pop(next(iter(pv)))
        with self.assertRaises(SimulationError):
            simulate_scenario(self.scenario(), lambda _: pv)

    def test_input_validation(self):
        with self.assertRaises(ValidationError):
            self.scenario(systems=default_systems()[:1] * 2)
        with self.assertRaises(ValidationError):
            self.scenario(simulation_year=2025)  # PVGIS ends in 2023
        with self.assertRaises(ValidationError):
            self.scenario(hourly_load_kwh=[0.4] * 100)
        with self.assertRaises(ValidationError):
            battery(min_soc_pct=60, initial_soc_pct=50)
        with self.assertRaises(ValidationError):
            Location(latitude=10, longitude=0)
        with self.assertRaises(ValidationError):
            SystemConfig(system_id="Bad ID", label="x", pv=PVSystem(peak_kw=1), capex_eur=1)

    def test_synthetic_load_sums_to_annual_kwh_and_has_evening_peak(self):
        hours = year_hours(2024)
        load = synthetic_load(hours, 4000, "Europe/Berlin")
        self.assertEqual(len(load), 8784)
        self.assertAlmostEqual(sum(load), 4000, places=6)
        self.assertGreater(load[19], load[3])   # 20:00 vs 04:00 Berlin on 1 Jan


class PVTests(unittest.TestCase):
    def test_solar_position_at_greenwich_summer_solstice_noon(self):
        zenith, azimuth = solar_position(datetime(2026, 6, 21, 12, 2, tzinfo=UTC), 51.48, 0.0)
        self.assertAlmostEqual(zenith, 51.48 - 23.44, delta=0.3)
        self.assertAlmostEqual(azimuth, 180, delta=1.5)

    def test_weather_model_is_zero_at_night_and_tilt_helps_in_winter(self):
        night = pv_from_weather([(datetime(2026, 1, 15, 2, tzinfo=UTC), 0.0, 0.0)], BERLIN, PVSystem(peak_kw=1))
        self.assertEqual(list(night.values()), [0.0])
        zenith, azimuth = solar_position(datetime(2026, 1, 15, 11, 30, tzinfo=UTC), 52.52, 13.405)
        flat = plane_of_array(300, zenith, azimuth, 0, 0, 15)
        tilted = plane_of_array(300, zenith, azimuth, 60, 0, 15)
        self.assertGreater(tilted, flat)
        stamped = pv_from_weather([(datetime(2026, 1, 15, 12, tzinfo=UTC), 300.0, 0.0)], BERLIN, PVSystem(peak_kw=1))
        self.assertEqual(list(stamped), [datetime(2026, 1, 15, 11, tzinfo=UTC)])  # preceding-hour convention

    def test_pvgis_parse_and_cached_provider(self):
        rows = [{"time": (datetime(2023, 1, 1, tzinfo=UTC) + timedelta(hours=h)).strftime("%Y%m%d:%H10"),
                 "P": 500.0 if 10 <= h % 24 <= 14 else 0.0} for h in range(8760)]
        payload = {"inputs": {"location": {"latitude": 52.5, "longitude": 13.4}, "meteo_data": {"radiation_db": "TEST"}},
                   "outputs": {"hourly": rows}, "meta": {"outputs": {"hourly": {"variables": {}}}}}
        parsed = parse_pvgis_hourly(payload)
        self.assertEqual(parsed[datetime(2023, 1, 1, 12, tzinfo=UTC)], 0.5)
        calls = []

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_json(self, url, params=None):
                calls.append(params)
                return payload

        with tempfile.TemporaryDirectory() as root:
            provider = PVGISProvider(IngestionStore(Path(root)), client_factory=FakeClient)
            first = provider.per_kwp(BERLIN, PVSystem(peak_kw=4, tilt_deg=30, azimuth_deg=-20), 2023)
            second = provider.per_kwp(BERLIN, PVSystem(peak_kw=4, tilt_deg=30, azimuth_deg=-20), 2023)
        self.assertEqual(len(calls), 1)                      # second request served from the cache
        self.assertEqual(calls[0]["angle"], 30)
        self.assertEqual(calls[0]["aspect"], -20)
        self.assertEqual(first, second)
        self.assertEqual(provider.metadata["radiation_database"], "TEST")


if __name__ == "__main__":
    unittest.main()

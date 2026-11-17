"""Fetch current Eurostat household electricity reference values on demand."""

from __future__ import annotations

import calendar
from functools import lru_cache
from typing import Any

from eurogrid.connectors import EurostatConnector
from eurogrid.http import HttpJsonClient


def eurostat_series(payload: dict[str, Any], fixed_dimensions: dict[str, str], varying_dimension: str) -> dict[str, float]:
    """Read a filtered Eurostat JSON-stat series without assuming dimension order."""
    dimensions = payload.get("dimension", {})
    ids = payload.get("id", [])
    sizes = payload.get("size", [])
    if not ids or len(ids) != len(sizes) or varying_dimension not in ids:
        raise ValueError("Eurostat JSON-stat response has unexpected dimensions")
    categories: dict[str, dict[str, int]] = {}
    for name in ids:
        index = dimensions.get(name, {}).get("category", {}).get("index", {})
        if isinstance(index, list):
            categories[name] = {str(code): offset for offset, code in enumerate(index)}
        else:
            categories[name] = {str(code): int(offset) for code, offset in index.items()}
    values = payload.get("value", {})
    result: dict[str, float] = {}
    for code, varying_index in categories[varying_dimension].items():
        flat_index = 0
        usable = True
        for offset, name in enumerate(ids):
            if name == varying_dimension:
                category_index = varying_index
            elif name in fixed_dimensions:
                category_index = categories[name].get(fixed_dimensions[name], -1)
                if category_index < 0:
                    usable = False
                    break
            else:
                # The caller filters every other Eurostat dimension to one value.
                category_index = 0
            flat_index = flat_index * int(sizes[offset]) + category_index
        raw = values.get(str(flat_index)) if usable else None
        if raw is not None:
            result[code] = float(raw)
    return result


@lru_cache(maxsize=32)
def household_benchmark(geography: str) -> dict[str, Any]:
    """Return a per-person use benchmark and the latest all-tax household tariff.

    Eurostat updates the use and tariff series annually and twice-yearly,
    respectively, so values are cached for the lifetime of this local API process.
    """
    with HttpJsonClient(timeout_seconds=20, max_retries=1) as client:
        eurostat = EurostatConnector(client)
        consumption_sample = eurostat.household_electricity_consumption(geography)
        population_sample = eurostat.population(geography)
        price_sample = eurostat.household_prices(
            geography=geography, consumption_band="KWH2500-4999", tax="I_TAX", currency="EUR")

    household_use = eurostat_series(consumption_sample.payload,
                                    {"freq": "A", "nrg_bal": "FC_OTH_HH_E", "siec": "E7000", "unit": "GWH", "geo": geography},
                                    "time")
    population = eurostat_series(population_sample.payload,
                                  {"freq": "A", "unit": "NR", "age": "TOTAL", "sex": "T", "geo": geography},
                                  "time")
    common_years = sorted(set(household_use) & set(population))
    annual_kwh_per_person = daily_kwh_per_person = None
    use_year = None
    if common_years:
        use_year = common_years[-1]
        persons = population[use_year]
        if persons > 0:
            annual_kwh_per_person = household_use[use_year] * 1_000_000 / persons
            days = 366 if calendar.isleap(int(use_year)) else 365
            daily_kwh_per_person = annual_kwh_per_person / days

    tariff_by_period = eurostat_series(price_sample.payload,
                                       {"freq": "S", "siec": "E7000", "nrg_cons": "KWH2500-4999",
                                        "unit": "KWH", "tax": "I_TAX", "currency": "EUR", "geo": geography},
                                       "time")
    tariff_period = max(tariff_by_period, default=None)
    return {
        "geography": geography,
        "daily_kwh_per_person": daily_kwh_per_person,
        "annual_kwh_per_person": annual_kwh_per_person,
        "consumption_year": use_year,
        "retail_eur_per_kwh": tariff_by_period.get(tariff_period) if tariff_period else None,
        "retail_period": tariff_period,
        "retail_band": "2,500–4,999 kWh per year",
        "retail_tax_level": "All taxes and levies included",
        "sources": {
            "consumption": "Eurostat nrg_cb_e household electricity consumption divided by Eurostat demo_pjan population.",
            "retail_price": "Eurostat nrg_pc_204, household band 2,500–4,999 kWh/year, all taxes and levies included.",
        },
    }

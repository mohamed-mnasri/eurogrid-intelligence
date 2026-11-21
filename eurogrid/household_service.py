"""Fetch current Eurostat household electricity reference values on demand."""

from __future__ import annotations

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
def household_price_series(geography: str) -> tuple[dict[str, Any], ...]:
    """Load the official half-yearly household prices once per geography."""
    with HttpJsonClient(timeout_seconds=20, max_retries=1) as client:
        eurostat = EurostatConnector(client)
        price_sample = eurostat.household_prices(
            geography=geography, consumption_band="KWH2500-4999", tax="I_TAX", currency="EUR")

    tariff_by_period = eurostat_series(price_sample.payload,
                                       {"freq": "S", "siec": "E7000", "nrg_cons": "KWH2500-4999",
                                        "unit": "KWH", "tax": "I_TAX", "currency": "EUR", "geo": geography},
                                       "time")
    return tuple({"period": period, "retail_eur_per_kwh": price}
                 for period, price in sorted(tariff_by_period.items()))


def household_benchmark(geography: str) -> dict[str, Any]:
    """Return the latest official tax-inclusive household electricity price."""
    prices = household_price_series(geography)
    latest = prices[-1] if prices else None
    return {
        "geography": geography,
        "retail_eur_per_kwh": latest["retail_eur_per_kwh"] if latest else None,
        "retail_period": latest["period"] if latest else None,
        "retail_band": "2,500–4,999 kWh per year",
        "retail_tax_level": "All taxes and levies included",
        "sources": {
            "retail_price": "Eurostat nrg_pc_204, household band 2,500–4,999 kWh/year, all taxes and levies included.",
        },
    }


def household_price_history(geography: str, year: int) -> dict[str, Any]:
    """Return only the Eurostat H1/H2 retail observations for a selected year."""
    prefix = f"{year}-S"
    return {
        "geography": geography,
        "year": year,
        "retail_band": "2,500–4,999 kWh per year",
        "periods": [row for row in household_price_series(geography)
                    if row["period"].startswith(prefix)],
        "source": "Eurostat nrg_pc_204; national household prices are published half-yearly.",
    }

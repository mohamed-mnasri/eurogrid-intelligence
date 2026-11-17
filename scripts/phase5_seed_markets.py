"""Collect a bounded, rate-limit-aware sample for the handbook's five markets.

This calls public read-only endpoints and writes only to the gitignored Phase 2
data lake. Prices and power are requested for the five initial market groups;
weather is attached to one clearly identified reference coordinate per country.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import date

from eurogrid.cli import ingest_generation, ingest_price, ingest_weather
from eurogrid.ingestion import IngestionStore


@dataclass(frozen=True)
class Market:
    """Provider codes and a representative weather point for one market group."""

    name: str
    country_code: str
    bidding_zone: str
    latitude: float
    longitude: float
    reference_city: str


MARKETS = (
    Market("Germany", "de", "DE-LU", 52.52, 13.405, "Berlin"),
    Market("France", "fr", "FR", 48.8566, 2.3522, "Paris"),
    Market("Spain", "es", "ES", 40.4168, -3.7038, "Madrid"),
    Market("Italy", "it", "IT-North", 45.4642, 9.19, "Milan"),
    Market("Netherlands", "nl", "NL", 52.3676, 4.9041, "Amsterdam"),
)


def main(argv: list[str] | None = None) -> int:
    """Fetch each market's price, public power, and reference-point weather."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-01-01", help="First inclusive sample date")
    parser.add_argument("--end", default="2025-01-31", help="Last inclusive sample date; keep the request within 31 days")
    parser.add_argument("--wait-seconds", type=float, default=31, help="Delay between same-provider endpoint calls")
    args = parser.parse_args(argv)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start > end or (end - start).days >= 31:
        parser.error("choose an inclusive date range of at most 31 days")
    if args.wait_seconds < 0:
        parser.error("--wait-seconds cannot be negative")

    store = IngestionStore()
    for index, market in enumerate(MARKETS):
        print(f"[{index + 1}/{len(MARKETS)}] {market.name}: price {market.bidding_zone}", flush=True)
        ingest_price(store, market.bidding_zone, args.start, args.end)
        print(f"[{index + 1}/{len(MARKETS)}] {market.name}: public power {market.country_code}", flush=True)
        ingest_generation(store, market.country_code, args.start, args.end)
        print(f"[{index + 1}/{len(MARKETS)}] {market.name}: {market.reference_city} weather", flush=True)
        ingest_weather(store, market.latitude, market.longitude, args.start, args.end,
                       country_code=market.country_code)
        if index < len(MARKETS) - 1 and args.wait_seconds:
            time.sleep(args.wait_seconds)
    print(f"Saved source runs: {store.stats()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

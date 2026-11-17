"""Fetch European aggregate and country samples for the local dashboard.

By default this refreshes the latest complete calendar month. Pass an explicit
start/end pair for a historical backfill. Source calls are kept sequential and
rate-limit aware; raw responses remain local under the ignored data directory.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import date, timedelta

from eurogrid.cli import ingest_generation, ingest_price, ingest_weather
from eurogrid.ingestion import IngestionStore

MAX_SEED_DAYS = 731


@dataclass(frozen=True)
class Market:
    """Provider geography plus optional market price and weather coordinates."""

    name: str
    country_code: str
    bidding_zone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    reference_city: str | None = None


# The provider returns these two as European aggregates; they cannot be split
# back into country or regional values unless countries are fetched separately.
MARKETS = (
    Market("All Europe", "all"),
    Market("European Union", "eu"),
    Market("Germany", "de", "DE-LU", 52.52, 13.405, "Berlin"),
    Market("France", "fr", "FR", 48.8566, 2.3522, "Paris"),
    Market("Spain", "es", "ES", 40.4168, -3.7038, "Madrid"),
    Market("Poland", "pl", "PL", 52.2297, 21.0122, "Warsaw"),
    Market("Netherlands", "nl", "NL", 52.3676, 4.9041, "Amsterdam"),
    # Public power is available for the UK; this Energy-Charts price catalogue
    # does not provide a UK bidding-zone price, so no price request is made.
    Market("United Kingdom", "uk", None, 51.5072, -0.1276, "London"),
)


def latest_complete_month(today: date | None = None) -> tuple[str, str]:
    """Return the first and last dates of the most recently finished month."""
    today = today or date.today()
    first_of_this_month = today.replace(day=1)
    last_day = first_of_this_month - timedelta(days=1)
    first_day = last_day.replace(day=1)
    return first_day.isoformat(), last_day.isoformat()


def main(argv: list[str] | None = None) -> int:
    """Fetch prices where available, public power, and country-point weather."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="First inclusive date; provide together with --end for a backfill")
    parser.add_argument("--end", help="Last inclusive date; defaults to the latest complete month when omitted")
    parser.add_argument("--wait-seconds", type=float, default=31,
                        help="Delay between same-provider geography calls (default: 31)")
    args = parser.parse_args(argv)
    if (args.start is None) != (args.end is None):
        parser.error("provide both --start and --end, or omit both for the latest complete month")
    start_text, end_text = (args.start, args.end) if args.start else latest_complete_month()
    start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
    if start > end or (end - start).days + 1 > MAX_SEED_DAYS:
        parser.error(f"choose an inclusive date range of at most {MAX_SEED_DAYS} days")
    if args.wait_seconds < 0:
        parser.error("--wait-seconds cannot be negative")

    store = IngestionStore()
    for index, market in enumerate(MARKETS):
        if market.bidding_zone:
            print(f"[{index + 1}/{len(MARKETS)}] {market.name}: day-ahead prices", flush=True)
            ingest_price(store, market.bidding_zone, start_text, end_text)
        print(f"[{index + 1}/{len(MARKETS)}] {market.name}: public power", flush=True)
        ingest_generation(store, market.country_code, start_text, end_text)
        if market.latitude is not None and market.longitude is not None:
            print(f"[{index + 1}/{len(MARKETS)}] {market.name}: {market.reference_city} weather", flush=True)
            ingest_weather(store, market.latitude, market.longitude, start_text, end_text,
                           country_code=market.country_code)
        if index < len(MARKETS) - 1 and args.wait_seconds:
            time.sleep(args.wait_seconds)
    print(f"Saved source runs: {store.stats()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

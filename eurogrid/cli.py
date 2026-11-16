"""Bounded Phase 2 ingestion commands."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from eurogrid.connectors import EnergyChartsConnector, OpenMeteoConnector
from eurogrid.http import HttpJsonClient
from eurogrid.ingestion import IngestionStore, IngestionResult

MAX_BACKFILL_DAYS = 31


def identity(provider: str, dataset: str, endpoint: str, params: dict) -> dict:
    return {"provider": provider, "dataset": dataset, "endpoint": endpoint, "request_parameters": params}


def ingest_price(store: IngestionStore, zone: str, start: str, end: str, force: bool = False) -> IngestionResult:
    params = {"bzn": zone, "start": start, "end": end}
    with HttpJsonClient() as client:
        connector = EnergyChartsConnector(client)
        return store.ingest(lambda: connector.price(zone, start, end),
          identity=identity("energy-charts", "day_ahead_price", "/v2/price", params), force_refresh=force)


def ingest_generation(store: IngestionStore, country: str, start: str, end: str, force: bool = False) -> IngestionResult:
    params = {"country": country, "start": start, "end": end}
    with HttpJsonClient() as client:
        connector = EnergyChartsConnector(client)
        return store.ingest(lambda: connector.public_power(country, start, end),
          identity=identity("energy-charts", "public_power", "/v2/public_power", params), force_refresh=force)


def ingest_weather(store: IngestionStore, latitude: float, longitude: float, start: str, end: str, force: bool = False) -> IngestionResult:
    params = {"latitude": latitude, "longitude": longitude, "start_date": start, "end_date": end,
              "hourly": "temperature_2m,wind_speed_10m,shortwave_radiation", "timezone": "UTC"}
    with HttpJsonClient() as client:
        connector = OpenMeteoConnector(client)
        return store.ingest(lambda: connector.historical(latitude, longitude, start, end),
          identity=identity("open-meteo", "historical_weather", "/v1/archive", params), force_refresh=force)


def _run_chunks(start: str, end: str, chunk_days: int, run: Callable[[str, str], IngestionResult]) -> list[IngestionResult]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError("start date must be on or before end date")
    total = (last - first).days + 1
    if total > MAX_BACKFILL_DAYS:
        raise ValueError(f"Backfill is limited to {MAX_BACKFILL_DAYS} days per command")
    if not 1 <= chunk_days <= MAX_BACKFILL_DAYS:
        raise ValueError(f"chunk-days must be between 1 and {MAX_BACKFILL_DAYS}")
    results = []
    cursor = first
    while cursor <= last:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), last)
        results.append(run(cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return results


def _report(result: IngestionResult) -> None:
    print(f"rows={result.rows} cache_hit={result.cache_hit} sha256={result.raw_sha256}")
    print(f"raw={result.path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("price", "generation", "weather"):
        sub = commands.add_parser(name)
        sub.add_argument("--start", required=True)
        sub.add_argument("--end", required=True)
        sub.add_argument("--force", action="store_true", help="bypass the 24-hour response cache")
        if name == "price":
            sub.add_argument("--zone", default="DE-LU")
        elif name == "generation":
            sub.add_argument("--country", default="all")
        else:
            sub.add_argument("--latitude", type=float, default=52.52)
            sub.add_argument("--longitude", type=float, default=13.405)
    backfill = commands.add_parser("backfill")
    backfill.add_argument("--connector", choices=("price", "generation", "weather"), required=True)
    backfill.add_argument("--start", required=True)
    backfill.add_argument("--end", required=True)
    backfill.add_argument("--chunk-days", type=int, default=1)
    backfill.add_argument("--zone", default="DE-LU")
    backfill.add_argument("--country", default="all")
    backfill.add_argument("--latitude", type=float, default=52.52)
    backfill.add_argument("--longitude", type=float, default=13.405)
    backfill.add_argument("--force", action="store_true")
    commands.add_parser("stats")
    args = parser.parse_args(argv)
    store = IngestionStore(args.data_dir)
    if args.command == "stats":
        print(store.stats())
        return 0
    def run(start: str, end: str) -> IngestionResult:
        if args.command == "price" or getattr(args, "connector", None) == "price":
            return ingest_price(store, args.zone, start, end, args.force)
        if args.command == "generation" or getattr(args, "connector", None) == "generation":
            return ingest_generation(store, args.country, start, end, args.force)
        return ingest_weather(store, args.latitude, args.longitude, start, end, args.force)
    if args.command == "backfill":
        results = _run_chunks(args.start, args.end, args.chunk_days, run)
        for result in results:
            _report(result)
    else:
        _report(run(args.start, args.end))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

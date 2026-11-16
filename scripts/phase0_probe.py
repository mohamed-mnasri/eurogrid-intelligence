"""Small, read-only Phase 0 probes for immediately accessible public sources."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any

import truststore

# The bundled CA list in some Python distributions can lag behind Windows. Use
# the operating system trust store; certificate verification remains enabled.
truststore.inject_into_ssl()

TIMEOUT_SECONDS = 25
USER_AGENT = "EuroGrid-Phase0-Probe/0.1 (local learning project)"


def get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        print(f"HTTP {response.status} | {response.headers.get('Content-Type')} | {url}")
        return json.loads(response.read().decode("utf-8"))


def show(label: str, value: Any, limit: int = 1800) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    print(f"\n--- {label} ---\n{rendered[:limit]}")
    if len(rendered) > limit:
        print("... sample truncated; response was not saved")


def energy_power_url() -> str:
    params = {"country": "all", "start": "2025-01-01", "end": "2025-01-02"}
    return "https://api.energy-charts.info/v2/public_power?" + urllib.parse.urlencode(params)


def energy_price_url(bidding_zone: str = "DE-LU") -> str:
    params = {"bzn": bidding_zone, "start": "2025-01-01", "end": "2025-01-02"}
    return "https://api.energy-charts.info/v2/price?" + urllib.parse.urlencode(params)


def price_summary(price: dict[str, Any]) -> dict[str, Any]:
    """Keep the response's licence and time metadata visible in the probe output."""
    fields = (
        "country", "bidding_zone", "timezone", "resolution", "interval_minutes",
        "unit", "available_from", "available_until", "license", "data",
    )
    return {key: price.get(key) for key in fields}


def energy_charts() -> None:
    # Discover the live v2 catalogue before making endpoint calls.
    catalogue = get_json("https://api.energy-charts.info/v2")
    show("Energy-Charts v2 endpoint catalogue", catalogue, 1000)

    # The OpenAPI enum is the live source of supported bidding-zone codes.
    schema = get_json("https://api.energy-charts.info/openapi.json")
    zones = schema.get("components", {}).get("schemas", {}).get("BiddingZoneV2", {}).get("enum", [])
    show("Energy-Charts price bidding-zone codes", zones, 1200)

    # The documented all-country selector provides pan-European power data.
    show("Energy-Charts all-Europe public power", get_json(energy_power_url()))

    # Price endpoint is bidding-zone scoped (there is no all-Europe price selector).
    # DE-LU is the first handbook validation zone and is listed in the live enum.
    show("Energy-Charts price sample (DE-LU)", price_summary(get_json(energy_price_url())))


def open_meteo() -> None:
    end = date.today() - timedelta(days=7)
    start = end - timedelta(days=1)
    params = {
        "latitude": 52.52,
        "longitude": 13.405,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": "temperature_2m,wind_speed_10m",
        "timezone": "UTC",
    }
    url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)
    show("Open-Meteo historical weather (Berlin sample)", get_json(url))


def pvgis() -> None:
    params = {
        "lat": 48.8566,
        "lon": 2.3522,
        "peakpower": 1,
        "loss": 14,
        "outputformat": "json",
    }
    url = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc?" + urllib.parse.urlencode(params)
    show("PVGIS PV output (Paris sample)", get_json(url))


def eurostat() -> None:
    params = {
        "geo": "DE",
        "format": "JSON",
        "lang": "en",
        "freq": "S",
        "nrg_cons": "KWH2500-4999",
        "tax": "I_TAX",
        "currency": "EUR",
    }
    url = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_pc_204?" + urllib.parse.urlencode(params)
    result = get_json(url)
    show("Eurostat household electricity price sample (Germany; 2,500–4,999 kWh/year; incl. taxes; EUR)", eurostat_summary(result))


def eurostat_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Return six labeled periods without treating absent values as zero."""
    periods = result.get("dimension", {}).get("time", {}).get("category", {}).get("index", {})
    values = result.get("value", {})
    return {
        "label": result.get("label"),
        "updated": result.get("updated"),
        "dimensions": result.get("id"),
        "latest_period_values": [
            {"period": period, "price": values.get(str(index))}
            for period, index in list(periods.items())[-6:]
        ],
    }


PROBES = {
    "energy-charts": energy_charts,
    "open-meteo": open_meteo,
    "pvgis": pvgis,
    "eurostat": eurostat,
}


def main() -> int:
    requested = sys.argv[1:] or list(PROBES)
    failed = False
    for name in requested:
        probe = PROBES.get(name)
        if probe is None:
            print(f"Unknown probe: {name}. Choose from: {', '.join(PROBES)}", file=sys.stderr)
            failed = True
            continue
        print(f"\n### {name} ###")
        try:
            probe()
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as error:
            print(f"Probe failed: {error}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

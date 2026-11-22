"""Source and licence register (handbook 3.1 and 14, Data Operations).

One structured list of every provider the project uses, the datasets taken
from it, the licence/attribution terms and the access status. Served by the
Data Operations page and summarised in docs/sources.md.
"""

from __future__ import annotations

SOURCES: list[dict[str, str]] = [
    {
        "provider": "Fraunhofer ISE Energy-Charts",
        "provider_id": "energy-charts",
        "datasets": "day_ahead_price (/v2/price), public_power (/v2/public_power): generation, load, net cross-border trading",
        "licence": "CC BY 4.0 for general API data; day-ahead prices are CC BY 4.0 only for named zones "
                   "(e.g. DE-LU, FR, NL, PL, AT, BE); other zones are private/internal use only",
        "attribution": "Energy-Charts.info (Fraunhofer ISE); preserve the licence field returned with each response",
        "access": "No key; rate limited (respect 429 Retry-After)",
        "url": "https://api.energy-charts.info/",
        "status": "active",
    },
    {
        "provider": "Open-Meteo",
        "provider_id": "open-meteo",
        "datasets": "historical_weather (/v1/archive): temperature, wind speed, shortwave radiation",
        "licence": "CC BY 4.0; free API is for non-commercial use",
        "attribution": "Weather data by Open-Meteo.com (modelled reanalysis, not station observations)",
        "access": "No key for non-commercial use",
        "url": "https://open-meteo.com/en/terms",
        "status": "active",
    },
    {
        "provider": "European Commission JRC PVGIS 5.3",
        "provider_id": "pvgis",
        "datasets": "seriescalc hourly PV output per kWp (2005–2023), PVcalc",
        "licence": "No licence field in the JSON; follow JRC/PVGIS reuse terms",
        "attribution": "PVGIS © European Union, 2001–2026",
        "access": "No key",
        "url": "https://re.jrc.ec.europa.eu/api/",
        "status": "active",
    },
    {
        "provider": "Eurostat",
        "provider_id": "eurostat",
        "datasets": "nrg_pc_204 household electricity prices (half-yearly, band 2,500–4,999 kWh, incl. taxes)",
        "licence": "Reuse permitted with source acknowledgement unless a dataset notice says otherwise",
        "attribution": "Source: Eurostat (nrg_pc_204)",
        "access": "No key",
        "url": "https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204/default/table",
        "status": "active",
    },
    {
        "provider": "ENTSO-E Transparency Platform",
        "provider_id": "entsoe",
        "datasets": "prices, generation, load, flows, outages (planned)",
        "licence": "ENTSO-E terms and attribution for each data item",
        "attribution": "ENTSO-E Transparency Platform",
        "access": "Account + API token required; not connected, so outage evidence is reported as unavailable",
        "url": "https://transparency.entsoe.eu/",
        "status": "not connected",
    },
]

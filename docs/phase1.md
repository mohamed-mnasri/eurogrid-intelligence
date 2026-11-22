# Phase 1: API connector proof of concept

## Outcome

The live sample runner completed on 16 November 2026 with six saved JSON responses and a provenance manifest in the local, gitignored `data/raw/phase1/` directory. The responses are intentionally not committed; rerun `python -m eurogrid.sample` to reproduce them.

| Source sample | Observed response shape | Geography and time | Unit / resolution | Licence and attribution note |
|---|---|---|---|---|
| Energy-Charts day-ahead price | 48 hourly records | DE-LU; 1–2 January 2025; `Europe/Berlin` | EUR/MWh; PT1H | Response says CC BY 4.0 from Bundesnetzagentur / SMARD.de. The connector keeps the full response licence. |
| Energy-Charts public generation | 48 hourly records and 27 series | `country=all`; 1–2 January 2025; `Europe/Berlin` | MW; PT1H | Response says CC BY 4.0 and names energy-charts.info. The `all` selection returned a pan-European envelope; it does not claim every underlying series is complete. |
| Open-Meteo historical weather | 48 hourly rows across temperature, wind speed and shortwave radiation | Berlin coordinate request; provider returned grid point 52.54833, 13.407822 at 37 m; 1–2 January 2025; GMT/UTC | °C, km/h, W/m²; PT1H | API response has no licence field. Terms require free-tier non-commercial use and CC BY 4.0 attribution. This is reanalysis/modelled weather, not a station observation. |
| PVGIS annual reference | Monthly rows plus annual totals | Paris; 1 kWp, 14% loss and default mounting; SARAH3 radiation and ERA5 meteorology; database years 2005–2023 | Annual energy field `E_y` is kWh/y; monthly/hourly field units are captured from the response `meta` block. | JSON has no licence field. Attribute JRC/PVGIS 5.3 and follow its reuse terms. This is an API-shape reference using default mounting assumptions, not a household yield or financial estimate. |
| PVGIS hourly reference | 8,784 hourly rows for leap year 2020 | Paris; PVGIS-SARAH3 | `P` W, `G(i)` W/m², temperature °C, wind m/s; PT1H | Same JRC/PVGIS note as the annual response. The annual and hourly tools have distinct response shapes and are kept as separate raw files. |
| Eurostat `nrg_pc_204` household prices | JSON-stat dataset, 42 returned geography codes and 77 half-year periods through 2026-S1 | All geographies returned by the dataset; band `KWH2500-4999`; taxes included; EUR | EUR/kWh; P6M | Dataset update timestamp 2026-10-19. Eurostat reuse policy allows reuse of statistical data with source acknowledgement unless a dataset notice says otherwise. |

The exact raw response, query parameters, request time, source URL, units, coverage and attribution note are stored per sample. `manifest.json` gives a small index for reviewing them without opening each response. Energy-Charts has an actual per-response licence field; the other providers do not all return licence fields, so the manifest records their applicable source terms without inventing a response licence.

## Implementation

- `eurogrid/http.py`: HTTPX client with a request timeout, verified TLS using the Windows trust store, bounded GET retries, exponential fallback backoff, and `Retry-After` support for 429/5xx responses.
- `eurogrid/connectors.py`: typed read-only wrappers for Energy-Charts price and public power, Open-Meteo archive, PVGIS annual/hourly outputs, and Eurostat household prices.
- `eurogrid/sample.py`: runs the bounded source set sequentially, saves raw provider JSON and an attribution/coverage manifest. The output directory is gitignored.
- `tests/test_http.py` and `tests/test_connectors.py`: offline mock-transport tests for retries, query parameters, response metadata, missing values, annual/hourly shapes, and local sample writing.

## Coverage and licensing choices

The sample uses Energy-Charts `country=all` for generation and leaves Eurostat's `geo` dimension unfiltered so the response includes all geographies reported by that dataset. Energy-Charts day-ahead prices have no all-Europe selector and require one bidding zone per request; this sample stays with the licensed DE-LU zone instead of requesting restricted zones. Weather and PVGIS are coordinate-based global services, so the sample exercises one location in each.

ENTSO-E is not included because API access requires a separately approved user token. The source setup steps remain in [`sources.md`](sources.md).

## Re-run in Visual Studio Code

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
python -m eurogrid.sample
```

The sample fetch makes public read-only requests and writes local data; the unit tests make no network requests. Keep `data/raw/phase1/` out of Git unless a later licence review explicitly permits redistribution.

## Official provider documentation

- [Energy-Charts API v2 and OpenAPI schema](https://api.energy-charts.info/)
- [Open-Meteo historical weather API](https://open-meteo.com/en/docs/historical-weather-api)
- [PVGIS 5.3 non-interactive API](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/api-non-interactive-service_en)
- [Eurostat statistics API](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-detailed-guidelines/api-statistics)
- [Eurostat reuse policy](https://ec.europa.eu/eurostat/help/copyright-notice)

# Phase 0 source and licence inventory

Coverage policy: use provider-supported pan-European discovery/selection when the endpoint offers it. Record the exact zones, dates, response licence, and missing coverage returned by each data family. Do not hardcode five countries as the platform boundary. The handbook's initial deeply validated markets are Germany, France, Spain, Italy and the Netherlands.

| Provider | Phase 0 data / endpoint | Expected coverage | Licence and attribution | Credentials / status |
|---|---|---|---|---|
| Fraunhofer Energy-Charts | Public power `/v2/public_power`; day-ahead price `/v2/price`; machine-readable catalogue `/v2` and OpenAPI schema | Broad Europe; the live schema enumerates supported countries and bidding zones. `country=all` is available for public power. Prices are requested one bidding zone at a time; the endpoint has no all-Europe price selector. | General API data is CC BY 4.0 unless otherwise stated. Price endpoint docs identify specific CC BY 4.0 zones, while other zone prices are private/internal use only. Preserve each response's licence and do not redistribute restricted prices. | No token for public endpoints. Phase 0 probe included. Respect `429 Retry-After`; public price endpoint is rate limited. |
| Open-Meteo | Historical weather archive API | Global coordinate coverage; modelled reanalysis, not station observation. | Free API is for non-commercial use; CC BY 4.0 attribution. Confirm current terms before use. | No key for free non-commercial use. Phase 0 probe included. |
| PVGIS (JRC) | PV yield / hourly series API | Global/near-global supported land and coastal locations. | JRC/EU source; review current reuse and attribution terms. | No key. Phase 0 probe included. |
| Eurostat | `nrg_pc_204`, household electricity prices | EU and additional reporting countries; half-yearly national aggregates. | Eurostat reuse policy; cite dataset and extraction date. | No key. Phase 0 probe included. |
| ENTSO-E Transparency Platform | Prices, generation, load, flows, outages | Broad European market coverage; area codes, resolution and publication completeness vary. | Follow ENTSO-E terms and attribution for the specific data. | Account, API access request and security token required. Not probed until access is granted. Never commit token. |
| Global Wind Atlas (optional) | Wind resource maps / GIS downloads | Global screening layer. Not a turbine yield or financial model. | Follow its data ownership, terms and attribution requirements. | Public resource access; not required to satisfy Phase 0 API price/weather exit check. |

## Live documentation

- Energy-Charts API catalogue and OpenAPI schema: <https://api.energy-charts.info/> and <https://api.energy-charts.info/openapi.json>
- Energy-Charts overview: <https://www.energy-charts.info/api.html>
- Open-Meteo archive API: <https://open-meteo.com/en/docs/historical-weather-api>
- Open-Meteo terms: <https://open-meteo.com/en/terms>
- PVGIS API: <https://re.jrc.ec.europa.eu/api/>
- Eurostat dataset: <https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204/default/table>
- ENTSO-E Transparency Platform: <https://transparency.entsoe.eu/>
- ENTSO-E API guide: <https://transparency.entsoe.eu/content/static_content/Static%20content/web%20api/Guide.html>
- Global Wind Atlas: <https://globalwindatlas.info/>

## ENTSO-E access handoff

ENTSO-E registration requires the developer's own account and an email from its registered address. The handbook's requested sequence is: create and verify an account on the production Transparency Platform; email `transparency@entsoe.eu` with subject `RESTful API access` and the registered email address; after access is granted, create the security token in the account area and store it only in `.env` as `ENTSOE_API_TOKEN`. Access may take several working days. No account or email was created or sent as part of Phase 0.

## Current coverage choice

Energy-Charts documents an `all` country value for applicable APIs. That allows an all-Europe request for compatible endpoint families; it does **not** establish that every series or bidding zone is complete across Europe. The price endpoint requires one `bzn` per request, so pan-European prices require iterating supported zones and respecting each zone's licence. The live docs currently name CC BY 4.0 prices for AT, BE, CH, CZ, DE-LU, DE-AT-LU, DK1, DK2, FR, HU, IT-North, NL, NO2, PL, SE4 and SI; other price zones are marked private/internal. No one-call all-Europe price dataset is available from this endpoint. The probe prints the live bidding-zone enum and each sample's response licence. Country-wide public-power probe uses `country=all`. Validate each source independently before enabling it in a product workflow.

## Phase 0 sample check record (16 November 2026)

Read-only samples were successfully retrieved. Energy-Charts `/v2/price` returned a 60-minute DE-LU day-ahead series in EUR/MWh for 1–2 January 2025; the response includes the authoritative licence. `/v2/public_power?country=all` returned the European-wide public-power envelope. Open-Meteo returned hourly historical weather for the Berlin coordinate sample. PVGIS returned a 1 kWp Paris reference output using SARAH3/ERA5 data (2005–2023). Eurostat returned the 2,500–4,999 kWh/year, incl.-taxes household electricity price slice with `updated=2026-10-19` for Germany. These are shape/connectivity checks, not a claim of complete geographic/time coverage.

The first Python run exposed a stale bundled CA certificate problem in this Windows Python distribution. The probe now uses `truststore` to use the Windows certificate store while keeping normal TLS certificate verification enabled. ENTSO-E remains pending account approval and a token; it is not part of the immediately accessible sample check.

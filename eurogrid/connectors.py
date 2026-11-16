"""Phase 1, read-only public-source connectors and response metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from eurogrid.http import HttpJsonClient


ENERGY_CHARTS = "https://api.energy-charts.info"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
PVGIS = "https://re.jrc.ec.europa.eu/api/v5_3"
EUROSTAT = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_pc_204"

SOURCE_TERMS = {
    "energy-charts": "https://api.energy-charts.info/",
    "open-meteo": "https://open-meteo.com/en/terms",
    "pvgis": "https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/api-non-interactive-service_en",
    "eurostat": "https://ec.europa.eu/eurostat/help/copyright-notice",
}


@dataclass(frozen=True)
class SourceSample:
    provider: str
    dataset: str
    endpoint: str
    requested_at: str
    request_parameters: dict[str, Any]
    metadata: dict[str, Any]
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sample(
    *,
    provider: str,
    dataset: str,
    endpoint: str,
    parameters: dict[str, Any],
    metadata: dict[str, Any],
    payload: dict[str, Any],
) -> SourceSample:
    return SourceSample(
        provider=provider,
        dataset=dataset,
        endpoint=endpoint,
        requested_at=datetime.now(timezone.utc).isoformat(),
        request_parameters=parameters,
        metadata={"terms_url": SOURCE_TERMS[provider], **metadata},
        payload=payload,
    )


class EnergyChartsConnector:
    def __init__(self, client: HttpJsonClient) -> None:
        self.client = client

    def price(self, bidding_zone: str, start: str, end: str) -> SourceSample:
        params = {"bzn": bidding_zone, "start": start, "end": end}
        payload = self.client.get_json(f"{ENERGY_CHARTS}/v2/price", params=params)
        if not isinstance(payload.get("data"), list) or not payload.get("license"):
            raise ValueError("Energy-Charts price response is missing data or licence metadata")
        return _sample(
            provider="energy-charts",
            dataset="day_ahead_price",
            endpoint="/v2/price",
            parameters=params,
            metadata={
                "geography": {"country": payload.get("country"), "bidding_zone": payload.get("bidding_zone")},
                "period": {"start": payload.get("available_from"), "end": payload.get("available_until")},
                "timezone": payload.get("timezone"),
                "resolution": payload.get("resolution"),
                "interval_minutes": payload.get("interval_minutes"),
                "unit": payload.get("unit"),
                "license": payload.get("license"),
                "attribution": payload.get("license"),
                "series": payload.get("series"),
            },
            payload=payload,
        )

    def public_power(self, country: str, start: str, end: str) -> SourceSample:
        params = {"country": country, "start": start, "end": end}
        payload = self.client.get_json(f"{ENERGY_CHARTS}/v2/public_power", params=params)
        if not isinstance(payload.get("data"), list) or not isinstance(payload.get("series"), list):
            raise ValueError("Energy-Charts public_power response is missing data or series")
        return _sample(
            provider="energy-charts",
            dataset="public_power",
            endpoint="/v2/public_power",
            parameters=params,
            metadata={
                "geography": {"country": payload.get("country"), "bidding_zone": payload.get("bidding_zone")},
                "period": {"start": payload.get("available_from"), "end": payload.get("available_until")},
                "timezone": payload.get("timezone"),
                "resolution": payload.get("resolution"),
                "interval_minutes": payload.get("interval_minutes"),
                "unit": payload.get("unit"),
                "license": payload.get("license"),
                "license_note": "Provider default: CC BY 4.0 unless this dataset/response states otherwise.",
                "attribution": payload.get("license") or "energy-charts.info",
                "series": payload.get("series"),
            },
            payload=payload,
        )


class OpenMeteoConnector:
    def __init__(self, client: HttpJsonClient) -> None:
        self.client = client

    def historical(
        self,
        latitude: float,
        longitude: float,
        start: str,
        end: str,
        variables: tuple[str, ...] = ("temperature_2m", "wind_speed_10m", "shortwave_radiation"),
    ) -> SourceSample:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start,
            "end_date": end,
            "hourly": ",".join(variables),
            "timezone": "UTC",
        }
        payload = self.client.get_json(OPEN_METEO_ARCHIVE, params=params)
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
            raise ValueError("Open-Meteo response is missing hourly timestamps")
        for variable in variables:
            if variable not in hourly:
                raise ValueError(f"Open-Meteo response is missing requested variable: {variable}")
        return _sample(
            provider="open-meteo",
            dataset="historical_weather",
            endpoint="/v1/archive",
            parameters=params,
            metadata={
                "geography": {"latitude": payload.get("latitude"), "longitude": payload.get("longitude"), "elevation_m": payload.get("elevation")},
                "period": {"start": hourly["time"][0] if hourly["time"] else None, "end": hourly["time"][-1] if hourly["time"] else None},
                "timezone": payload.get("timezone"),
                "resolution": "PT1H",
                "unit": payload.get("hourly_units"),
                "license": None,
                "license_note": "Free API use is non-commercial; CC BY 4.0 attribution applies. Historical reanalysis is modelled data.",
                "attribution": "Open-Meteo.com",
                "model": "provider default (response does not identify a single model)",
            },
            payload=payload,
        )


class PVGISConnector:
    def __init__(self, client: HttpJsonClient) -> None:
        self.client = client

    def annual_pv_output(self, latitude: float, longitude: float, peak_power_kw: float = 1.0, loss_percent: float = 14.0) -> SourceSample:
        params = {"lat": latitude, "lon": longitude, "peakpower": peak_power_kw, "loss": loss_percent, "outputformat": "json"}
        payload = self.client.get_json(f"{PVGIS}/PVcalc", params=params)
        outputs = payload.get("outputs")
        if not isinstance(outputs, dict) or "monthly" not in outputs:
            raise ValueError("PVGIS PVcalc response is missing output data")
        location = payload.get("inputs", {}).get("location", {})
        meteo = payload.get("inputs", {}).get("meteo_data", {})
        output_meta = payload.get("meta", {}).get("outputs", {})
        return _sample(
            provider="pvgis",
            dataset="pv_yield_annual_reference",
            endpoint="/PVcalc",
            parameters=params,
            metadata={
                "geography": {"latitude": location.get("latitude", latitude), "longitude": location.get("longitude", longitude), "elevation_m": location.get("elevation")},
                "period": {"start_year": meteo.get("year_min"), "end_year": meteo.get("year_max")},
                "resolution": "monthly plus annual totals (if returned)",
                "unit": {
                    kind: {
                        field: {"description": info.get("description"), "unit": info.get("units")}
                        for field, info in output_meta.get(kind, {}).get("variables", {}).items()
                    }
                    for kind in ("monthly", "totals")
                },
                "license": None,
                "license_note": "No licence is declared in this JSON response; follow JRC/PVGIS reuse and attribution terms.",
                "attribution": "European Commission Joint Research Centre (JRC), PVGIS 5.3",
                "radiation_database": meteo.get("radiation_db"),
                "meteorological_database": meteo.get("meteo_db"),
            },
            payload=payload,
        )

    def hourly_pv_series(
        self,
        latitude: float,
        longitude: float,
        year: int,
        peak_power_kw: float = 1.0,
        loss_percent: float = 14.0,
    ) -> SourceSample:
        params = {
            "lat": latitude,
            "lon": longitude,
            "startyear": year,
            "endyear": year,
            "pvcalculation": 1,
            "peakpower": peak_power_kw,
            "loss": loss_percent,
            "outputformat": "json",
        }
        payload = self.client.get_json(f"{PVGIS}/seriescalc", params=params)
        outputs = payload.get("outputs")
        hourly = outputs.get("hourly") if isinstance(outputs, dict) else None
        if not isinstance(hourly, list):
            raise ValueError("PVGIS seriescalc response is missing hourly records")
        inputs = payload.get("inputs", {})
        location = inputs.get("location", {})
        hourly_variables = payload.get("meta", {}).get("outputs", {}).get("hourly", {}).get("variables", {})
        return _sample(
            provider="pvgis",
            dataset="pv_hourly_reference",
            endpoint="/seriescalc",
            parameters=params,
            metadata={
                "geography": {"latitude": location.get("latitude", latitude), "longitude": location.get("longitude", longitude), "elevation_m": location.get("elevation")},
                "period": {"start_year": year, "end_year": year, "records": len(hourly)},
                "resolution": "PT1H",
                "unit": {
                    field: {"description": info.get("description"), "unit": info.get("units")}
                    for field, info in hourly_variables.items()
                },
                "license": None,
                "license_note": "No licence is declared in this JSON response; follow JRC/PVGIS reuse and attribution terms.",
                "attribution": "European Commission Joint Research Centre (JRC), PVGIS 5.3",
                "radiation_database": inputs.get("meteo_data", {}).get("radiation_db"),
            },
            payload=payload,
        )


class EurostatConnector:
    def __init__(self, client: HttpJsonClient) -> None:
        self.client = client

    def household_prices(
        self,
        geography: str | None = None,
        consumption_band: str = "KWH2500-4999",
        tax: str = "I_TAX",
        currency: str = "EUR",
    ) -> SourceSample:
        params = {
            "format": "JSON",
            "lang": "en",
            "freq": "S",
            "nrg_cons": consumption_band,
            "tax": tax,
            "currency": currency,
            "unit": "KWH",
        }
        if geography is not None:
            params["geo"] = geography
        payload = self.client.get_json(EUROSTAT, params=params)
        if payload.get("class") != "dataset" or not isinstance(payload.get("value"), dict):
            raise ValueError("Eurostat response is missing dataset values")
        dimensions = payload.get("dimension", {})
        time_categories = dimensions.get("time", {}).get("category", {}).get("index", {})
        geography_codes = list(dimensions.get("geo", {}).get("category", {}).get("index", {}))
        return _sample(
            provider="eurostat",
            dataset="nrg_pc_204_household_electricity_prices",
            endpoint="/statistics/1.0/data/nrg_pc_204",
            parameters=params,
            metadata={
                "geography": geography if geography is not None else {"selection": "all geographies returned by dataset", "codes": geography_codes},
                "period": list(time_categories.keys()),
                "resolution": "P6M",
                "unit": currency + "/kWh (filtered dataset unit code KWH)",
                "license": None,
                "license_note": "Eurostat statistical-data reuse policy applies; cite Eurostat and dataset nrg_pc_204.",
                "attribution": "Eurostat, dataset nrg_pc_204",
                "updated": payload.get("updated"),
                "label": payload.get("label"),
                "dimensions": payload.get("id"),
                "dimension_sizes": payload.get("size"),
                "selected_dimensions": {"nrg_cons": consumption_band, "unit": "KWH", "tax": tax, "currency": currency},
            },
            payload=payload,
        )

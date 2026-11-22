import json
import tempfile
import unittest
from pathlib import Path

import httpx

from eurogrid.connectors import (
    EnergyChartsConnector,
    EurostatConnector,
    OpenMeteoConnector,
    PVGISConnector,
)
from eurogrid.http import HttpJsonClient
from eurogrid.sample import save_manifest, write_sample


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.payloads = {
            "/v2/price": {
                "country": "de", "bidding_zone": "DE-LU", "timezone": "Europe/Berlin",
                "resolution": "PT1H", "interval_minutes": 60, "unit": "EUR / MWh",
                "available_from": "2025-01-01T00:00:00+01:00", "available_until": "2025-01-01T23:00:00+01:00",
                "license": "CC BY 4.0", "series": [{"id": "day_ahead_price"}], "data": [],
            },
            "/v2/public_power": {
                "country": "all", "timezone": "Europe/Berlin", "resolution": "PT1H", "interval_minutes": 60,
                "unit": "MW", "series": [{"id": "solar"}, {"id": "wind_onshore"}], "data": [],
            },
            "/v1/archive": {
                "latitude": 52.52, "longitude": 13.41, "elevation": 44, "timezone": "GMT",
                "hourly_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h", "shortwave_radiation": "W/m²"},
                "hourly": {"time": ["2025-01-01T00:00"], "temperature_2m": [1], "wind_speed_10m": [4], "shortwave_radiation": [0]},
            },
            "/api/v5_3/PVcalc": {
                "inputs": {"location": {"latitude": 48.86, "longitude": 2.35, "elevation": 41},
                           "meteo_data": {"radiation_db": "PVGIS-SARAH3", "meteo_db": "ERA5", "year_min": 2005, "year_max": 2023}},
                "outputs": {"monthly": {"fixed": [{"month": 1, "E_m": 22.0}]}, "totals": {"fixed": {"E_y": 1000}}},
                "meta": {"outputs": {"monthly": {"variables": {"E_m": {"description": "Monthly energy", "units": "kWh/mo"}}},
                                        "totals": {"variables": {"E_y": {"description": "Annual energy", "units": "kWh/y"}}}}},
            },
            "/api/v5_3/seriescalc": {
                "inputs": {"location": {"latitude": 48.86, "longitude": 2.35, "elevation": 41},
                           "meteo_data": {"radiation_db": "PVGIS-SARAH3"}},
                "outputs": {"hourly": [{"time": "2020-01-01T00:00Z", "P": 0.0}]},
                "meta": {"outputs": {"hourly": {"variables": {"P": {"description": "Power", "units": "W"}}}}},
            },
            "/eurostat/api/dissemination/statistics/1.0/data/nrg_pc_204": {
                "class": "dataset", "label": "Household electricity prices", "updated": "2026-10-19",
                "id": ["freq", "geo", "time"], "size": [1, 1, 1], "value": {"0": 0.36},
                "dimension": {
                    "geo": {"category": {"index": {"EU27_2020": 0, "DE": 1}}},
                    "time": {"category": {"index": {"2026-S1": 0}}},
                },
            },
        }

        def handler(request):
            self.requests.append(request)
            payload = self.payloads.get(request.url.path)
            if payload is None:
                return httpx.Response(404)
            return httpx.Response(200, json=payload)

        self.client = HttpJsonClient(transport=httpx.MockTransport(handler), sleep=lambda _seconds: None)

    def tearDown(self):
        self.client.close()

    def test_energy_charts_price_keeps_response_licence_and_units(self):
        sample = EnergyChartsConnector(self.client).price("DE-LU", "2025-01-01", "2025-01-01")
        self.assertEqual(sample.metadata["license"], "CC BY 4.0")
        self.assertEqual(sample.metadata["unit"], "EUR / MWh")
        self.assertEqual(sample.metadata["resolution"], "PT1H")
        self.assertEqual(self.requests[-1].url.params["bzn"], "DE-LU")

    def test_energy_charts_power_accepts_all_europe(self):
        sample = EnergyChartsConnector(self.client).public_power("all", "2025-01-01", "2025-01-01")
        self.assertEqual(sample.metadata["geography"]["country"], "all")
        self.assertEqual(len(sample.metadata["series"]), 2)
        self.assertEqual(self.requests[-1].url.params["country"], "all")

    def test_open_meteo_checks_requested_arrays_and_records_units(self):
        sample = OpenMeteoConnector(self.client).historical(52.52, 13.405, "2025-01-01", "2025-01-01")
        self.assertEqual(sample.metadata["resolution"], "PT1H")
        self.assertEqual(sample.metadata["unit"]["temperature_2m"], "°C")
        self.assertEqual(sample.metadata["attribution"], "Open-Meteo.com")

    def test_pvgis_annual_and_hourly_have_distinct_shapes(self):
        connector = PVGISConnector(self.client)
        annual = connector.annual_pv_output(48.8566, 2.3522)
        hourly = connector.hourly_pv_series(48.8566, 2.3522, 2020)
        self.assertIn("monthly", annual.payload["outputs"])
        self.assertIn("hourly", hourly.payload["outputs"])
        self.assertEqual(hourly.metadata["period"]["records"], 1)
        self.assertEqual(annual.metadata["unit"]["totals"]["E_y"]["unit"], "kWh/y")
        self.assertEqual(hourly.metadata["unit"]["P"]["unit"], "W")

    def test_eurostat_sample_records_all_reported_geographies_and_half_year_resolution(self):
        sample = EurostatConnector(self.client).household_prices()
        self.assertEqual(sample.metadata["geography"]["codes"], ["EU27_2020", "DE"])
        self.assertEqual(sample.metadata["resolution"], "P6M")
        self.assertEqual(sample.request_parameters["nrg_cons"], "KWH2500-4999")
        self.assertEqual(sample.request_parameters["unit"], "KWH")
        self.assertNotIn("geo", sample.request_parameters)

    def test_raw_sample_and_manifest_keep_provenance(self):
        sample = EnergyChartsConnector(self.client).price("DE-LU", "2025-01-01", "2025-01-01")
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            sample_path = write_sample(sample, output_dir)
            manifest_path = save_manifest([sample], [sample_path], output_dir)
            stored = json.loads(sample_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["metadata"]["license"], "CC BY 4.0")
        self.assertEqual(manifest["samples"][0]["attribution"], "CC BY 4.0")
        self.assertTrue(manifest["samples"][0]["terms_url"])


if __name__ == "__main__":
    unittest.main()

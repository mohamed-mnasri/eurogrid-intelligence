"""Offline tests for the Phase 0 source-probe helpers."""

import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from scripts import phase0_probe


class EnergyChartsProbeTests(unittest.TestCase):
    def test_public_power_probe_requests_all_europe(self):
        parsed = urlparse(phase0_probe.energy_power_url())
        self.assertEqual(parsed.path, "/v2/public_power")
        self.assertEqual(parse_qs(parsed.query)["country"], ["all"])

    def test_price_probe_is_bidding_zone_scoped(self):
        parsed = urlparse(phase0_probe.energy_price_url("FR"))
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/v2/price")
        self.assertEqual(query["bzn"], ["FR"])
        self.assertNotIn("country", query)

    def test_price_summary_preserves_license_and_negative_prices(self):
        response = {
            "bidding_zone": "DE-LU",
            "license": "CC BY 4.0",
            "data": [{"timestamp": "2025-01-01T00:00:00+01:00", "values": {"day_ahead_price": -0.1}}],
        }
        summary = phase0_probe.price_summary(response)
        self.assertEqual(summary["license"], "CC BY 4.0")
        self.assertEqual(summary["data"][0]["values"]["day_ahead_price"], -0.1)


class EurostatProbeTests(unittest.TestCase):
    def test_summary_labels_periods_and_keeps_missing_values(self):
        response = {
            "label": "Household price",
            "updated": "2026-10-19",
            "id": ["geo", "time"],
            "dimension": {"time": {"category": {"index": {"2025-S1": 0, "2025-S2": 1}}}},
            "value": {"0": 0.38},
        }
        summary = phase0_probe.eurostat_summary(response)
        self.assertEqual(summary["latest_period_values"], [
            {"period": "2025-S1", "price": 0.38},
            {"period": "2025-S2", "price": None},
        ])


class HttpHelperTests(unittest.TestCase):
    @patch("scripts.phase0_probe.urllib.request.urlopen")
    def test_get_json_decodes_response_and_sets_timeout(self, urlopen):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.headers.get.return_value = "application/json"
        response.__enter__.return_value.read.return_value = json.dumps({"ok": True}).encode()
        urlopen.return_value = response

        self.assertEqual(phase0_probe.get_json("https://example.test/data"), {"ok": True})
        self.assertEqual(urlopen.call_args.kwargs["timeout"], phase0_probe.TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()

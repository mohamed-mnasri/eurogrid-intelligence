import unittest
from datetime import datetime, timezone

import httpx

from eurogrid.http import HttpJsonClient, _retry_after_seconds


class HttpJsonClientTests(unittest.TestCase):
    def test_returns_json_for_successful_get(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"ok": True})

        with HttpJsonClient(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(client.get_json("https://example.test/data", params={"q": "x"}), {"ok": True})
        self.assertEqual(seen[0].method, "GET")
        self.assertEqual(seen[0].url.params["q"], "x")

    def test_honors_retry_after_then_retries_transient_status(self):
        calls = []
        sleeps = []

        def handler(_request):
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "3"})
            return httpx.Response(200, json={"ok": True})

        with HttpJsonClient(transport=httpx.MockTransport(handler), sleep=sleeps.append) as client:
            self.assertEqual(client.get_json("https://example.test/data"), {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [3.0])

    def test_does_not_retry_client_errors(self):
        calls = []

        def handler(_request):
            calls.append(1)
            return httpx.Response(400, json={"detail": "bad input"})

        with HttpJsonClient(transport=httpx.MockTransport(handler), sleep=lambda _seconds: None) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                client.get_json("https://example.test/data")
        self.assertEqual(len(calls), 1)

    def test_retry_after_supports_delta_seconds_and_ignores_bad_headers(self):
        self.assertEqual(_retry_after_seconds("4"), 4.0)
        self.assertIsNone(_retry_after_seconds("not-a-date"))

    def test_retry_after_supports_http_date(self):
        now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        self.assertEqual(_retry_after_seconds("Mon, 16 Nov 2026 00:00:05 GMT", now), 5.0)

    def test_retries_network_errors_with_bounded_backoff(self):
        calls = []
        sleeps = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ConnectError("temporary network failure", request=request)
            return httpx.Response(200, json={"ok": True})

        with HttpJsonClient(transport=httpx.MockTransport(handler), sleep=sleeps.append) as client:
            self.assertEqual(client.get_json("https://example.test/data"), {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [0.5])

    def test_rejects_invalid_timeout_and_retry_count(self):
        with self.assertRaises(ValueError):
            HttpJsonClient(timeout_seconds=0)
        with self.assertRaises(ValueError):
            HttpJsonClient(max_retries=-1)


if __name__ == "__main__":
    unittest.main()

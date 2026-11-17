"""HTTP transport shared by the provider connectors.

The client centralizes HTTPS verification, timeouts, and safe retries so that
individual source connectors only need to describe their endpoint requests.
"""

from __future__ import annotations

import email.utils
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
import truststore

# Use the operating-system trust store on Windows while leaving verification on.
truststore.inject_into_ssl()


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Parse either Retry-After format, returning None when it is unusable."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            return max(0.0, (retry_at - current).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class HttpJsonClient:
    """HTTPX wrapper for idempotent JSON GETs with timeout and safe retries.

    Only GET requests are exposed, and only transient network errors or
    documented transient status codes are retried. Retry-After is honored.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 25.0,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Create a reusable HTTP client; transport and sleep are injectable for tests."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": "EuroGrid-Intelligence/0.2 (local data sample)"},
            transport=transport,
            follow_redirects=True,
        )

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        """GET a JSON response and retry only transient network/server failures."""
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.get(url, params=params)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt >= self._max_retries:
                    raise
                self._sleep(self._backoff_seconds * (2**attempt))
                continue

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                delay = _retry_after_seconds(response.headers.get("Retry-After"))
                if delay is None:
                    delay = self._backoff_seconds * (2**attempt)
                self._sleep(delay)
                continue

            response.raise_for_status()
            return response.json()

        raise RuntimeError("GET retry loop exited unexpectedly")

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> "HttpJsonClient":
        """Allow the client to be managed with a ``with`` statement."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the connection pool when leaving a context manager."""
        self.close()

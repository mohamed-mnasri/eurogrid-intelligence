"""The markets the project ingests: provider geography, optional price zone and weather reference city."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Market:
    """Provider geography plus optional market price and weather coordinates."""

    name: str
    country_code: str
    bidding_zone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    reference_city: str | None = None


# The provider returns "all" as a European aggregate; it cannot be split back into countries.
MARKETS: tuple[Market, ...] = (
    Market("All Europe", "all"),
    Market("Germany", "de", "DE-LU", 52.52, 13.405, "Berlin"),
    Market("France", "fr", "FR", 48.8566, 2.3522, "Paris"),
    Market("Spain", "es", "ES", 40.4168, -3.7038, "Madrid"),
    Market("Poland", "pl", "PL", 52.2297, 21.0122, "Warsaw"),
    Market("Netherlands", "nl", "NL", 52.3676, 4.9041, "Amsterdam"),
    # Public power is available for the UK; Energy-Charts has no UK day-ahead price, so none is requested.
    Market("United Kingdom", "uk", None, 51.5072, -0.1276, "London"),
)

"""One logging configuration for the CLIs and the API (level from EUROGRID_LOG_LEVEL, default INFO)."""

from __future__ import annotations

import logging
import os

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str | None = None) -> None:
    """Configure the root logger once; later calls only adjust the level."""
    name = (level or os.environ.get("EUROGRID_LOG_LEVEL") or "INFO").upper()
    root = logging.getLogger()
    if not any(getattr(handler, "_eurogrid", False) for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(FORMAT, "%Y-%m-%dT%H:%M:%S"))
        handler._eurogrid = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(getattr(logging, name, logging.INFO))
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

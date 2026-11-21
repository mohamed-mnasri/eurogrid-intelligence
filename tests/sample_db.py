"""Synthetic Gold-layer DuckDB for service-level tests.

Thin wrapper around :mod:`eurogrid.synthetic`. The default conditions inject a
DE-LU spike to 400 EUR/MWh on 2026-03-05 17:00–20:00 UTC while German wind
drops to 2 GW and load rises 10%; FR stays calm, so the event is local.
``missing_generation=True`` deletes German generation in the event window to
exercise fail-closed / limited-report behaviour.
"""

from __future__ import annotations

from pathlib import Path

from eurogrid.synthetic import EVENT_END, EVENT_START, Conditions, build_synthetic_database

__all__ = ["EVENT_END", "EVENT_START", "build_sample_database"]


def build_sample_database(path: Path, missing_generation: bool = False) -> Path:
    return build_synthetic_database(path, Conditions(missing_generation=missing_generation))

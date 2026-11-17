"""Run a small source survey and save original JSON with a provenance manifest.

This is a reproducible connectivity/schema sample, separate from the Phase 2
incremental ingestion CLI.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from eurogrid.connectors import (
    EnergyChartsConnector,
    EurostatConnector,
    OpenMeteoConnector,
    PVGISConnector,
    SourceSample,
)
from eurogrid.http import HttpJsonClient


def _safe_name(value: str) -> str:
    """Convert a provider/dataset label into a portable filename component."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower()
    if not cleaned:
        raise ValueError("sample name must contain letters or digits")
    return cleaned


def write_sample(sample: SourceSample, output_dir: Path) -> Path:
    """Write one complete source sample atomically as indented JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_name(f"{sample.provider}_{sample.dataset}")
    destination = output_dir / f"{name}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(sample.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return destination


def save_manifest(samples: list[SourceSample], files: list[Path], output_dir: Path) -> Path:
    """Write an at-a-glance index of each sample's coverage and attribution."""
    manifest = {
        "project": "EuroGrid Intelligence",
        "phase": 1,
        "purpose": "Bounded raw API connectivity, schema, coverage, and licensing samples.",
        "samples": [
            {
                "file": file.name,
                "provider": sample.provider,
                "dataset": sample.dataset,
                "endpoint": sample.endpoint,
                "requested_at": sample.requested_at,
                "geography": sample.metadata.get("geography"),
                "period": sample.metadata.get("period"),
                "unit": sample.metadata.get("unit"),
                "resolution": sample.metadata.get("resolution"),
                "license": sample.metadata.get("license"),
                "license_note": sample.metadata.get("license_note"),
                "attribution": sample.metadata.get("attribution"),
                "terms_url": sample.metadata.get("terms_url"),
            }
            for sample, file in zip(samples, files)
        ],
    }
    destination = output_dir / "manifest.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return destination


def _format_units(units: Any) -> str:
    """Format provider unit metadata for concise terminal output."""
    if not isinstance(units, dict):
        return str(units)
    if "monthly" in units or "totals" in units:
        labels = []
        for group, fields in units.items():
            if isinstance(fields, dict):
                value_key = "E_y" if group == "totals" else "E_m"
                field = fields.get(value_key, {})
                if field.get("unit"):
                    labels.append(f"{value_key}={field['unit']}")
        return "; ".join(labels) or "see raw response metadata"
    if units and all(isinstance(value, dict) for value in units.values()):
        return ", ".join(f"{key}={value.get('unit') or 'unit n/a'}" for key, value in units.items())
    return ", ".join(f"{key}={value}" for key, value in units.items())


def fetch_phase1_samples(start: str, end: str, pvgis_year: int) -> list[SourceSample]:
    """Fetch the bounded demonstration set sequentially to respect rate limits."""
    with HttpJsonClient(timeout_seconds=30, max_retries=2) as client:
        energy = EnergyChartsConnector(client)
        weather = OpenMeteoConnector(client)
        pvgis = PVGISConnector(client)
        eurostat = EurostatConnector(client)
        # Sequential GETs avoid load spikes and stay well within Energy-Charts limits.
        return [
            energy.price("DE-LU", start, end),
            energy.public_power("all", start, end),
            weather.historical(52.52, 13.405, start, end),
            pvgis.annual_pv_output(48.8566, 2.3522),
            pvgis.hourly_pv_series(48.8566, 2.3522, pvgis_year),
            eurostat.household_prices(),
        ]


def main(argv: list[str] | None = None) -> int:
    """Parse sample options, fetch source responses, and write them locally."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-01-01", help="Start date for market and weather samples")
    parser.add_argument("--end", default="2025-01-02", help="End date for market and weather samples (inclusive, provider-specific)")
    parser.add_argument("--pvgis-year", type=int, default=2020, help="One historical year for PVGIS hourly output")
    parser.add_argument("--output", type=Path, default=Path("data/raw/phase1"), help="Local output directory (gitignored)")
    args = parser.parse_args(argv)
    if args.start > args.end:
        parser.error("--start must be earlier than or equal to --end")

    samples = fetch_phase1_samples(args.start, args.end, args.pvgis_year)
    files = [write_sample(sample, args.output) for sample in samples]
    manifest = save_manifest(samples, files, args.output)
    print(f"Saved {len(files)} raw samples and provenance metadata to {args.output}")
    print(f"Manifest: {manifest}")
    for sample in samples:
        print(
            f"- {sample.provider}/{sample.dataset}: geography={sample.metadata.get('geography')}; "
            f"resolution={sample.metadata.get('resolution')}; unit={_format_units(sample.metadata.get('unit'))}; "
            f"license={sample.metadata.get('license') or sample.metadata.get('license_note')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

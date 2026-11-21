"""Command line for Phase 6 anomaly detection and rule-based investigations.

Examples (from the project root, with .venv active)::

    python -m eurogrid.investigate_cli detect --zone DE-LU --start 2026-01-01 --end 2026-10-31
    python -m eurogrid.investigate_cli list --zone DE-LU --limit 10
    python -m eurogrid.investigate_cli report --anomaly-id anm_...

Reports are saved in the case store and written as JSON + Markdown under
``data/investigations/`` (gitignored with the rest of ``data/``).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from eurogrid.anomaly import DetectorConfig
from eurogrid.investigation import CaseStore, detect_price_anomalies, investigate, render_markdown
from eurogrid.investigation_tools import InvestigationTools


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", type=Path, default=Path("data/eurogrid.duckdb"))
    parser.add_argument("--store", type=Path, default=Path("data/metadata/investigations.sqlite"))
    commands = parser.add_subparsers(dest="command", required=True)
    detect = commands.add_parser("detect", help="score prices and save anomaly events")
    detect.add_argument("--zone", required=True)
    detect.add_argument("--start", required=True, type=date.fromisoformat)
    detect.add_argument("--end", required=True, type=date.fromisoformat)
    detect.add_argument("--window-days", type=int, default=28)
    detect.add_argument("--min-observations", type=int, default=8)
    detect.add_argument("--threshold", type=float, default=5.0)
    detect.add_argument("--mad-floor", type=float, default=10.0)
    listing = commands.add_parser("list", help="show saved anomaly events")
    listing.add_argument("--zone")
    listing.add_argument("--limit", type=int, default=20)
    report = commands.add_parser("report", help="run the rule-based investigation for one anomaly")
    report.add_argument("--anomaly-id", required=True)
    report.add_argument("--out-dir", type=Path, default=Path("data/investigations"))
    args = parser.parse_args(argv)

    tools = InvestigationTools(args.database)
    store = CaseStore(args.store)
    if args.command == "detect":
        config = DetectorConfig(window_days=args.window_days, min_observations=args.min_observations,
                                threshold=args.threshold, mad_floor=args.mad_floor)
        events = detect_price_anomalies(tools, args.zone, args.start, args.end, config)
        store.save_events(events)
        print(f"{len(events)} anomaly event(s) saved for {args.zone} {args.start}..{args.end}")
        for event in sorted(events, key=lambda item: -item.max_abs_score)[:10]:
            print(f"  {event.anomaly_id}  {event.direction:5}  {event.start_utc}  {event.hour_count:>3}h  "
                  f"peak {event.observed_value:8.2f} vs {event.baseline_median:8.2f} EUR/MWh  score {event.max_abs_score:.1f}")
    elif args.command == "list":
        events, total = store.list_events(geography=args.zone, limit=args.limit)
        print(f"{total} saved event(s); showing {len(events)}")
        for event in events:
            print(f"  {event['anomaly_id']}  {event['geography']:6} {event['direction']:5} {event['start_utc']}  "
                  f"score {event['max_abs_score']:.1f}")
    else:
        event = store.get_event(args.anomaly_id)
        if event is None:
            parser.error(f"unknown anomaly id {args.anomaly_id}; run 'detect' first")
        result = investigate(event, tools)
        store.save_report(result)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / f"{result.report_id}.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        markdown = render_markdown(result)
        (args.out_dir / f"{result.report_id}.md").write_text(markdown, encoding="utf-8")
        print(markdown)
        print(f"Saved {args.out_dir / (result.report_id + '.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

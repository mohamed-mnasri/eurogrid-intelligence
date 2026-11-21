"""Phase 12 offline demo: build a synthetic dataset and prepare every page, with no API credentials.

    python -m eurogrid.demo

Creates ``data/demo/`` with:

* ``eurogrid_demo.duckdb`` — synthetic source tables (``provider='synthetic'``) for DE-LU and France,
  1 Feb – 12 Mar 2026, with a known injected German price spike on 5 March 17:00–20:00 UTC;
* Gold models built by the real dbt project when dbt is installed (otherwise ready-made Gold tables);
* detected anomalies, rule-based investigations and a Scenario Lab run in ``data/demo/metadata/``.

Then start the API against it (the printed commands set ``EUROGRID_DB_PATH``, ``EUROGRID_DATA_DIR`` and
``EUROGRID_DBT_DIR``). The app shows a "Synthetic demo data" label everywhere. The planner and replay still
call PVGIS / Open-Meteo / Eurostat, which are free and need no key; the AI agent needs a local Ollama model.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from eurogrid.chaos import dbt_executable, run_dbt
from eurogrid.evaluation import EvaluationStore, run_suite
from eurogrid.investigation import CaseStore, detect_price_anomalies, investigate
from eurogrid.investigation_tools import InvestigationTools
from eurogrid.synthetic import build_synthetic_sources


def build_demo(data_dir: Path, use_dbt: bool = True) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / "eurogrid_demo.duckdb"
    for stale in (database, data_dir / "metadata" / "investigations.sqlite"):
        if stale.exists():
            stale.unlink()
    with_dbt = use_dbt and dbt_executable() is not None
    build_synthetic_sources(database, keep_gold=not with_dbt)
    dbt_summary = {"ran": False, "reason": "dbt not used (ready-made Gold tables)"}
    if with_dbt:
        dbt_summary = run_dbt(database, data_dir / "dbt")
        if not dbt_summary.get("ran") or dbt_summary.get("exit_code"):
            raise SystemExit(f"dbt build failed on the demo data: {dbt_summary.get('reason') or dbt_summary.get('counts')}")
    tools = InvestigationTools(database)
    store = CaseStore(data_dir / "metadata" / "investigations.sqlite")
    events = []
    for zone in sorted(tools.zones()):
        found = detect_price_anomalies(tools, zone, date(2026, 3, 1), date(2026, 3, 12))
        store.save_events(found)
        events.extend(found)
    for event in events:
        store.save_report(investigate(event.to_dict(), tools))
    evaluation = run_suite("rule-based")
    EvaluationStore(data_dir / "metadata" / "evaluations.sqlite").save(evaluation)
    return {"database": database, "dbt": dbt_summary, "events": events, "evaluation": evaluation}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Build the offline synthetic demo")
    parser.add_argument("--data-dir", type=Path, default=Path("data/demo"))
    parser.add_argument("--no-dbt", action="store_true", help="skip dbt and write ready-made Gold tables")
    args = parser.parse_args(argv)
    result = build_demo(args.data_dir, use_dbt=not args.no_dbt)
    dbt = result["dbt"]
    print(f"Synthetic demo database: {result['database']}")
    print(f"dbt: {'built, tests ' + str(dbt.get('counts')) if dbt.get('ran') else dbt.get('reason')}")
    for event in result["events"]:
        print(f"  anomaly {event.anomaly_id}: {event.geography} {event.direction} {event.start_utc} (score {event.max_abs_score:.1f})")
    evaluation = result["evaluation"]
    print(f"Scenario Lab: {evaluation['cases_passed']}/{evaluation['cases_total']} synthetic cases pass every check")
    root = args.data_dir.as_posix()
    print("\nStart the API on the demo data (PowerShell):")
    print(f'  $env:EUROGRID_DB_PATH="{root}/eurogrid_demo.duckdb"; $env:EUROGRID_DATA_DIR="{root}"; '
          f'$env:EUROGRID_DBT_DIR="{root}/dbt"')
    print("  .\\.venv\\Scripts\\python.exe -m uvicorn eurogrid.api:app --host 127.0.0.1 --port 8000")
    print("Then in another terminal:  npm --prefix frontend run dev   and open http://localhost:5173")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

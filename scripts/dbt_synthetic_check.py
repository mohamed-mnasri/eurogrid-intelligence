"""Build the synthetic source data and run the real dbt project on it (models + tests).

Used by CI and runnable locally; needs no production data and no API credentials:

    python scripts/dbt_synthetic_check.py

Exits non-zero if dbt is missing, a model fails to build or any dbt test fails.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eurogrid.chaos import dbt_executable, run_dbt  # noqa: E402
from eurogrid.synthetic import build_synthetic_sources  # noqa: E402


def main() -> int:
    if dbt_executable() is None:
        print("dbt executable not found: create .venv-dbt (pip install -r requirements-dbt.txt) "
              "or set EUROGRID_DBT_EXECUTABLE")
        return 2
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        database = build_synthetic_sources(root / "synthetic.duckdb")
        result = run_dbt(database, root / "dbt")
    if not result.get("ran"):
        print(f"dbt did not run: {result.get('reason')}")
        return 1
    print(f"dbt build on synthetic sources: {result['counts']} (exit code {result['exit_code']})")
    for item in result["results"]:
        if item["status"] not in {"pass", "success"}:
            print(f"  {item['status'].upper()}: {item['name']} ({item.get('message')})")
    return 0 if result["exit_code"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

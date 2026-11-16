# Phase 2: Raw data lake and ingestion metadata

Phase 2 adds a repeatable local Bronze layer. Connector responses are saved as content-addressed JSON and retain the full source payload, request parameters, requested time, geography, terms, license and attribution fields. Paths partition by provider, dataset, geography, year and month, then request identity. A changed response creates a new SHA-256 version; an unchanged response is stored once. Local data and caches remain gitignored.

`data/metadata/ingestion_runs.sqlite3` records each successful or failed run, including provider/dataset/endpoint, request parameters and request hash, start/end time, status, returned row count, cache status, raw hash/path, and error detail. Cache keys use provider, endpoint, dataset and canonical parameters. The default cache lifetime is 24 hours; `--force` bypasses it. Phase 1's six-call sample runner remains available for the original source survey.

## Commands

Run from the repository root in the VS Code PowerShell terminal:

```powershell
python -m eurogrid.cli price --zone DE-LU --start 2025-01-01 --end 2025-01-02
python -m eurogrid.cli generation --country all --start 2025-01-01 --end 2025-01-02
python -m eurogrid.cli weather --latitude 52.52 --longitude 13.405 --start 2025-01-01 --end 2025-01-02
python -m eurogrid.cli backfill --connector price --zone DE-LU --start 2025-01-01 --end 2025-01-07 --chunk-days 1
python -m eurogrid.cli stats
```

Backfills accept an inclusive window of at most 31 days per invocation, with chunk sizes from 1 to 31 days. Energy-Charts price requests remain bidding-zone scoped; generation supports its `country=all` pan-European response. Rerun an identical command to use the cache and see another ledger run without a duplicate raw file. Add `--force` to make a new API call and preserve a new version if the source changed.

## Verify locally

```powershell
python -m unittest discover -s tests -v
python -m eurogrid.cli stats
Get-ChildItem data\raw -Recurse -Filter response_*.json
```

No network calls are made by the unit tests. The CLI's connector commands make read-only public API calls.

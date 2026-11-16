# EuroGrid Intelligence

European Grid-to-Home Energy Intelligence Platform

EuroGrid will combine European electricity-market data with transparent household solar and battery scenarios. Its market analysis and energy calculations will be deterministic; AI will later investigate verified results and explain evidence with clear limitations.

## Current stage

**Phase 0: project skeleton and source registration.** This repository currently contains project conventions, source/licence notes, setup guidance for Visual Studio Code, and read-only API probes. It does not yet contain production connectors, a database, analytics, an AI agent, or a frontend.

The architecture is pan-European. Coverage is discovered from provider metadata and each endpoint's live response; it can differ by data family and date. Germany, France, Spain, Italy and the Netherlands are the first validation markets in the handbook, not the geographic limit. Energy-Charts public generation supports `country=all`, which the Phase 0 probe has verified. Day-ahead prices require one bidding zone per request and have zone-specific licensing, so an all-Europe price request is not available from that API. See `docs/sources.md`.

## Phase 0 setup in Visual Studio Code

1. Install Python 3.11 or newer if needed, then open this folder in VS Code.
2. Open **Terminal → New Terminal** and create the environment:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

   If PowerShell blocks activation, use `\.venv\Scripts\python.exe` directly in the following commands.
3. In VS Code, choose the `.venv` interpreter (`Ctrl+Shift+P` → **Python: Select Interpreter**).
4. Run the initial public-source checks:

   ```powershell
   python scripts/phase0_probe.py
   ```

   The probes make a small number of read-only requests, print response metadata and a short sample, and do not save provider data. They honor the Energy-Charts low request rate by making at most one request per endpoint on each run. See `docs/sources.md` for the source and licence inventory.

## Run the Phase 0 tests

In the VS Code terminal, activate `.venv` as above and run the offline unit tests:

```powershell
python -m unittest discover -s tests -v
```

These tests check the all-Europe generation selector, zone-scoped prices, licence preservation, missing-value handling and the HTTP helper. They do not call any API. To repeat the live connectivity checks separately, run `python scripts/phase0_probe.py` (this does make public read-only API requests).

## Data and security rules

- Treat source terms and returned licence metadata as authoritative for each dataset.
- Keep API tokens and private household data out of Git. `.env` is ignored; `.env.example` contains names only.
- Missing data stays missing; it is never silently changed to zero.
- Wholesale prices are not household retail tariffs.
- Simulated household profiles and financial assumptions must be labeled as such.

## Next phase

Stop after Phase 0. Once its exit criteria are met, Phase 1 will design reusable API clients and connector contracts from the actual response shapes and licence metadata.

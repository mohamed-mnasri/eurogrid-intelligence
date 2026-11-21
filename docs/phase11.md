# Phase 11: Grid-to-Home event replay and chaos mode

## Event replay

**Event Replay** (`#/replay/<anomaly_id>`, `eurogrid/replay.py`) takes a detected market event and a household simulated in the planner. It replays the event's local calendar day(s) hour by hour.

**Inputs**

- **Prices:** actual day-ahead prices for those hours (fails closed if any hour is missing).
- **Usual prices:** for each hour, the median price for the same UTC hour over the prior 28 days.
- **Weather and PV:** actual Open-Meteo weather at the household's location for exactly those days. PV is modelled from it (`WeatherModelProvider.per_kwp_window`).
- **Household:** the scenario's load profile for the event year, and its systems. Each configuration is compared, along with "no solar or battery".

**Tariffs.** Each configuration is costed under:

- the household's own tariff, and
- an illustrative dynamic tariff on the event zone.

Each tariff is costed at actual prices and at usual prices. The difference is the **event premium**. Event-hour costs are shown separately.

**What the market did.** The page reuses the event's latest investigation report, or creates a rule-based one, so it explains what was measured alongside the household counterfactual.

**Assumptions.** Each result lists its assumptions:

- it is a counterfactual simulation;
- a 24-hour battery warm-up runs first;
- household load is synthetic;
- the battery follows a self-consumption rule and is not price-optimised;
- the dynamic tariff uses illustrative components;
- standing charges are excluded.

**The report.** `GET /api/replays/{id}/export?format=markdown` downloads the event-to-household report.

Example (DE-LU spike, 14 Sep 2026, Berlin household of 3,500 kWh/yr):

| Configuration | Fixed tariff premium | Dynamic tariff premium |
|---|---|---|
| No solar or battery | €0.00 | €1.49 |
| 4 kWp PV | €0.00 | €0.96 |
| 4 kWp + 5 kWh battery | €0.00 | €0.04 |

## Chaos mode

**Chaos mode** is a panel on Data Operations and a CLI (`eurogrid/chaos.py`). It runs a test-only failure-injection experiment:

1. Copy a slice of the source tables around an event into `data/chaos/<run_id>/clean.duckdb` and `faulty.duckdb`. Production is attached read-only.
2. Inject one fault into the faulty copy:
   - `missing_feed`: the event country's generation rows vanish for the event day(s);
   - `malformed_feed`: the event zone's prices arrive twice, and half the hours carry an invalid status.
3. Run the real dbt project (`dbt build`) on both copies, using a throwaway profile. Artifacts go to the run folder, never to `eurogrid_analytics/target`.
4. Re-run the data-quality gate and the investigation on both copies. The AI agent can be included as an option.
5. Record:
   - failing dbt tests;
   - downstream models, from dbt lineage, and the app outputs that read them;
   - quality status for each domain, clean versus broken;
   - checks that the report is not complete, names the missing evidence, makes no causal claims and has no proven causes.

**Measured on real data (event of 14 Sep 2026)**

| Fault | dbt caught it | Quality gate | Rule-based report | AI agent report |
|---|---|---|---|---|
| Missing generation feed | no (24/24 tests pass) | generation: missing | limited; names generation; 0 causal claims | — |
| Malformed price feed | yes: `unique_gold_market_price_fact_key`, `accepted_values_..._price_status` | price: partial (50%) | limited; names price; 0 causal claims | limited; names price; 0 causal claims |

The missing-feed result is a real finding. The dbt schema tests (unique, not null, accepted values) cannot detect absent rows; only the investigation's quality gate catches a gap.

Chaos mode also exposed a gap that is now fixed: a domain that was only *partly* available was not named in a report's missing evidence. Both investigators now name it, with the share of valid hours.

```powershell
.\.venv\Scripts\python.exe -m eurogrid.chaos --fault missing_feed --anomaly-id anm_7b8b94ca16d4dd71
```

## Synthetic source data

`build_synthetic_sources` in `eurogrid/synthetic.py` writes the synthetic dataset as **source** tables (`dim_geography`, `fct_*`). Every row carries `provider='synthetic'`. The real dbt project builds it (12 models, 24 tests pass), so chaos tests and CI run without production data.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_phase11 -v
```

The real-dbt chaos test is skipped if no dbt executable is found. Set `EUROGRID_DBT_EXECUTABLE`, or use `.venv-dbt`.

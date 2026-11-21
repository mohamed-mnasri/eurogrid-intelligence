# Phase 6: Anomaly detector and deterministic investigations

Phase 6 finds unusual day-ahead price hours and investigates them **without an LLM**. Every number comes from a fixed, parameterised query against the tested Gold models, and every report statement points to an evidence record.

## The pieces

| File | What it does |
|---|---|
| `eurogrid/anomaly.py` | Pure scoring maths: comparable-hour baseline, robust z-score, grouping hours into events. No database access. |
| `eurogrid/investigation_tools.py` | The approved, read-only tools (prices, baseline comparison, generation mix, demand, cross-border trading, outages, weather, neighbouring markets, data quality). Pydantic inputs/outputs; each result carries an evidence record. |
| `eurogrid/investigation.py` | Detection runs, the SQLite case store, and the rule-based report builder. |
| `eurogrid/investigate_cli.py` | Terminal commands: `detect`, `list`, `report`. |

## How an hour is scored

For each valid hourly price, the detector collects earlier prices from the **same local hour and same day type** (weekday or weekend) over the previous **28 days**. The current hour and later hours are never part of its own baseline.

```
median = median(reference prices)
MAD    = median(|reference price − median|)
score  = 0.6745 × (price − median) / max(MAD, mad_floor)
```

- An hour is anomalous when `|score| ≥ 5.0` and there are at least **8** reference values.
- `mad_floor` (10 EUR/MWh) matters because midday solar hours can sit near 0 EUR/MWh for weeks. Without it, a move from €0 to €9 scored over 300 on real German data. With the floor, the real outliers stand out (for example −€414/MWh on 26 Apr 2026 and €697/MWh on 14 Sep 2026 in DE-LU).
- Flagged hours in the same direction, separated by at most one unflagged hour, become one **event** (`spike` or `dip`).
- The event ID is a hash of the zone, metric, start time, detector version and settings. Re-running detection overwrites events; it does not duplicate them.

All settings are stored inside each event and can be changed with CLI flags (`--window-days`, `--min-observations`, `--threshold`, `--mad-floor`).

## How a report is built

`investigate` always runs the same tools in the same order. It applies written rules (stored in each report under `rules`):

| Check | Rule | Where it goes |
|---|---|---|
| Wind + solar vs the same hours on the previous 14 days | ≥ 15% lower for a spike (higher for a dip) | coinciding + plausible factor |
| Public-grid load vs comparable hours | ≥ 5% higher for a spike (lower for a dip) | coinciding + plausible factor |
| Net scheduled cross-border trading | changed by ≥ 500 MW | coinciding (provider sign kept, no import/export claim) |
| Connected zones' prices | at least half moved ≥ 20% the same way | "shared across connected markets", otherwise "mainly local" |
| Weather at the reference point | always reported as context | observation |
| Outages | ENTSO-E is not ingested | always listed under missing evidence |

The report separates **observations**, **relationships that coincide with the event**, **plausible contributing factors**, and **proven causes**. Proven causes is always empty, because public data cannot prove the price mechanism.

Confidence is a set of per-domain factors (price, generation, load, flows, outages, weather, time alignment), each with a status and completeness ratio. There is no single "confidence %". The report status is:

- `blocked` if price data is missing,
- `limited` if price, generation or load is incomplete,
- `complete` otherwise.

Missing data is never treated as zero. A missing generation feed produces a "could not be compared" entry instead of a finding.

## Try it

The case store is `data/metadata/investigations.sqlite`. Reports are also written to `data/investigations/` as JSON and Markdown. Both are gitignored.

```powershell
.\.venv\Scripts\python.exe -m eurogrid.investigate_cli detect --zone DE-LU --start 2026-01-01 --end 2026-10-31
```

```powershell
.\.venv\Scripts\python.exe -m eurogrid.investigate_cli list --zone DE-LU --limit 10
```

```powershell
.\.venv\Scripts\python.exe -m eurogrid.investigate_cli report --anomaly-id <id from the list>
```

Approved zones are the bidding zones with ingested prices (DE-LU, FR, ES, NL, PL, and IT-North for its short sample). Detection runs are limited to 366 days, and each tool call to 31 days.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_anomaly tests.test_investigation -v
```

- `tests/test_anomaly.py` checks the score against a hand calculation. With reference values 10…17 (median 13.5, MAD 2.0), a price of 40 scores 0.6745 × 26.5 / 2 = 8.937. It also covers the minimum-data rule, the MAD floor, no look-ahead, weekday/weekend separation, local time zones and stable event IDs.
- `tests/test_investigation.py` builds a synthetic DuckDB (`tests/sample_db.py`) with a known German spike, 90% lower wind and 10% higher load. It checks that the spike is detected once, that the report contains the expected findings with evidence IDs, that results repeat exactly, and that a missing generation feed produces a `limited` report.

## Known limitations

- No load forecast and no outage records are ingested, so those hypotheses are always listed as unchecked.
- Cross-border data is country-level net scheduled trading, not per-border physical flows.
- The neighbour map is a small static list among the ingested zones (`NEIGHBOUR_ZONES`).

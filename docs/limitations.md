# Known limitations

These are stated plainly so nobody reads more into the results than the data supports.

## Data

- **Not live.** Data is whatever was last ingested; the app flags anything older than 7 days as stale.
- **Public-power series are not a full system balance.** Generation and grid load come from Energy-Charts public-power data. Industrial self-supply, storage and imports mean the two will not add up.
- **No outage data.** ENTSO-E needs a personal token and is not connected, so every report says outage evidence could not be checked.
- **No load forecasts**, so forecast error cannot be assessed.
- **Price licences vary by zone.** Day-ahead prices are CC BY 4.0 only for named zones; others are internal-use only. Nothing in `data/` is committed or redistributed.
- **"All Europe" is not an official price.** It is an equal-weight average of the ingested country zones.
- **One weather point per country.** Weather comes from a single reference city, not a national average.

## Analytics and AI

- **Detection.** The anomaly detector is a transparent statistical rule: median/MAD of the same local hour and day type over 28 days, with threshold 5. It finds unusual hours, not market abuse or causes.
- **No proven causes.** Reports separate observations, coinciding changes and plausible factors. Proven causes are always empty, because public data cannot establish the price-formation mechanism.
- **The agent is checked, not trusted.** The local model's findings are kept only when code can verify their evidence, wording and materiality.
  - A small model (qwen3.5:4b) can still choose an odd tool order. Code checks keep its reports honest; they cannot make them insightful.
  - The first evaluation run showed the model misfiling unchanged series as relationships. That is why code now re-files those as observations.
- **The evaluation suite is small and synthetic.** It has seven controlled cases. Passing it shows the guardrails work on known conditions; it is not an accuracy figure for real events.
- **The evaluation does not cover:** outage records, timestamp shifts and duplicate intervals in the investigator. Duplicates are caught by dbt tests in chaos mode.
- **dbt tests can't see gaps.** The schema tests (unique, not null, accepted values) cannot detect missing rows; chaos mode shows this. Gaps are caught by the investigation quality gate and the Data Operations coverage matrix, not by dbt.

## Household simulation and replay

- **Simulated estimates, not bills or quotes.** Default system costs are placeholders; replace them with an installer quotation.
- **Synthetic load.** Household load is a synthetic weekday/weekend profile with a seasonal factor, unless measured hourly load is supplied.
- **PV source.** PVGIS hourly data ends in 2023. Later years use the documented weather-based PV model, which gave +6.2% versus PVGIS for Berlin in 2023.
- **Simple battery.** The battery follows a self-consumption rule (charge from solar surplus, discharge to cover load). It is not optimised against prices, so savings on a dynamic tariff are understated.
- **Illustrative dynamic tariff.** It uses stated components (markup, grid charges, levies, VAT), not a supplier offer. Standing charges are excluded from the replay.
- **Linear finance.** Future years apply degradation and price escalation to year-one savings, a linear approximation, instead of re-simulating every year.
- **Replay scope.** The replay covers the event's local day(s) with a 24-hour warm-up. It shows the effect of one event, not of a typical month.

## Engineering

- **Single-user local tool.** No authentication or multi-user isolation; the API binds to `127.0.0.1`.
- **Long requests block.** The agent and chaos runs are synchronous HTTP requests (30–100 s). There is no job queue.
- **CI scope.** CI runs on synthetic data only, and the real-data pipeline is not run in CI. CI never calls Ollama; agent tests use a fake model.
- **No Docker Compose.** It was considered and left out because it would not make startup simpler. Ollama, the dbt environment and the Vite dev server already start with one command each, and Ollama on Windows works best natively. See `docs/phase12.md`.

# Demo script (about 4 minutes)

A walkthrough for recording the portfolio video. Use real data if you have it ingested, or the synthetic demo (`python -m eurogrid.demo`). The synthetic demo shows a banner, so keep it visible and say that it is synthetic.

Before recording:

- start Ollama;
- start the API;
- start `npm --prefix frontend run dev`;
- open http://localhost:5173.

| # | Time | Page | Show | Say (in your own words) |
|---|---|---|---|---|
| 1 | 0:00 | Market Overview | Country and month selectors; wholesale vs household price chart | "Public European market data, ingested, normalized and modelled with dbt. The wholesale price is a fraction of what households pay." |
| 2 | 0:30 | Market Analytics | Country lines, the month × hour heatmap, the anomaly scatter | "Solar pushes midday prices down in summer; evenings spike. Each dot is an hour flagged by a transparent median/MAD rule." |
| 3 | 1:00 | AI Investigations | Open the biggest spike; click **Run AI agent**; wait | "A local model, running on this PC, chooses which approved tools to call. It can't write SQL or choose the country or dates." |
| 4 | 1:40 | Findings → Activity trace | Evidence chips; the graph path; "chosen by code (model repeated a tool)" | "Every statement links to a stored tool call. Code removes anything without evidence, with causal wording, or that calls an unchanged series a relationship. Proven causes stay empty." |
| 5 | 2:10 | Event Replay | Choose the event and the Berlin household; **Replay event** | "The same household that day: on a fixed tariff the spike costs nothing; on a dynamic tariff it adds €1.49; a 5 kWh battery cuts that to €0.04. It's a counterfactual, and the page says so." |
| 6 | 2:50 | Data Operations → Chaos mode | Run **Missing generation feed** | "Break a copy of the data and re-run dbt and the investigation. All 24 dbt tests still pass, because schema tests can't see missing rows. The quality gate catches it, and the report turns limited and names what's missing." |
| 7 | 3:30 | Scenario Lab | Agent vs rule-based matrices; run history 1/7 → 7/7 | "Seven synthetic cases with known causes. The first agent run failed six of them by overstating relationships. A code check fixed it, and I kept the failing run in the history." |
| 8 | 3:50 | — | — | "Everything is local and free, with no API keys. The code, tests and CI are on GitHub." |

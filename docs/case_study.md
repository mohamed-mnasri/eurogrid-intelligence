# Case study draft (LinkedIn)

*A draft to edit into your own voice before posting. Every number comes from the project's own runs. Check them against your latest data before publishing.*

---

**I built a European electricity-market investigator that runs a local AI model, then spent most of the effort stopping the AI from overclaiming.**

EuroGrid Intelligence takes public market data and does three things with it:

1. it finds unusual price hours;
2. it investigates them with evidence;
3. it shows what an event would have meant for a household with solar and a battery.

Everything runs locally on free, keyless sources: Energy-Charts, Open-Meteo, PVGIS and Eurostat.

**The pipeline.** Python connectors with retries and a run ledger feed raw, content-addressed files. A normalizer turns them into DuckDB. A dbt project builds tested Gold models (42 data tests, plus source freshness), orchestrated by a pipeline command that loads only new files and records every stage. A FastAPI layer has bounded, typed endpoints, and a seven-page React app sits on top.

**The AI part, and why it's small.** A local Qwen 3.5 (4B) model runs in Ollama inside a LangGraph workflow. The model does only two things: it picks the next tool from an allowlist, and it drafts findings that cite evidence IDs. Code does everything else:

- fills in the country and time window;
- caps the number of tool calls;
- runs a data-quality gate;
- removes statements without valid evidence or with causal wording;
- sets status and confidence by rule.

There is no SQL input anywhere, and "proven causes" is always empty. Public data can show what coincided with a price spike, not prove why it happened.

**Measuring it instead of trusting it.** I built seven synthetic cases with known injected conditions: a wind drop, a demand spike, an import swing, a regional spike, no clear cause, a missing generation feed and a missing price feed. Each case is scored on nine checks.

- The rule-based investigator passed 7/7.
- The first agent run passed **1/7**. The model labelled unchanged series as "relationships coinciding with the event".
- I added a code check: a relationship must cite evidence that changed by the same thresholds the rule-based engine uses. Otherwise it is re-filed as an observation. The re-run passed **7/7**, and **7/7 again in each of three repeated runs** with 0 failed model calls.
- Each agent investigation costs a median of 41 s, 7 model calls, and about 7.7k prompt plus 1.4k output tokens on a local GPU (no API bill). The rule-based engine answers in 0.3 s. Both numbers are recorded with every run.

Both runs stay in the history.

**Breaking it on purpose.** Chaos mode copies a slice of real data and breaks the copy. Then it re-runs the real dbt build, the quality gate and the investigation.

- A *malformed* price feed failed two dbt tests (duplicate keys, invalid status).
- A *missing* generation feed passed **all 24 dbt tests**, because schema tests can't see rows that aren't there. Only the investigation's quality gate caught it, and the report turned "limited" and named the missing evidence.
- So I wrote a generic hourly-gap dbt test (and relationship, status and freshness checks). The same experiment on real data now fails the build: 42/42 on the clean copy, the gap test failing on the broken one.
- The chaos runs also showed that partially missing data wasn't being named in reports. That's fixed now.

**Grid-to-Home.** Replaying a real German price spike (14 Sep 2026, peak €697/MWh) against a simulated Berlin household gave these results for that day:

- a fixed tariff absorbed it completely;
- an illustrative dynamic tariff added €1.49;
- 4 kWp of solar cut that to €0.96;
- solar plus a 5 kWh battery cut it to €0.04.

The report labels this as a counterfactual and lists every assumption.

**What I'd tell someone building something similar:** the model is the easy part. The value is in what surrounds it: evidence records, deterministic checks, an evaluation set you're willing to fail, and failure tests that tell you where your data tests are blind.

Stack: Python, DuckDB, dbt, FastAPI, Pydantic, LangGraph, Ollama, React, ECharts, GitHub Actions.

*[link to repository] · [link to demo video]*

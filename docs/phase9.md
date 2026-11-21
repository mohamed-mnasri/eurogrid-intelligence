# Phase 9: Local LLM investigation agent

Phase 9 adds a LangGraph agent that investigates a saved anomaly using a local model served by [Ollama](https://ollama.com) (default `qwen3.5:4b`). No data leaves the machine.

## What the model does, and what code does

| Model | Code |
|---|---|
| picks the next approved tool by name | loads the anomaly; fills in the zone and window |
| drafts findings that cite evidence IDs | runs the data-quality gate (blocks when price data is missing) |
| | enforces the policy: allowed tools, zone and window, at most `--max-tool-calls` calls |
| | makes sure the price-baseline comparison always runs |
| | drops findings with no, unknown or unusable evidence, or with causal wording |
| | sets confidence factors and status, and saves the report and trace |

Replies from the model are forced into a JSON schema (Ollama `format=`). If a reply is invalid, code falls back to the next unused tool, and the trace records that code made the choice.

## Graph

```
load_event → quality_gate ─(price missing)─────────────────────────┐
                 │                                                 │
                 ▼                                                 │
            choose_tool ⇄ run_tool                                 │
                 │                                                 ▼
          validate_evidence → write_report → check_report → END
```

Modules: `eurogrid/agent_tools.py` (typed tool names, policy), `eurogrid/llm.py` (Ollama wrapper), `eurogrid/agent.py` (graph and CLI).

## Run

```powershell
ollama pull qwen3.5:4b
```

```powershell
.\.venv\Scripts\python.exe scripts\phase9_ollama_check.py
```

```powershell
.\.venv\Scripts\python.exe -m eurogrid.agent --anomaly-id anm_7b8b94ca16d4dd71
```

Outputs:

- `data/investigations/<report_id>.md`, the report;
- `data/investigations/<report_id>_trace.json`, which holds the node path, each tool call and who chose it, the model's draft before checking, and the missing evidence;
- the case store, with generator `langgraph-ollama:<model>`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_agent -v
```

The tests use a scripted fake model, so they don't need Ollama. They cover:

- tool choice by the model;
- removal of findings with missing or fake evidence and of causal claims;
- the tool budget;
- invalid model replies;
- outages counted as missing evidence;
- a failed report;
- policy blocks;
- the blocked path.

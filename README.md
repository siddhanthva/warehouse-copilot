# Warehouse Copilot

**Ask a Medicare reimbursement warehouse questions in plain English. Get the answer, the exact SQL, a table and a chart, with SQL validated in code and a human approval step for ambiguous or expensive queries.**

![CI](https://github.com/YOUR_GITHUB_USER/warehouse-copilot/actions/workflows/ci.yml/badge.svg)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue) ![LangGraph](https://img.shields.io/badge/LangGraph-1.2-purple) ![MCP](https://img.shields.io/badge/MCP-FastMCP-black) ![Gemini on Vertex AI](https://img.shields.io/badge/Gemini-Vertex%20AI-4285F4)

**Live demo:** TODO(me): Cloud Run URL · **Demo video:** TODO(me) · ![demo](docs/demo.gif)

## The problem
TODO(me): 2–3 sentences in your own words.

## Architecture
See [`docs/architecture.mmd`](docs/architecture.mmd) (export to `docs/architecture.png`).

```
Question → select_tables → generate_sql → validate (sqlglot) → check_approval (EXPLAIN via MCP)
             ↑ retry ≤ 2 ←──────────────┘        │ ambiguous / expensive
             ↑ clarification ← human_review (LangGraph interrupt) → approve → execute (MCP) → summarize
```

## How it works
- **7-step LangGraph agent** (`src/warehouse_copilot/agent/graph.py`): the model only picks tables, writes one SQL draft (Pydantic structured output) and summarizes. Validation, retry caps and approval rules are plain code.
- **MCP server** (`mcp_server.py`, FastMCP over stdio) with `list_tables`, `describe_table`, `run_readonly_query`, `estimate_query_cost`, `get_metric_definition`. The agent never touches the database directly; Claude Desktop can use the same server.
- **SQL guard** (`sql_guard.py`): single SELECT only, no writes anywhere in the tree (including inside CTEs), no dangerous functions, allow-listed tables, LIMIT ≤ 200.
- **Human approval**: ambiguous questions pause for a clarification; queries whose plan scans more than a threshold pause for Approve / Reject. State survives between requests in a Postgres checkpointer.
- **Self-correction**: guard violations and database errors go back to the model, with one shared budget of 2 retries.
- **Metadata over prompt tricks**: table/column descriptions come from dbt's `manifest.json` / `catalog.json`; business metrics from `metrics.yaml`.
- **Tracing**: every run is a Langfuse trace with a span per node and model call (tokens, latency, cost).

## Evaluation
TODO(me): paste the tables from `evals/results/*.md` for both models.

| Model | Exec. accuracy | Easy | Medium | Hard | Valid SQL | p50 / p95 | $/question |
|---|---|---|---|---|---|---|---|
| TODO | | | | | | | |

Execution match compares values (not column names), ignores row order unless the question needs it, and rounds numerics to 2 decimals. Nightly CI fails if accuracy drops more than 3 points below `evals/baseline.json`.

## Security design
Two independent layers: the sqlglot guard, and a `copilot_ro` Postgres role that is read-only with a 10 s statement timeout. `tests/test_injection.py` runs 23 attacks (stacked statements, writes hidden in CTEs, `COPY TO PROGRAM`, `pg_sleep`, `pg_shadow`, unicode-obfuscated keywords, prompt injection asking for credentials, ...) under the assumption that **the model is fully compromised**: each is checked at the guard, at the MCP tool and through the whole agent, and none reaches the database. A separate test bypasses the guard entirely and shows the database role refusing writes.

## Design decisions
TODO(me): in your own words. Starting points are in `CLAUDE.md` → Decisions (why validation lives in code, why MCP, the approval threshold).

## Limitations and next steps
TODO(me).

## Run locally
```bash
cp .env.example .env          # fill in
uv sync --dev
uv run pytest -q              # unit tests, model mocked
uv run streamlit run app/streamlit_app.py
```
Full setup (GCP, roles, dbt docs, WIF, Cloud Run): [`docs/SETUP.md`](docs/SETUP.md).

Try it without any cloud account against a synthetic local Postgres:
```bash
createdb warehouse && psql -d warehouse -f scripts/dev_seed.sql
psql -d warehouse -v ro_password="'dev'" -v app_password="'dev2'" -v schema=marts -f scripts/setup_readonly_role.sql
RUN_INTEGRATION=1 DATABASE_URL=postgresql://copilot_ro:dev@localhost/warehouse uv run pytest -q -m integration
```

## Tech stack
Python 3.11 · LangGraph · LangChain · Gemini on Vertex AI · MCP Python SDK (FastMCP) · langchain-mcp-adapters · sqlglot · PostgreSQL · dbt · Streamlit · Langfuse · pytest · GitHub Actions (Workload Identity Federation) · Docker · Cloud Run

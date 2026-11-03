# Warehouse Copilot

## Goal
A text-to-SQL analytics agent over my Hospital Reimbursement Intelligence warehouse (dbt project
`cms_spine` in `C:\projects\healthcare-analytics`, hosted on Neon Postgres, schema `dbt_dev`). Users ask in plain
English and get an answer, the SQL, a table and a chart. The agent reaches the database only
through my MCP server, SQL is validated in code, ambiguous or expensive queries pause for
human approval, and accuracy is measured on a golden set in CI.

## Levels (build in this order — one level per session)
0 Setup · 1 Golden set · 2 Data access · 3 MCP server · 4 LangGraph agent ·
5 SQL guard + approval · 6 Attack tests · 7 Evals + CI · 8 Tracing, UI, deploy ·
9 Packaging · 10 Real users

Status: code for levels 3–8 exists and is tested with a mocked model. Levels 0, 1, 2 and 10
need real accounts/data (see docs/SETUP.md). Level 9 docs have sections marked for me to write.

## Data facts (affect question design)
- ONE time period only: Medicare inpatient = single DY24 file, cost report = 2023. No year column
  in fct_medicare_drg, so NO year-over-year questions; hard tier = window functions, CTEs, ranks
  within groups, percentiles, multi-fact joins.
- Allow-listed marts (9, see config.py): dim_hospital, dim_drg, dim_geography, fct_medicare_drg
  (hospital x DRG, per-discharge averages), fct_hospital_finance (1 row/hospital),
  mart_finance_scorecard, mart_reimbursement_gap, mart_service_line_margin, mart_burden_vs_community.
- Neon free tier = 0.5 GB and suspends when idle: connections need retry.
- tests/fixtures + scripts/dev_seed.sql use a SYNTHETIC `marts` schema on purpose; they are
  independent of the real warehouse (conftest overrides ALLOWED_TABLES / DEFAULT_SCHEMA).
- Golden-set SQL and results are verified by the human, never auto-accepted. Candidate questions:
  evals/question_candidates.md.

## Stack
Python 3.11, LangGraph 1.2, LangChain core 1.7, Gemini on Vertex AI via
`langchain-google-genai` (`ChatGoogleGenerativeAI(vertexai=True)`), MCP Python SDK 1.x
(`mcp.server.fastmcp.FastMCP`), langchain-mcp-adapters 0.3, sqlglot 30, psycopg 3,
PostgreSQL (Neon/Supabase), dbt metadata, Streamlit, pytest, Langfuse 5, Docker,
Cloud Run, GitHub Actions (Workload Identity Federation).

## Layout
- `src/warehouse_copilot/config.py` env vars, model names, limits, allow-list
- `src/warehouse_copilot/sql_guard.py` sqlglot validation + LIMIT injection (layer 1)
- `src/warehouse_copilot/db.py` read-only connection, timeouts, EXPLAIN (layer 2 lives in the DB role)
- `src/warehouse_copilot/schema_docs.py` dbt manifest/catalog → table docs; metrics.yaml loader
- `src/warehouse_copilot/mcp_server.py` FastMCP server, 5 tools
- `src/warehouse_copilot/agent/` state, prompts, graph, LLM factory, MCP tool client
- `src/warehouse_copilot/runtime.py` sync facade (background loop) used by Streamlit + evals
- `app/streamlit_app.py` UI · `evals/` golden set, runner, comparison · `tests/`
- `scripts/` role setup SQL, synthetic dev seed, Gemini smoke test, Cloud Run deploy

## Commands
- `uv sync --dev` then `uv run pytest -q` (unit, no network)
- `RUN_INTEGRATION=1 DATABASE_URL=... uv run pytest -q -m integration` (needs Postgres)
- `uv run python -m warehouse_copilot.mcp_server` / `npx @modelcontextprotocol/inspector uv run python -m warehouse_copilot.mcp_server`
- `uv run streamlit run app/streamlit_app.py`
- `uv run python -m evals.run_evals --model <name>`

## Rules
- Work on ONE level at a time. Propose a plan before writing code.
- Check current library docs before using LangGraph, MCP, langchain-mcp-adapters or Vertex APIs.
- Write or update tests with every change. Run pytest before saying a level is done.
- Never put secrets in code; read them from environment variables.
- Never call paid APIs in unit tests; mock the model (tests/fakes.py). Only evals make real calls.
- The app connects to the warehouse ONLY as the read-only role (DATABASE_URL).
- Keep functions small and typed. Docstrings explain WHY.
- After each level, update the Decisions section.

## SQL guard rules
| Rule | Check |
|---|---|
| Parses | `sqlglot.parse(sql, read="postgres")` succeeds (after NFKC normalisation, zero-width chars removed) |
| Single statement | Exactly one statement (blocks `SELECT 1; DROP ...`) |
| SELECT only | Root is SELECT or UNION/INTERSECT/EXCEPT (CTEs allowed) |
| No write/DDL anywhere | Rejects INSERT, UPDATE, DELETE, MERGE, DROP, CREATE, ALTER, TRUNCATE, GRANT, REVOKE, COPY, SET, SELECT INTO, FOR UPDATE, raw commands anywhere in the tree |
| No dangerous functions | Blocks `pg_*`, `lo_*`, `dblink*`, `set_config`, `current_setting`, `query_to_xml`, `version`, ... |
| Allow-listed tables | Every table (excluding CTE names) is in ALLOWED_TABLES; unqualified names resolve to DEFAULT_SCHEMA |
| Row limit | Adds `LIMIT 200` if absent; caps larger or non-literal limits at 200 |
Executed SQL is regenerated from the validated tree with comments stripped.

## Decisions
- 2026-11-01 Vertex via `ChatGoogleGenerativeAI(vertexai=True)`: `ChatVertexAI` is deprecated since langchain-google-vertexai 3.2 (removal in 4.0).
- 2026-11-01 Gemini 3+: temperature left unset (library default per Google guidance), `thinking_level=low` for cost/latency.
- 2026-11-01 `mcp<2` pinned because langchain-mcp-adapters 0.3 requires it.
- 2026-11-01 Added a 5th MCP tool `estimate_query_cost` so the approval gate's EXPLAIN also goes through MCP (agent never touches the DB directly).
- 2026-11-01 Approval threshold uses the planner's *largest row estimate anywhere in the plan* (rows scanned), not the top node's rows: the top node is always ≤ 200 because of the LIMIT. Started at 1,000,000 (APPROVAL_ROW_THRESHOLD); tuned on 2026-11-03, see below.
- 2026-11-01 Separate `human_review` node holds `interrupt()`; `check_approval` is deterministic. Interrupted nodes re-run from the top on resume, so the node with side effects (EXPLAIN) must not be the one that pauses.
- 2026-11-01 One retry budget (max 2) shared by guard failures, EXPLAIN errors and DB errors; plus recursion_limit 40 as a hard step cap.
- 2026-11-01 Clarifications capped at 2 per question; after that the agent answers with stated assumptions.
- 2026-11-01 Checkpointer + daily counter use a separate writable role/schema (`copilot_app`/`app_state`, APP_STATE_DATABASE_URL). The read-only role can't write checkpoints, and the app must never hold write access to the warehouse.
- 2026-11-01 Draft stored in state as a dict (not a Pydantic object) so checkpoints serialize without registering custom types.
- 2026-11-01 Questions over 2,000 chars are refused before any model call.
- 2026-11-02 Merged the scaffold into this repo; allow-list/metrics rewritten for the real marts (schema `dbt_dev` is inferred from the Power BI model: VERIFY on Neon). dim_date excluded from the allow-list.
- 2026-11-02 setup_readonly_role.sql takes `-v schema=...` so the same script serves the synthetic CI schema and the real one.
- 2026-11-02 Gemini 3.x models return 404 on us-central1 for this project but work on the `global` endpoint, so GOOGLE_CLOUD_LOCATION=global. Cloud Run region is a separate CLOUD_RUN_REGION. Default model gemini-3.8-flash, comparison model gemini-3.5-flash-lite.
- 2026-11-02 Roles copilot_ro / copilot_app created on the existing Neon project (schema dbt_dev confirmed). Neon rejects weak role passwords, so they are generated. SQL had to drop 'AUTHORIZATION' from CREATE SCHEMA (admin cannot SET ROLE on managed Postgres). tests/test_real_warehouse.py (REAL_WAREHOUSE=1) proves layer 2 on the real warehouse; scripts/build_catalog.py builds catalog.json read-only.
- 2026-11-02 First live run: 5 easy questions answered correctly, 0 retries (gemini-3.8-flash). Fixes: Windows uses InMemorySaver (async psycopg cannot run on ProactorEventLoop, MCP stdio needs it); db.connect skips startup options on Neon pooled hosts (role settings already enforce them); .env copilot URLs use the direct host.
- 2026-11-03 Agent can decline: `SQLDraft.answerable=false` routes to a `decline` node (status `unanswerable`, no query runs). A first "unanswerable" verdict is re-checked once with every allowed table (`widened`, not counted as a retry) because a wrong table pick otherwise becomes a false "can't answer" (seen on q057). Unanswerable golden questions (q061-q063) pass only if the agent declines; reported separately from execution accuracy.
- 2026-11-03 Gold answers must fit the 200-row cap, so q043/q047/q048/q052 were reworded (counts per group, thresholds). Holdouts (q023, q028, q040, q045, q050) deliberately not yet written.
- 2026-11-03 Approval threshold set to 2,000,000 rows scanned. Measured on the real warehouse: the 55 gold queries have planner scan estimates with median about 145k (the size of the biggest table) and a maximum of 947,180 (q055, a legitimate 3-table join); a runaway cross join of fct_medicare_drg with itself estimates 21.2 billion. 1,000,000 left only about 5% headroom over the largest legitimate query; anything from about 2M to 100M separates legitimate from runaway, so 2M keeps normal analyst joins unpaused while still catching fan-out joins. Live check: that cross join paused with an approve interrupt; approve returned 200 rows, reject ran nothing.

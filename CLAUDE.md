# Warehouse Copilot

## Goal
Text-to-SQL analytics agent over my Hospital Reimbursement Intelligence warehouse (dbt project
`cms_spine`, repo `C:\projects\healthcare-analytics`, hosted on Neon Postgres). Users ask in plain
English and get an answer, the SQL, a table and a chart. The agent reaches the database only through
my MCP server, SQL is validated in code, ambiguous or expensive queries pause for human approval,
and accuracy is measured on a golden set in CI.

## Levels (one per session)
0 Setup · 1 Golden set · 2 Data access · 3 MCP server · 4 LangGraph agent · 5 SQL guard + approval ·
6 Attack tests · 7 Evals + CI · 8 Tracing, UI, deploy · 9 Packaging · 10 Real users

## Stack
Python 3.11 (uv), LangGraph, LangChain, Gemini on Vertex AI (credits), MCP Python SDK (FastMCP),
langchain-mcp-adapters, sqlglot, PostgreSQL (Neon free tier), dbt metadata (manifest/catalog copied
into metadata/), Streamlit, pytest, Langfuse, Docker, Cloud Run, GitHub Actions (WIF).
Free-only except Google credits. Public repo (free Actions minutes).

## Data facts (affect question design)
- ONE time period only: Medicare inpatient = single DY24 file, cost report = 2023. No year column
  in fct_medicare_drg. NO year-over-year questions; hard tier = window functions, CTEs, ranks within
  groups, percentiles, multi-fact joins.
- Marts: dim_hospital, dim_drg, dim_geography, dim_date, fct_medicare_drg (hospital x DRG),
  fct_hospital_finance (1 row/hospital), mart_finance_scorecard, mart_reimbursement_gap,
  mart_service_line_margin, mart_burden_vs_community.
- Neon free tier = 0.5 GB. Neon suspends when idle: retry connections.

## Rules
- ONE level at a time. Propose a plan before writing code.
- Check current library docs before using LangGraph, MCP, langchain-mcp-adapters or Google APIs.
- Write/update tests with every change. Run pytest before saying a level is done.
- Never read, print or commit .env or any secret. Read secrets from environment variables only.
- Never call paid/real model APIs in unit tests; mock the model. Only evals make real calls.
- The app connects ONLY as the read-only role (DATABASE_URL).
- Golden-set SQL and results are verified by the human, never auto-accepted.
- Keep functions small and typed. Docstrings explain WHY.
- After each level, update Decisions.

## SQL guard rules
Parses (sqlglot, postgres) · single statement · SELECT only (WITH...SELECT ok) · no write/DDL node
anywhere in tree · no dangerous functions (pg_sleep, pg_read_file, lo_import, dblink, set_config...)
· every table (excluding CTE names) in allow-list · LIMIT 200 added/capped.

## Decisions
- 2026-11-01: Separate repo from the Medicare dbt project; copilot reads its marts read-only.
- 2026-11-01: langchain-google-genai (vertexai=True) chosen over langchain-google-vertexai; verify at Level 4.

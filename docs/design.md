# Warehouse Copilot — design (one page)

## Problem
**TODO(me):** 2–3 sentences in your own words: who asks ad-hoc questions, what it costs analysts today.

## Users
Hospital finance analysts and managers who know the business questions but not the schema.

## Approach
A LangGraph state machine (see `docs/architecture.mmd`). The model does only three things:
pick tables, write one SQL draft as structured output, and summarize the result. Everything
else (validation, retry cap, approval policy, row limits) is plain, tested code. The agent
reaches the warehouse only through an MCP server, which re-validates every query.

## Guard design
Two independent layers:
1. **sqlglot guard** (`sql_guard.py`): parse as Postgres → exactly one statement → SELECT-family
   root → no write/DDL/COPY/SET/SELECT INTO/row locks anywhere in the tree (catches writes inside
   CTEs) → no dangerous functions (`pg_*`, `lo_*`, `dblink`, `set_config`, ...) → every table in the
   allow-list → LIMIT forced to ≤ 200. Executed SQL is regenerated from the tree, so comments
   can't smuggle anything.
2. **Database role** `copilot_ro`: SELECT-only grants on the marts, `default_transaction_read_only`,
   `statement_timeout = 10s`; each session is also opened read-only by `db.py`.

## Approval policy
- Ambiguous question (model sets `ambiguous=true`) → pause with a clarifying question; max 2 rounds.
- Expensive query: EXPLAIN (no ANALYZE) through MCP; if the largest row estimate in the plan exceeds
  `APPROVAL_ROW_THRESHOLD` (start: 1,000,000) → pause with the SQL and estimate for Approve/Reject.
- **TODO(me):** the threshold you chose on the real warehouse and why.

## Evaluation method
Golden set of 40–60 verified question → SQL → result triples. Execution match compares values (not
column names), ignores row and column order unless `order_matters`, and rounds numerics to 2 decimals.
Reported overall, by difficulty, plus valid-SQL rate, retries, p50/p95 latency, tokens and cost, for
two Gemini tiers. Nightly CI fails if accuracy drops > 3 points below `evals/baseline.json`.

## Risks
**TODO(me):** your top risks after testing (see the plan's risk table as a starting point).

## Next steps
**TODO(me):** e.g. retrieval over schema docs for 500+ tables, semantic layer, few-shot from similar golden questions.

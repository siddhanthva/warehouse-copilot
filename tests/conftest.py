"""Shared fixtures. Unit tests never touch the network, the database or a real model."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

TEST_TABLES = (
    "marts.fct_inpatient_drg_payments,marts.dim_hospitals,marts.dim_drg,marts.fct_hospital_financials"
)


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """Pin settings to fixture values so a developer's .env cannot leak into tests."""
    from warehouse_copilot import config

    monkeypatch.setattr(config, "_load_dotenv", lambda: None)
    keep_state = os.environ.get("RUN_INTEGRATION")
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") + (() if keep_state else ("APP_STATE_DATABASE_URL",)):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ALLOWED_TABLES", TEST_TABLES)
    monkeypatch.setenv("DEFAULT_SCHEMA", "marts")
    monkeypatch.setenv("DBT_TARGET_DIR", str(FIXTURES / "dbt_target"))
    if not os.environ.get("RUN_INTEGRATION"):
        monkeypatch.delenv("DATABASE_URL", raising=False)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def pytest_collection_modifyitems(config, items):
    """Integration tests need a real DATABASE_URL; skip them unless RUN_INTEGRATION=1."""
    if os.environ.get("RUN_INTEGRATION") and (os.environ.get("DATABASE_URL") or os.environ.get("REAL_WAREHOUSE")):
        return
    skip = pytest.mark.skip(reason="set RUN_INTEGRATION=1 and DATABASE_URL to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)

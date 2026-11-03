"""db.connect must not send startup options through Neon's pooled endpoint (it rejects them)."""

from __future__ import annotations

import pytest

from warehouse_copilot import db


class _FakeConn:
    read_only = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize(
    "host,expect_options",
    [("ep-x-pooler.c-9.us-east-2.aws.neon.tech", False), ("ep-x.c-9.us-east-2.aws.neon.tech", True)],
)
def test_startup_options_only_on_direct_hosts(monkeypatch, host, expect_options):
    seen = {}

    def fake_connect(url, **kwargs):
        seen.update(kwargs)
        return _FakeConn()

    monkeypatch.setattr(db.psycopg, "connect", fake_connect)
    with db.connect(f"postgresql://u:p@{host}/d?sslmode=require"):
        pass
    assert (seen["options"] is not None) is expect_options
    if expect_options:
        assert "statement_timeout" in seen["options"] and "default_transaction_read_only=on" in seen["options"]

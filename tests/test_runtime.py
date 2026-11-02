"""The sync runtime used by Streamlit and evals, with mocked model and warehouse."""

from langgraph.checkpoint.memory import InMemorySaver

from tests.fakes import FakeLLM, FakeWarehouse, draft, summary, tables
from warehouse_copilot.agent.state import SQLDraft, Summary, TableSelection
from warehouse_copilot.runtime import AgentRuntime


def test_runtime_pause_and_resume_from_sync_code():
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [draft()], Summary: [summary()]})
    wh = FakeWarehouse(estimated_rows=10_000_000)
    rt = AgentRuntime(model="fake", llm=llm, tools=wh, checkpointer=InMemorySaver())
    try:
        turn = rt.ask("big question")
        assert turn.paused and turn.interrupt["type"] == "approve"
        done = rt.resume(turn.thread_id, {"approved": True})
        assert done.status == "ok" and done.state["result"]["rows"]
    finally:
        rt.close()


import os  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.integration
def test_runtime_end_to_end_through_real_mcp_server_and_database():
    """Fake model, but the real MCP subprocess, guard, read-only DB and (if configured)
    the Postgres checkpointer."""
    sql = "SELECT h.hospital_name, COUNT(*) AS n FROM marts.dim_hospitals h GROUP BY 1 ORDER BY 2 DESC"
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [draft("SELECT * FROM pg_user"), draft(sql)],
                   Summary: [summary()]})
    if os.environ.get("APP_STATE_DATABASE_URL"):
        from warehouse_copilot import config
        config.get_settings.cache_clear()
    rt = AgentRuntime(model="fake", llm=llm)
    try:
        turn = rt.ask("How many rows per hospital?")
        assert turn.status == "ok", turn.state.get("error")
        assert turn.state["retries"] == 1
        assert turn.state["result"]["columns"] == ["hospital_name", "n"]
    finally:
        rt.close()

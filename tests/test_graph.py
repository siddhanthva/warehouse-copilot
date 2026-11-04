"""Graph tests with a mocked model and warehouse: happy path, every retry path,
retry exhaustion, and the clarify / approve / reject interrupts."""

from __future__ import annotations

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from tests.fakes import FakeLLM, FakeWarehouse, draft, summary, tables
from warehouse_copilot import schema_docs
from warehouse_copilot.agent.graph import build_graph
from warehouse_copilot.agent.state import SQLDraft, Summary, TableSelection


@pytest.fixture(autouse=True)
def _docs_cache():
    schema_docs.get_schema_docs.cache_clear()
    yield


def make(llm, wh, **kw):
    graph = build_graph(llm, wh, checkpointer=InMemorySaver(), **kw)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    return graph, config


async def test_happy_path():
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [draft()], Summary: [summary()]})
    wh = FakeWarehouse()
    graph, cfg = make(llm, wh)

    out = await graph.ainvoke({"question": "Top 10 hospitals by average Medicare payment?"}, cfg)

    assert out["status"] == "ok"
    assert out["answer"].startswith("General Hospital")
    assert out["retries"] == 0
    assert out["chart_spec"] == {"type": "bar", "x": "hospital_name", "y": "avg_payment"}
    assert len(wh.executed) == 1 and wh.executed[0].endswith("LIMIT 10")
    assert out["usage"]["llm_calls"] == 3
    assert out["usage"]["input_tokens"] == 300
    # The SQL prompt carries schema docs and metric definitions.
    sql_prompt = llm.prompts_for(SQLDraft)[0]
    assert "marts.dim_hospitals" in sql_prompt and "avg_medicare_payment_per_discharge" in sql_prompt


async def test_unanswerable_question_declines_without_running_a_query():
    llm = FakeLLM(
        {
            TableSelection: [tables()],
            # declined twice: once on the picked tables, once after re-checking with every table
            SQLDraft: [draft("", answerable=False, unanswerable_reason="The warehouse covers one period only.")] * 2,
        }
    )
    wh = FakeWarehouse()
    graph, cfg = make(llm, wh)

    out = await graph.ainvoke({"question": "How did payments change from last year?"}, cfg)

    assert out["status"] == "unanswerable"
    assert "one period only" in out["answer"]
    assert wh.executed == [] and wh.explained == []
    assert out["retries"] == 0
    assert out["usage"]["llm_calls"] == 3  # select + two SQL drafts, no summarize call


async def test_unanswerable_verdict_is_rechecked_with_every_table():
    """A wrong table pick must not turn into a false 'can't answer'."""
    llm = FakeLLM(
        {
            TableSelection: [tables("marts.dim_hospitals")],
            SQLDraft: [draft("", answerable=False, unanswerable_reason="No payment columns."), draft()],
            Summary: [summary()],
        }
    )
    wh = FakeWarehouse()
    graph, cfg = make(llm, wh)

    out = await graph.ainvoke({"question": "Which hospital is paid most?"}, cfg)

    assert out["status"] == "ok" and len(wh.executed) == 1
    assert out["retries"] == 0 and out["widened"] is True
    first, second = llm.prompts_for(SQLDraft)
    assert "fct_inpatient_drg_payments" not in first and "fct_inpatient_drg_payments" in second


async def test_unanswerable_after_widening_declines():
    llm = FakeLLM(
        {
            TableSelection: [tables("marts.dim_hospitals")],
            SQLDraft: [draft("", answerable=False, unanswerable_reason="No age data.")] * 2,
        }
    )
    wh = FakeWarehouse()
    graph, cfg = make(llm, wh)

    out = await graph.ainvoke({"question": "Patient mix by age?"}, cfg)

    assert out["status"] == "unanswerable" and "No age data." in out["answer"]
    assert wh.executed == [] and out["usage"]["llm_calls"] == 3  # select + two SQL drafts


async def test_validation_retry_feeds_guard_error_back():
    llm = FakeLLM(
        {
            TableSelection: [tables()],
            SQLDraft: [draft("SELECT * FROM pg_catalog.pg_user"), draft()],
            Summary: [summary()],
        }
    )
    wh = FakeWarehouse()
    graph, cfg = make(llm, wh)

    out = await graph.ainvoke({"question": "q"}, cfg)

    assert out["status"] == "ok"
    assert out["retries"] == 1
    retry_prompt = llm.prompts_for(SQLDraft)[1]
    assert "allow-list" in retry_prompt and "pg_catalog.pg_user" in retry_prompt
    assert out["attempts"][0]["stage"] == "guard"
    assert wh.executed and "pg_user" not in wh.executed[0]


async def test_db_error_retry():
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [draft(), draft()], Summary: [summary()]})
    wh = FakeWarehouse(db_errors=['column "hospitl_name" does not exist'])
    graph, cfg = make(llm, wh)

    out = await graph.ainvoke({"question": "q"}, cfg)

    assert out["status"] == "ok"
    assert out["retries"] == 1
    assert "hospitl_name" in llm.prompts_for(SQLDraft)[1]


async def test_retry_exhaustion_fails_gracefully():
    bad = draft("DELETE FROM marts.dim_hospitals")
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [bad, bad, bad]})
    wh = FakeWarehouse()
    graph, cfg = make(llm, wh)

    out = await graph.ainvoke({"question": "q"}, cfg)

    assert out["status"] == "failed"
    assert out["retries"] == 3  # 1 try + 2 retries, then stop
    assert len(llm.prompts_for(SQLDraft)) == 3
    assert wh.executed == []
    assert "couldn't produce" in out["answer"]


async def test_mixed_guard_and_db_errors_share_one_retry_budget():
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [draft("DROP TABLE x"), draft(), draft()]})
    wh = FakeWarehouse(db_errors=["syntax error", "syntax error"])
    graph, cfg = make(llm, wh)

    out = await graph.ainvoke({"question": "q"}, cfg)

    assert out["status"] == "failed"
    assert len(llm.prompts_for(SQLDraft)) == 3


async def test_long_question_refused_before_model_call():
    llm = FakeLLM()
    graph, cfg = make(llm, FakeWarehouse())
    out = await graph.ainvoke({"question": "x" * 50_000}, cfg)
    assert out["status"] == "refused"
    assert llm.calls == []


async def test_invalid_table_selection_falls_back_to_all_tables():
    llm = FakeLLM({TableSelection: [tables("pg_shadow")], SQLDraft: [draft()], Summary: [summary()]})
    graph, cfg = make(llm, FakeWarehouse())
    out = await graph.ainvoke({"question": "q"}, cfg)
    assert "pg_shadow" not in out["candidate_tables"]
    assert len(out["candidate_tables"]) == 4


async def test_chart_on_missing_column_is_dropped():
    from warehouse_copilot.agent.state import ChartSpec

    bad_chart = Summary(answer="x", chart=ChartSpec(type="bar", x="nope", y="avg_payment"))
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [draft()], Summary: [bad_chart]})
    graph, cfg = make(llm, FakeWarehouse())
    out = await graph.ainvoke({"question": "q"}, cfg)
    assert out["chart_spec"]["type"] == "none"


# ---------- approval gate ----------


async def test_clarify_interrupt_and_resume():
    ambiguous = SQLDraft(sql="", ambiguous=True, clarifying_question="Top by which metric?")
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [ambiguous, draft()], Summary: [summary()]})
    wh = FakeWarehouse()
    graph, cfg = make(llm, wh)

    paused = await graph.ainvoke({"question": "Show me the top hospitals"}, cfg)
    intr = paused["__interrupt__"][0].value
    assert intr == {"type": "clarify", "question": "Top by which metric?", "assumptions": []}
    assert wh.executed == [] and wh.explained == []

    out = await graph.ainvoke(Command(resume={"clarification": "average Medicare payment"}), cfg)

    assert out["status"] == "ok"
    assert out["clarifications"] == ["average Medicare payment"]
    assert "Clarification 1 from the user: average Medicare payment" in str(llm.calls[-2][1][1].content)


async def test_clarify_cap_stops_asking():
    amb = SQLDraft(sql=draft().sql, ambiguous=True, clarifying_question="Which?")
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [amb, amb, amb], Summary: [summary()]})
    graph, cfg = make(llm, FakeWarehouse())
    await graph.ainvoke({"question": "top hospitals"}, cfg)
    await graph.ainvoke(Command(resume="by payment"), cfg)
    out = await graph.ainvoke(Command(resume="in 2023"), cfg)
    # After 2 clarifications the agent stops asking and runs its best SQL.
    assert out["status"] == "ok"


async def test_expensive_query_approved():
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [draft()], Summary: [summary()]})
    wh = FakeWarehouse(estimated_rows=5_000_000)
    graph, cfg = make(llm, wh)

    paused = await graph.ainvoke({"question": "q"}, cfg)
    intr = paused["__interrupt__"][0].value
    assert intr["type"] == "approve"
    assert intr["estimated_rows"] == 5_000_000
    assert intr["sql"].endswith("LIMIT 10")
    assert wh.executed == []

    out = await graph.ainvoke(Command(resume={"approved": True}), cfg)
    assert out["status"] == "ok"
    assert len(wh.executed) == 1


async def test_expensive_query_rejected():
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [draft()]})
    wh = FakeWarehouse(estimated_rows=5_000_000)
    graph, cfg = make(llm, wh)

    await graph.ainvoke({"question": "q"}, cfg)
    out = await graph.ainvoke(Command(resume={"approved": False}), cfg)

    assert out["status"] == "rejected"
    assert wh.executed == []
    assert "did not run" in out["answer"]


async def test_cheap_query_does_not_pause():
    llm = FakeLLM({TableSelection: [tables()], SQLDraft: [draft()], Summary: [summary()]})
    graph, cfg = make(llm, FakeWarehouse(estimated_rows=999_999))
    out = await graph.ainvoke({"question": "q"}, cfg)
    assert "__interrupt__" not in out and out["status"] == "ok"


def test_graph_shape():
    graph = build_graph(FakeLLM(), FakeWarehouse())
    nodes = set(graph.get_graph().nodes)
    assert {"select_tables", "generate_sql", "validate", "check_approval", "human_review",
            "execute", "summarize", "fail", "rejected"} <= nodes


def test_prompts_carry_the_data_notes_learned_from_eval_failures():
    """Guards the rules added after the first full eval (keys vs names, per-discharge totals, DRG codes)."""
    from warehouse_copilot.agent import prompts

    sql_rules = prompts.GENERATE_SQL_SYSTEM
    for marker in ("Never return only a surrogate key", "drg_code = 291", "SUM(value *", "mart_service_line_margin", "within each"):
        assert marker in sql_rules, marker
    assert "mart_service_line_margin" in prompts.SELECT_TABLES_SYSTEM

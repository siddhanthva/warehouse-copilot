"""The LangGraph agent.

    START -> select_tables -> generate_sql -> validate --ok--> check_approval --ok--> execute -> summarize -> END
                                   ^             | retry           | review / retry      | retry
                                   |             v                 v                     |
                                   +-------- (retries <= 2) <-- human_review <-----------+
                                                 | exhausted       (interrupt: clarify / approve)
                                                 v
                                               fail -> END      validate --not answerable--> decline -> END

Deterministic steps (guard, retry cap, approval policy) are plain code. Only table
selection, SQL writing and summarization call the model. `retries` counts every
regeneration (guard failures and database errors together) so the graph cannot loop.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from warehouse_copilot import schema_docs, sql_guard
from warehouse_copilot.agent import prompts
from warehouse_copilot.agent.state import AgentState, SQLDraft, Summary, TableSelection
from warehouse_copilot.agent.tools import WarehouseTools
from warehouse_copilot.config import Settings, get_settings


async def call_structured(
    llm: BaseChatModel,
    schema: type[BaseModel],
    messages: list,
    config: RunnableConfig | None = None,
) -> tuple[BaseModel | None, dict]:
    """Call the model for a Pydantic object and return (parsed, token usage)."""
    runnable = llm.with_structured_output(schema, include_raw=True)
    out = await runnable.ainvoke(messages, config=config)
    usage = {"llm_calls": 1}
    if isinstance(out, dict) and "parsed" in out:
        meta = getattr(out.get("raw"), "usage_metadata", None) or {}
        usage["input_tokens"] = meta.get("input_tokens", 0)
        usage["output_tokens"] = meta.get("output_tokens", 0)
        return out.get("parsed"), usage
    return out, usage  # a model wrapper that ignored include_raw


def full_question(state: AgentState) -> str:
    q = state["question"]
    for i, c in enumerate(state.get("clarifications") or [], start=1):
        q += f"\nClarification {i} from the user: {c}"
    return q


def build_graph(
    llm: BaseChatModel,
    tools: WarehouseTools,
    *,
    checkpointer: Any = None,
    settings: Settings | None = None,
):
    s = settings or get_settings()

    # ---------- nodes ----------

    async def select_tables(state: AgentState, config: RunnableConfig) -> dict:
        question = state.get("question") or ""
        if not question.strip():
            return {"status": "refused", "error": "Please ask a question."}
        if len(question) > s.max_question_chars:
            # Refused before any model call: protects cost and context from stuffing.
            return {
                "status": "refused",
                "error": f"Questions are limited to {s.max_question_chars} characters.",
            }
        docs = schema_docs.get_schema_docs()
        system = prompts.SELECT_TABLES_SYSTEM.format(
            catalog=schema_docs.table_catalog_prompt(docs), max_tables=s.max_candidate_tables
        )
        parsed, usage = await call_structured(
            llm, TableSelection, [SystemMessage(system), HumanMessage(full_question(state))], config
        )
        allowed = set(docs)
        picked = [t.lower() for t in (parsed.tables if parsed else []) if t.lower() in allowed]
        if not picked:  # model picked nothing valid: fall back to every allowed table
            picked = list(docs)
        return {
            "candidate_tables": picked[: s.max_candidate_tables],
            "retries": state.get("retries", 0),
            "usage": usage,
        }

    async def generate_sql(state: AgentState, config: RunnableConfig) -> dict:
        previous = ""
        error = state.get("validation_error") or state.get("db_error")
        if error and state.get("draft") is not None:
            previous = prompts.PREVIOUS_ATTEMPT.format(sql=state["draft"]["sql"], error=error)
        system = prompts.GENERATE_SQL_SYSTEM.format(
            schema=schema_docs.schema_prompt(state.get("candidate_tables") or []),
            metrics=schema_docs.metrics_prompt() or "(none)",
            previous=previous,
        )
        parsed, usage = await call_structured(
            llm, SQLDraft, [SystemMessage(system), HumanMessage(full_question(state))], config
        )
        if parsed is None:
            parsed = SQLDraft(sql="", assumptions=[], ambiguous=False)
        return {
            # Stored as a plain dict so checkpoints serialize without custom types.
            "draft": parsed.model_dump(),
            "validation_error": None,
            "db_error": None,
            "validated_sql": None,
            "usage": usage,
        }

    def validate(state: AgentState) -> dict:
        draft = SQLDraft(**state["draft"])
        if not draft.answerable:
            all_tables = list(schema_docs.get_schema_docs())
            if not state.get("widened") and set(state.get("candidate_tables") or []) != set(all_tables):
                # The model only saw the tables select_tables picked, so "no data" may just mean
                # the wrong tables. Give it the whole catalog once before declining. Not counted
                # as a retry: routing sees no validated SQL and sends it back to generate_sql.
                return {"candidate_tables": all_tables, "widened": True}
            return {"status": "unanswerable", "error": draft.unanswerable_reason}
        if draft.ambiguous and _can_clarify(state):
            return {"approval_type": "clarify", "needs_approval": True}
        if not draft.sql.strip():
            err = "the model returned no SQL"
        else:
            checked = sql_guard.validate(draft.sql)
            if checked.ok:
                return {"validated_sql": checked.sql, "validation_error": None, "approval_type": None}
            err = checked.feedback()
        retries = state.get("retries", 0) + 1
        return {
            "validation_error": err,
            "retries": retries,
            "attempts": [{"sql": draft.sql, "stage": "guard", "error": err}],
        }

    async def check_approval(state: AgentState) -> dict:
        """Deterministic policy: expensive plans need a human. No model involved."""
        est = await tools.estimate(state["validated_sql"])
        if "error" in est:
            retries = state.get("retries", 0) + 1
            return {
                "db_error": est["error"],
                "retries": retries,
                "attempts": [{"sql": state["validated_sql"], "stage": "explain", "error": est["error"]}],
            }
        scanned = max(int(est.get("max_scan_rows", 0)), int(est.get("estimated_rows", 0)))
        if scanned > s.approval_row_threshold:
            return {
                "estimate": est,
                "needs_approval": True,
                "approval_type": "approve",
                "approval_reason": (
                    f"The planner estimates about {scanned:,} rows scanned, above the "
                    f"{s.approval_row_threshold:,} row approval threshold."
                ),
            }
        return {"estimate": est, "needs_approval": False, "approval_type": None}

    def human_review(state: AgentState) -> Command:
        """Pause for a person. Everything before interrupt() re-runs on resume, so keep
        this node free of side effects."""
        if state.get("approval_type") == "clarify":
            draft = SQLDraft(**state["draft"])
            reply = interrupt(
                {
                    "type": "clarify",
                    "question": draft.clarifying_question
                    or "Could you clarify what you mean? Which metric or filter should I use?",
                    "assumptions": draft.assumptions,
                }
            )
            text = (reply.get("clarification") if isinstance(reply, dict) else reply) or ""
            if not str(text).strip():
                return Command(goto="rejected", update={"approved": False})
            return Command(
                goto="generate_sql",
                update={
                    "clarifications": [*(state.get("clarifications") or []), str(text).strip()],
                    "needs_approval": False,
                    "approval_type": None,
                },
            )
        decision = interrupt(
            {
                "type": "approve",
                "sql": state["validated_sql"],
                "estimated_rows": (state.get("estimate") or {}).get("max_scan_rows"),
                "reason": state.get("approval_reason"),
            }
        )
        approved = bool(decision.get("approved") if isinstance(decision, dict) else decision)
        if approved:
            return Command(goto="execute", update={"approved": True, "needs_approval": False})
        return Command(goto="rejected", update={"approved": False})

    async def execute(state: AgentState) -> dict:
        res = await tools.run_query(state["validated_sql"])
        if "error" in res:
            retries = state.get("retries", 0) + 1
            return {
                "db_error": res["error"],
                "retries": retries,
                "attempts": [{"sql": state["validated_sql"], "stage": res.get("stage", "database"), "error": res["error"]}],
            }
        return {"result": res, "db_error": None}

    async def summarize(state: AgentState, config: RunnableConfig) -> dict:
        res = state["result"] or {}
        rows = res.get("rows", [])
        user = prompts.SUMMARIZE_USER.format(
            question=full_question(state),
            sql=state["validated_sql"],
            assumptions="; ".join(state["draft"].get("assumptions") or []) or "none",
            row_count=len(rows),
            truncated=", truncated at the row limit" if res.get("truncated") else "",
            columns=res.get("columns", []),
            rows=json.dumps(rows[:50], default=str),
        )
        parsed, usage = await call_structured(
            llm, Summary, [SystemMessage(prompts.SUMMARIZE_SYSTEM), HumanMessage(user)], config
        )
        if parsed is None:
            parsed = Summary(answer=f"The query returned {len(rows)} rows.")
        chart = parsed.chart.model_dump()
        cols = set(res.get("columns", []))
        if chart["type"] != "none" and not {chart["x"], chart["y"]} <= cols:
            chart = {"type": "none", "x": None, "y": None}  # never chart columns that don't exist
        return {"answer": parsed.answer, "chart_spec": chart, "status": "ok", "usage": usage}

    def fail(state: AgentState) -> dict:
        if state.get("status") == "refused":
            return {"answer": state.get("error"), "status": "refused"}
        last = state.get("validation_error") or state.get("db_error") or "unknown error"
        return {
            "status": "failed",
            "error": last,
            "answer": (
                f"I couldn't produce a working query after {s.max_retries + 1} attempts. "
                f"Last problem: {last}. Try rephrasing or naming the metric and year."
            ),
        }

    def decline(state: AgentState) -> dict:
        """The warehouse lacks the data. Say so plainly instead of running a made-up query."""
        reason = state.get("error") or "the warehouse does not contain the data needed"
        return {
            "status": "unanswerable",
            "answer": f"I can't answer that from this warehouse: {reason} "
            "Try a question about hospitals, DRGs, finances or metro areas in the data.",
        }

    def rejected(state: AgentState) -> dict:
        return {
            "status": "rejected",
            "answer": "Okay, I did not run that query. Ask again with a narrower question "
            "(for example a specific year, state or DRG) and I'll try a cheaper query.",
        }

    # ---------- routing ----------

    def _can_clarify(state: AgentState) -> bool:
        return len(state.get("clarifications") or []) < s.max_clarifications

    def route_after_select(state: AgentState) -> Literal["generate_sql", "fail"]:
        return "fail" if state.get("status") == "refused" else "generate_sql"

    def route_after_validate(state: AgentState) -> Literal["clarify", "ok", "retry", "fail", "decline"]:
        if state.get("status") == "unanswerable":
            return "decline"
        if state.get("approval_type") == "clarify":
            return "clarify"
        if state.get("validated_sql"):
            return "ok"
        return "retry" if state.get("retries", 0) <= s.max_retries else "fail"

    def route_after_check(state: AgentState) -> Literal["review", "execute", "retry", "fail"]:
        if state.get("db_error"):
            return "retry" if state.get("retries", 0) <= s.max_retries else "fail"
        return "review" if state.get("needs_approval") else "execute"

    def route_after_execute(state: AgentState) -> Literal["ok", "retry", "fail"]:
        if state.get("db_error"):
            return "retry" if state.get("retries", 0) <= s.max_retries else "fail"
        return "ok"

    # ---------- wiring ----------

    g = StateGraph(AgentState)
    g.add_node("select_tables", select_tables)
    g.add_node("generate_sql", generate_sql)
    g.add_node("validate", validate)
    g.add_node("check_approval", check_approval)
    g.add_node("human_review", human_review, destinations=("generate_sql", "execute", "rejected"))
    g.add_node("execute", execute)
    g.add_node("summarize", summarize)
    g.add_node("fail", fail)
    g.add_node("rejected", rejected)
    g.add_node("decline", decline)

    g.add_edge(START, "select_tables")
    g.add_conditional_edges("select_tables", route_after_select, {"generate_sql": "generate_sql", "fail": "fail"})
    g.add_edge("generate_sql", "validate")
    g.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "clarify": "human_review",
            "ok": "check_approval",
            "retry": "generate_sql",
            "fail": "fail",
            "decline": "decline",
        },
    )
    g.add_conditional_edges(
        "check_approval",
        route_after_check,
        {"review": "human_review", "execute": "execute", "retry": "generate_sql", "fail": "fail"},
    )
    g.add_conditional_edges(
        "execute", route_after_execute, {"ok": "summarize", "retry": "generate_sql", "fail": "fail"}
    )
    g.add_edge("summarize", END)
    g.add_edge("fail", END)
    g.add_edge("rejected", END)
    g.add_edge("decline", END)

    return g.compile(checkpointer=checkpointer)

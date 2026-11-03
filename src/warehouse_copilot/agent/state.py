"""Agent state and structured-output schemas."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field


class TableSelection(BaseModel):
    """Which tables are needed to answer the question."""

    tables: list[str] = Field(description="1-5 fully qualified table names from the catalog")
    reason: str = Field(default="", description="One sentence on why these tables")


class SQLDraft(BaseModel):
    """A single PostgreSQL SELECT plus what the model assumed while writing it."""

    sql: str = Field(
        default="",
        description="One PostgreSQL SELECT statement (CTEs allowed). No semicolons. Empty if not answerable.",
    )
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list, description="Every interpretation you made, in plain English"
    )
    ambiguous: bool = Field(
        default=False,
        description="True only if the question cannot be answered without asking the user "
        "(e.g. 'top hospitals' with no metric).",
    )
    clarifying_question: str | None = Field(
        default=None, description="The one question to ask the user when ambiguous is true"
    )
    answerable: bool = Field(
        default=True,
        description="False only if the warehouse does not contain the data needed at all "
        "(e.g. another time period, individual doctors or patients).",
    )
    unanswerable_reason: str | None = Field(
        default=None, description="One or two sentences on what data is missing, when answerable is false"
    )


class ChartSpec(BaseModel):
    type: Literal["bar", "line", "none"] = "none"
    x: str | None = None
    y: str | None = None


class Summary(BaseModel):
    """Final answer for the user."""

    answer: str = Field(description="2-4 sentence plain-English answer that cites the numbers")
    chart: ChartSpec = Field(default_factory=ChartSpec)


def add_usage(left: dict | None, right: dict | None) -> dict:
    """Reducer: sum token counts across every model call in a run."""
    out = dict(left or {})
    for k, v in (right or {}).items():
        out[k] = out.get(k, 0) + (v or 0)
    return out


class AgentState(TypedDict, total=False):
    question: str
    clarifications: list[str]
    candidate_tables: list[str]
    widened: bool  # True once an 'unanswerable' verdict was re-checked with every table
    draft: dict  # SQLDraft.model_dump()
    validated_sql: str | None
    validation_error: str | None
    db_error: str | None
    retries: int
    estimate: dict | None
    needs_approval: bool
    approval_type: Literal["clarify", "approve"] | None
    approval_reason: str | None
    approved: bool | None
    result: dict | None  # {"columns": [...], "rows": [[...]], ...}
    answer: str | None
    chart_spec: dict | None
    status: Literal["ok", "failed", "rejected", "refused", "unanswerable"] | None
    error: str | None
    attempts: Annotated[list[dict], operator.add]  # every SQL tried + why it failed
    usage: Annotated[dict, add_usage]  # {"input_tokens": n, "output_tokens": n, "llm_calls": n}

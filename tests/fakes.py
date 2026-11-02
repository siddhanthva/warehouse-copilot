"""Test doubles: a scripted chat model and an in-memory warehouse. No network."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from warehouse_copilot import sql_guard
from warehouse_copilot.agent.state import ChartSpec, SQLDraft, Summary, TableSelection


class FakeLLM:
    """Returns scripted objects per output schema, in order, and records every prompt.

    Implements just enough of BaseChatModel for the graph: with_structured_output().
    """

    def __init__(self, script: dict[type, list[Any]] | None = None):
        self.queues: dict[str, deque] = defaultdict(deque)
        self.calls: list[tuple[str, list]] = []
        for schema, items in (script or {}).items():
            self.queues[schema.__name__].extend(items)

    def add(self, schema: type, *items: Any) -> FakeLLM:
        self.queues[schema.__name__].extend(items)
        return self

    def with_structured_output(self, schema, include_raw: bool = False, **_: Any):
        name = schema.__name__

        def respond(messages):
            self.calls.append((name, messages))
            if not self.queues[name]:
                raise AssertionError(f"FakeLLM ran out of scripted {name} responses")
            parsed = self.queues[name].popleft()
            raw = AIMessage(content="", usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})
            return {"raw": raw, "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return RunnableLambda(respond)

    def prompts_for(self, schema: type) -> list[str]:
        return [m[0].content for n, m in self.calls if n == schema.__name__]


class FakeWarehouse:
    """Stands in for the MCP tools. Applies the real guard, like the server does.

    `db_errors` is a list of error messages returned by successive run_query calls
    before a success; `estimated_rows` controls the approval gate.
    """

    def __init__(self, rows=None, columns=None, db_errors=None, estimated_rows=100):
        self.columns = columns or ["hospital_name", "avg_payment"]
        self.rows = rows if rows is not None else [["General Hospital", 15000.5], ["City Medical", 14000.0]]
        self.db_errors = list(db_errors or [])
        self.estimated_rows = estimated_rows
        self.executed: list[str] = []  # SQL that actually reached the "database"
        self.explained: list[str] = []

    async def estimate(self, sql: str) -> dict:
        checked = sql_guard.validate(sql)
        if not checked.ok:
            return {"error": checked.feedback(), "stage": "guard"}
        self.explained.append(checked.sql)
        return {"estimated_rows": 10, "total_cost": 1.0, "max_scan_rows": self.estimated_rows}

    async def run_query(self, sql: str) -> dict:
        checked = sql_guard.validate(sql)
        if not checked.ok:
            return {"error": checked.feedback(), "stage": "guard"}
        if self.db_errors:
            return {"error": self.db_errors.pop(0), "stage": "database"}
        self.executed.append(checked.sql)
        return {"columns": self.columns, "rows": self.rows, "row_count": len(self.rows), "truncated": False, "sql": checked.sql}


GOOD_SQL = (
    "SELECT h.hospital_name, ROUND(AVG(p.avg_medicare_payment), 2) AS avg_payment "
    "FROM marts.fct_inpatient_drg_payments p JOIN marts.dim_hospitals h USING (provider_ccn) "
    "GROUP BY h.hospital_name ORDER BY avg_payment DESC LIMIT 10"
)


def tables(*names: str) -> TableSelection:
    return TableSelection(tables=list(names) or ["marts.fct_inpatient_drg_payments", "marts.dim_hospitals"])


def draft(sql: str = GOOD_SQL, **kw: Any) -> SQLDraft:
    return SQLDraft(sql=sql, tables_used=[], assumptions=kw.pop("assumptions", ["Used all years"]), **kw)


def summary(text: str = "General Hospital has the highest average payment.") -> Summary:
    return Summary(answer=text, chart=ChartSpec(type="bar", x="hospital_name", y="avg_payment"))

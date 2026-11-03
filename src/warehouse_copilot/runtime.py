"""Synchronous facade over the async agent, shared by Streamlit and the eval runner.

Why a background event loop: the MCP session, the async Postgres checkpointer and the
graph are all bound to one asyncio loop. Streamlit reruns the script on every click in
a fresh thread, so we keep one loop alive in a daemon thread and submit work to it.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.types import Command

from warehouse_copilot import tracing
from warehouse_copilot.agent.graph import build_graph
from warehouse_copilot.agent.tools import MCPWarehouseTools, WarehouseTools
from warehouse_copilot.config import get_settings


@dataclass
class Turn:
    """What one ask/resume call produced."""

    thread_id: str
    status: str  # "ok" | "failed" | "rejected" | "refused" | "interrupted"
    interrupt: dict | None = None
    state: dict = field(default_factory=dict)
    latency_s: float = 0.0

    @property
    def paused(self) -> bool:
        return self.status == "interrupted"


class AgentRuntime:
    def __init__(
        self,
        *,
        model: str | None = None,
        llm: BaseChatModel | None = None,
        tools: WarehouseTools | None = None,
        checkpointer: Any = None,
    ):
        self.model = model or get_settings().gemini_model
        self._llm = llm
        self._tools = tools
        self._checkpointer = checkpointer
        self._stack = AsyncExitStack()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self.graph = self._submit(self._setup())

    # ---- lifecycle ----

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _setup(self):
        if self._llm is None:
            from warehouse_copilot.agent.llm import make_llm

            self._llm = make_llm(self.model)
        if self._tools is None:
            self._tools = await MCPWarehouseTools().start()
            self._stack.push_async_callback(self._tools.aclose)
        if self._checkpointer is None:
            self._checkpointer = await self._make_checkpointer()
        return build_graph(self._llm, self._tools, checkpointer=self._checkpointer)

    async def _make_checkpointer(self):
        url = get_settings().app_state_database_url
        # psycopg's async driver cannot run on Windows' default ProactorEventLoop, and the MCP stdio
        # subprocess needs that loop, so on Windows we keep checkpoints in memory. Deployed
        # (Linux) runs use Postgres so interrupts survive between requests.
        if not url or sys.platform == "win32":
            # Fine for local dev and evals; interrupts won't survive a container restart.
            from langgraph.checkpoint.memory import InMemorySaver

            return InMemorySaver()
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        saver = await self._stack.enter_async_context(AsyncPostgresSaver.from_conn_string(url))
        await saver.setup()  # idempotent: creates checkpoint tables on first run
        return saver

    def close(self) -> None:
        self._submit(self._stack.aclose())
        self._loop.call_soon_threadsafe(self._loop.stop)

    # ---- API ----

    def ask(self, question: str, *, thread_id: str | None = None, **trace: Any) -> Turn:
        """Start a new question. Use a fresh thread_id per question."""
        thread_id = thread_id or str(uuid.uuid4())
        return self._run({"question": question}, thread_id, **trace)

    def resume(self, thread_id: str, value: Any, **trace: Any) -> Turn:
        """Continue a paused run: {"approved": bool} or {"clarification": str}."""
        return self._run(Command(resume=value), thread_id, **trace)

    def _run(self, payload: Any, thread_id: str, **trace: Any) -> Turn:
        config = tracing.run_config(thread_id, model=self.model, **trace)
        start = time.perf_counter()
        out = self._submit(self.graph.ainvoke(payload, config))
        latency = time.perf_counter() - start
        tracing.flush()
        interrupts = out.get("__interrupt__") or []
        if interrupts:
            return Turn(thread_id, "interrupted", interrupts[0].value, dict(out), latency)
        return Turn(thread_id, out.get("status") or "failed", None, dict(out), latency)

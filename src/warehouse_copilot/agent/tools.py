"""How the agent reaches the database: through the MCP server, never directly.

`WarehouseTools` is the small interface the graph depends on. Production uses
`MCPWarehouseTools` (launches mcp_server.py over stdio via langchain-mcp-adapters and
keeps one session open); tests pass a fake with the same two methods.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Protocol

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

SERVER_NAME = "warehouse"


class WarehouseTools(Protocol):
    async def run_query(self, sql: str) -> dict: ...
    async def estimate(self, sql: str) -> dict: ...


def server_connection() -> dict:
    """stdio connection config that launches our MCP server as a subprocess."""
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "warehouse_copilot.mcp_server"],
        # Pass the environment through so the server sees DATABASE_URL etc.
        "env": dict(os.environ),
    }


def parse_tool_output(raw: Any) -> dict:
    """Normalise what an MCP tool returns (str, content blocks, or dict) into a dict."""
    if isinstance(raw, tuple):  # (content, artifact)
        raw = raw[0]
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        texts = [b.get("text", "") if isinstance(b, dict) else getattr(b, "text", str(b)) for b in raw]
        if len(texts) > 1:  # a list-returning tool sends one block per item
            return {"result": [parse_tool_output(t).get("result", parse_tool_output(t)) for t in texts]}
        raw = texts[0] if texts else ""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"error": raw, "stage": "tool"}
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    return {"error": f"unexpected tool output type {type(raw).__name__}", "stage": "tool"}


class MCPWarehouseTools:
    """Holds one long-lived MCP session in a dedicated task.

    The stdio client uses anyio task groups, which must be entered and exited in the
    same task, so a background task owns the session and other tasks just call tools.
    """

    def __init__(self, connection: dict | None = None):
        self._client = MultiServerMCPClient({SERVER_NAME: connection or server_connection()})
        self._tools: dict[str, Any] = {}
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._error: BaseException | None = None

    async def start(self) -> MCPWarehouseTools:
        self._task = asyncio.create_task(self._hold_session())
        await self._ready.wait()
        if self._error:
            raise self._error
        return self

    async def _hold_session(self) -> None:
        try:
            async with self._client.session(SERVER_NAME) as session:
                tools = await load_mcp_tools(session)
                self._tools = {t.name: t for t in tools}
                self._ready.set()
                await self._stop.wait()
        except BaseException as err:  # surface startup failures to start()
            self._error = err
            self._ready.set()

    async def aclose(self) -> None:
        self._stop.set()
        if self._task:
            await self._task

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    async def call(self, name: str, **kwargs: Any) -> dict:
        tool = self._tools[name]
        return parse_tool_output(await tool.ainvoke(kwargs))

    async def run_query(self, sql: str) -> dict:
        return await self.call("run_readonly_query", sql=sql)

    async def estimate(self, sql: str) -> dict:
        return await self.call("estimate_query_cost", sql=sql)

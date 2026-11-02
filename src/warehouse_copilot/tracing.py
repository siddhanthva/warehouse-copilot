"""Langfuse tracing. Every graph run gets one trace with a span per node and per model
call (tokens, latency, cost). Disabled cleanly when keys are absent (tests, local dev)."""

from __future__ import annotations

import os
from typing import Any

from warehouse_copilot.config import get_settings


def callbacks() -> list[Any]:
    s = get_settings()
    if not s.langfuse_enabled:
        return []
    os.environ.setdefault("LANGFUSE_BASE_URL", s.langfuse_host)
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]


def run_config(
    thread_id: str,
    *,
    model: str | None = None,
    question_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """RunnableConfig with checkpointer thread id, Langfuse handler and trace metadata."""
    s = get_settings()
    all_tags = [f"model:{model or s.gemini_model}", *(tags or [])]
    if question_id:
        all_tags.append(f"question:{question_id}")
    return {
        "configurable": {"thread_id": thread_id},
        "callbacks": callbacks(),
        "metadata": {
            "langfuse_session_id": session_id or thread_id,
            "langfuse_tags": all_tags,
            "model": model or s.gemini_model,
            "question_id": question_id,
        },
        "run_name": "warehouse-copilot",
        "recursion_limit": 40,  # hard step cap on top of the retry cap
    }


def flush() -> None:
    if get_settings().langfuse_enabled:
        from langfuse import get_client

        get_client().flush()

"""Daily request cap for the public demo (protects credits).

Stored in the app-state database (APP_STATE_DATABASE_URL, the writable copilot_app role),
never in the warehouse. Falls back to a per-process counter for local dev.
"""

from __future__ import annotations

import datetime as dt
import threading

import psycopg

from warehouse_copilot.config import get_settings

_lock = threading.Lock()
_memory: dict[str, int] = {}


def _today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


def try_consume(cap: int | None = None) -> tuple[bool, int]:
    """Atomically count one request. Returns (allowed, count_after)."""
    s = get_settings()
    cap = cap if cap is not None else s.daily_request_cap
    if not s.app_state_database_url:
        with _lock:
            n = _memory.get(_today(), 0)
            if n >= cap:
                return False, n
            _memory[_today()] = n + 1
            return True, n + 1
    with psycopg.connect(s.app_state_database_url, connect_timeout=10) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_usage (day date PRIMARY KEY, n int NOT NULL)"
        )
        row = conn.execute(
            "INSERT INTO daily_usage (day, n) VALUES (%s, 1) "
            "ON CONFLICT (day) DO UPDATE SET n = daily_usage.n + 1 "
            "WHERE daily_usage.n < %s RETURNING n",
            (_today(), cap),
        ).fetchone()
    if row is None:
        return False, cap
    return True, row[0]

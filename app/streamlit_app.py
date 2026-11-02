"""Warehouse Copilot chat UI.

    streamlit run app/streamlit_app.py

Access code first, then chat. Paused runs (clarify / approve) are resumed from the
same LangGraph thread, so the Postgres checkpointer keeps them alive across reruns.
"""

from __future__ import annotations

import hmac
import uuid

import pandas as pd
import streamlit as st

from warehouse_copilot import usage
from warehouse_copilot.config import get_settings
from warehouse_copilot.runtime import AgentRuntime, Turn

EXAMPLES = [
    "Which 10 hospitals had the highest average Medicare payment per discharge in 2023?",
    "How did total Medicare payments for heart failure change year over year?",
    "For each DRG, which hospital had the highest average Medicare payment in 2023?",
    "Which states have the highest charge-to-payment ratio?",
]

st.set_page_config(page_title="Warehouse Copilot", page_icon="🏥", layout="wide")
settings = get_settings()


# ---------- access gate ----------

def access_granted() -> bool:
    if not settings.app_access_code:
        return True  # local dev; Cloud Run always sets APP_ACCESS_CODE
    if st.session_state.get("authed"):
        return True
    st.title("Warehouse Copilot")
    code = st.text_input("Access code", type="password")
    if code:
        if hmac.compare_digest(code, settings.app_access_code):
            st.session_state.authed = True
            st.rerun()
        st.error("Wrong access code.")
    return False


if not access_granted():
    st.stop()


@st.cache_resource(show_spinner="Starting the agent and MCP server...")
def get_runtime() -> AgentRuntime:
    return AgentRuntime()


# ---------- state ----------

ss = st.session_state
ss.setdefault("messages", [])  # list of {"role", "content", "turn": Turn | None}
ss.setdefault("pending", None)  # Turn waiting for clarify/approve
ss.setdefault("session_id", str(uuid.uuid4()))


# ---------- rendering ----------

def render_result(turn: Turn) -> None:
    s = turn.state
    if s.get("validated_sql"):
        with st.expander("SQL", expanded=False):
            st.code(s["validated_sql"], language="sql", wrap_lines=True)
    assumptions = (s.get("draft") or {}).get("assumptions") or []
    if assumptions:
        st.caption("Assumptions: " + "; ".join(assumptions))
    res = s.get("result")
    if not res:
        return
    df = pd.DataFrame(res["rows"], columns=res["columns"])
    chart = s.get("chart_spec") or {}
    if chart.get("type") in {"bar", "line"} and chart.get("x") in df and chart.get("y") in df:
        data = df[[chart["x"], chart["y"]]].copy()
        data[chart["y"]] = pd.to_numeric(data[chart["y"]], errors="coerce")
        if chart["type"] == "bar":
            st.bar_chart(data, x=chart["x"], y=chart["y"], sort=f"-{chart['y']}")
        else:
            st.line_chart(data, x=chart["x"], y=chart["y"])
    st.dataframe(df, hide_index=True, width="stretch")
    if res.get("truncated"):
        st.caption(f"Showing the first {len(df)} rows (row limit).")


def show_message(msg: dict) -> None:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("turn") is not None:
            render_result(msg["turn"])


def handle(turn: Turn) -> None:
    if turn.paused:
        ss.pending = turn
        return
    ss.pending = None
    ss.messages.append({"role": "assistant", "content": turn.state.get("answer") or "No answer.", "turn": turn})


def run_safely(fn, *args) -> None:
    try:
        with st.spinner("Thinking..."):
            handle(fn(*args, session_id=ss.session_id))
    except Exception as err:  # keep the UI alive; details go to logs and Langfuse
        ss.pending = None
        ss.messages.append({"role": "assistant", "content": f"Something went wrong: {type(err).__name__}. Please try again."})


def ask(question: str) -> None:
    allowed, _ = usage.try_consume()
    ss.messages.append({"role": "user", "content": question})
    if not allowed:
        ss.messages.append({"role": "assistant", "content": "The demo's daily question limit is reached. Please come back tomorrow."})
        return
    run_safely(get_runtime().ask, question)


# ---------- layout ----------

with st.sidebar:
    st.header("Warehouse Copilot")
    st.write("Ask questions about Medicare inpatient payments in plain English.")
    st.subheader("Try")
    for ex in EXAMPLES:
        if st.button(ex, width="stretch", disabled=ss.pending is not None):
            ask(ex)
            st.rerun()
    st.divider()
    st.markdown(f"[Source code on GitHub]({settings.github_repo_url})")
    st.caption("Read-only access. Every query is validated before it runs.")
    if st.button("New conversation"):
        ss.messages, ss.pending = [], None
        st.rerun()

st.title("🏥 Warehouse Copilot")

for m in ss.messages:
    show_message(m)

pending: Turn | None = ss.pending
if pending is not None:
    info = pending.interrupt or {}
    with st.chat_message("assistant"):
        if info.get("type") == "approve":
            st.warning(info.get("reason") or "This query looks expensive.")
            st.code(info.get("sql", ""), language="sql", wrap_lines=True)
            c1, c2 = st.columns(2)
            if c1.button("Approve and run", type="primary"):
                run_safely(get_runtime().resume, pending.thread_id, {"approved": True})
                st.rerun()
            if c2.button("Reject"):
                run_safely(get_runtime().resume, pending.thread_id, {"approved": False})
                st.rerun()
        else:
            st.info(info.get("question") or "Could you clarify?")
            if info.get("assumptions"):
                st.caption("So far I assumed: " + "; ".join(info["assumptions"]))
    reply = st.chat_input("Your clarification...") if info.get("type") == "clarify" else None
    if reply:
        ss.messages.append({"role": "user", "content": reply})
        run_safely(get_runtime().resume, pending.thread_id, {"clarification": reply})
        st.rerun()
else:
    question = st.chat_input("Ask a question about Medicare payments...")
    if question:
        ask(question)
        st.rerun()

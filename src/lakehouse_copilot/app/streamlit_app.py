"""Lakehouse Copilot chat UI.  Run with:  lhc ui   (or: streamlit run this_file.py)"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from lakehouse_copilot.agent.copilot import Copilot, build_copilot
from lakehouse_copilot.config import get_settings

EXAMPLES = [
    "What was our net revenue in 2024?",
    "Top 5 cities by revenue during the Diwali festive season 2025",
    "What is the AOV for UPI vs COD?",
    "Monthly orders in 2025 for Electronics",
    "Which category has the highest gross margin %?",
    "Cancellation rate by payment method",
]


@st.cache_resource
def get_copilot() -> Copilot:
    return build_copilot()


def render_answer(turn: dict) -> None:
    st.markdown(turn["content"])
    if turn.get("sources"):
        st.caption(turn["sources"])
    if turn.get("sql"):
        with st.expander("SQL and result"):
            st.code(turn["sql"], language="sql")
            result = turn.get("result") or {}
            if result.get("rows"):
                st.dataframe(pd.DataFrame(result["rows"], columns=result["columns"]))
    if turn.get("steps"):
        with st.expander("How I answered"):
            st.markdown(f"Search query: `{turn['rewritten']}`")
            for step in turn["steps"]:
                status = f" - error: {step['error']}" if step.get("error") else ""
                st.markdown(f"- `{step['tool']}` {step['summary']}{status}")


def main() -> None:
    st.set_page_config(page_title="Lakehouse Copilot", page_icon="🪔", layout="wide")
    settings = get_settings()
    copilot = get_copilot()

    with st.sidebar:
        st.header("Lakehouse Copilot")
        st.write("Ask business questions about the e-commerce lakehouse.")
        st.markdown(
            f"- **Mode:** `{copilot.mode}`\n"
            f"- **SQL engine:** `{settings.query_engine}`\n"
            f"- **Retriever:** `{settings.retriever}`"
        )
        st.subheader("Try these")
        for example in EXAMPLES:
            if st.button(example, use_container_width=True):
                st.session_state["pending"] = example
        if st.button("Clear chat"):
            st.session_state["messages"] = []

    st.title("Ask your lakehouse")
    messages: list[dict] = st.session_state.setdefault("messages", [])
    for turn in messages:
        with st.chat_message(turn["role"]):
            render_answer(turn) if turn["role"] == "assistant" else st.markdown(turn["content"])

    question = st.chat_input("e.g. Which city had the highest revenue in 2025?")
    question = question or st.session_state.pop("pending", None)
    if not question:
        return

    history = [{"role": m["role"], "content": m["content"]} for m in messages]
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"), st.spinner("Thinking..."):
        answer = copilot.answer(question, history)
        turn = {
            "role": "assistant",
            "content": answer.answer,
            "sources": answer.sources,
            "sql": answer.sql,
            "result": answer.result,
            "rewritten": answer.rewritten.text,
            "steps": [
                {"tool": s.tool, "summary": str(s.output or "")[:200], "error": s.error}
                for s in answer.steps
            ],
        }
        render_answer(turn)
    messages.append(turn)


main()

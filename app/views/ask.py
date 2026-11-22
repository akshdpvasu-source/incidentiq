"""Ask IncidentIQ page: chat-style text-to-SQL with the SQL guard visible."""
import streamlit as st

from src.ai.text_to_sql import answer_question
from views.common import badge, require_data

SAMPLES = ["Which team has the highest SLA breach rate?",
           "Why did SLA breaches rise last month?",
           "Which affected service had the most tickets last month?",
           "What is the average resolution time by priority?",
           "Delete all tickets"]


def _show(turn):
    with st.chat_message("user"):
        st.write(turn["question"])
    with st.chat_message("assistant"):
        r = turn["result"]
        badge(r["status"])
        st.write(r["answer"])
        if r["ungrounded_numbers"]:
            st.warning("Check: these numbers in the explanation were not found in the result rows: "
                       + ", ".join(r["ungrounded_numbers"]))
        if r["sql"]:
            with st.expander("Generated SQL"):
                st.code(r["sql"], language="sql")
        if r["dataframe"] is not None:
            st.dataframe(r["dataframe"], hide_index=True)
        if r["provider"]:
            st.caption(f"Model: {r['provider']}")


def render():
    st.header("Ask IncidentIQ")
    st.caption("Questions become a single read-only SELECT. The SQL guard blocks anything else, "
               "and every attempt is logged in the Data Quality & Audit page.")
    if not require_data():
        return
    history = st.session_state.setdefault("chat", [])
    st.caption("Try a sample question:")
    left, right = st.columns(2)
    clicked = next((q for i, q in enumerate(SAMPLES)
                    if (left if i % 2 == 0 else right).button(q, key=f"sample{i}", width="stretch")), None)
    question = st.chat_input("Ask about incidents, SLAs, teams, services...") or clicked
    if question:
        with st.spinner("Thinking..."):
            history.append({"question": question, "result": answer_question(question)})
    for turn in reversed(history):
        _show(turn)
    if history and st.button("Clear chat"):
        history.clear()
        st.rerun()

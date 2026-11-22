"""Ticket Assistant page: summary + similar past incidents + suggested fix."""
import streamlit as st

from src.ai.similar_incidents import SimilarIncidentIndex, suggest_fix
from src.ai.summarizer import get_ticket, summarize_ticket
from views.common import require_data, table


@st.cache_resource(show_spinner="Building the similarity index...")
def _index():
    return SimilarIncidentIndex()


def _similar_section(text, exclude_id=None):
    matches = _index().find_similar(text, top_k=5, exclude_id=exclude_id)
    st.subheader("Top 5 similar past incidents")
    st.dataframe(matches, hide_index=True)
    with st.spinner("Drafting a suggested fix from those resolutions..."):
        fix = suggest_fix(text, matches)
    st.subheader("Suggested fix")
    if fix["status"] == "no_match":
        st.info(fix["suggestion"])
        return
    st.markdown(fix["suggestion"])
    st.caption(f"Cited tickets: {', '.join(fix['cited_ids']) or 'none'} - model: {fix['provider']}")
    if fix["invalid_citations"]:
        st.warning("The model cited tickets that were not retrieved: " + ", ".join(fix["invalid_citations"]))


def render():
    st.header("Ticket Assistant")
    if not require_data():
        return
    mode = st.radio("Input", ["Pick an existing ticket", "Paste a new ticket"], horizontal=True)
    if mode == "Pick an existing ticket":
        recent = table("SELECT ticket_id, priority, status, short_description FROM tickets "
                       "ORDER BY opened_at DESC LIMIT 300")
        labels = (recent["ticket_id"] + " | " + recent["priority"] + " | " + recent["status"] + " | "
                  + recent["short_description"]).tolist()
        choice = st.selectbox("Ticket", labels)
        ticket = get_ticket(choice.split(" | ")[0])
        st.json({k: ticket[k] for k in ("ticket_id", "priority", "status", "opened_at", "resolved_at",
                                        "category", "subcategory", "affected_service", "team_name")},
                expanded=False)
        st.write(f"**{ticket['short_description']}** - {ticket['description']}")
        if st.button("Analyse ticket", type="primary"):
            with st.spinner("Summarising..."):
                summary = summarize_ticket(ticket)
            st.subheader("Summary")
            st.markdown(summary["summary"].replace("\n", "  \n"))
            st.caption(f"{summary['word_count']} words - model: {summary['provider']}")
            _similar_section(f"{ticket['short_description']}. {ticket['description']}", ticket["ticket_id"])
    else:
        text = st.text_area("Describe the incident", "VPN connection fails for remote users with a "
                            "timeout during authentication. Several people at the London office affected.")
        if st.button("Find similar incidents", type="primary") and text.strip():
            with st.spinner("Summarising..."):
                summary = summarize_ticket({"short_description": text[:120], "description": text,
                                            "status": "New"})
            st.subheader("Summary")
            st.markdown(summary["summary"].replace("\n", "  \n"))
            _similar_section(text)

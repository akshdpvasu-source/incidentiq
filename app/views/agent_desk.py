"""Agent Desk page: the IT AGENT's view of tickets raised through "Raise a Ticket".

Pick a ticket from the queue -> see similar solved tickets (instant, no LLM) -> get an AI summary
and suggested fix -> chat with the AI about this one ticket -> resolve it. The resolution notes
become part of the knowledge base the next time the similarity index is rebuilt.
"""
import pandas as pd
import streamlit as st

from src import config, portal
from src.ai.agent_chat import STARTER_QUESTIONS, chat_reply
from src.ai.similar_incidents import suggest_fix
from src.ai.summarizer import summarize_ticket
from views.common import clear_caches, require_data, similar_index

S = st.session_state
FILTERS = ["Open tickets", "Assigned to me", "All portal tickets"]
QUEUE_COLUMNS = ["ticket_id", "status", "priority", "short_description", "category", "requester_name",
                 "site", "team_name", "agent_name", "opened_at", "due_at"]
STATUS_COLOR = {"Open": "orange", "In Progress": "blue", "Resolved": "green", "Closed": "green"}
MATCH_COLUMNS = ["ticket_id", "similarity", "category", "short_description", "resolution_notes",
                 "resolution_code"]


def _ticket_text(ticket):
    return f"{ticket['short_description']}. {ticket['description']}"


def _matches(ticket):
    """Top 5 similar solved tickets (empty table if there is no solved history yet)."""
    try:
        return similar_index().find_similar(_ticket_text(ticket), top_k=5, exclude_id=ticket["ticket_id"])
    except ValueError:
        return pd.DataFrame(columns=["ticket_id", "similarity", "category", "subcategory",
                                     "short_description", "resolution_notes", "resolution_code"])


# ------------------------------------------------------------------ queue
def _pick_agent(queue):
    agents = portal.list_agents()
    if agents.empty:
        st.warning("No agents in the database yet. Load a ticket CSV on the Pipeline page first.")
        return None
    labels = [f"{r.agent_name} ({r.team_name})" for r in agents.itertuples()]
    default = next((i for i, r in enumerate(agents.itertuples()) if r.team_name == "ServiceDesk-L1"), 0)
    choice = st.selectbox("You are working as", labels, index=default, key="desk_agent",
                          help="Pick the agent you are playing. Taking or resolving a ticket is recorded under this name.")
    return agents.iloc[labels.index(choice)]["agent_name"]


def _queue_table(queue, agent_name):
    view_filter = st.radio("Show", FILTERS, horizontal=True, key="desk_filter")
    view = queue
    if view_filter == "Open tickets":
        view = queue[queue["status"].isin(portal.OPEN_STATUSES)]
    elif view_filter == "Assigned to me":
        view = queue[queue["agent_name"] == agent_name]
    view = view.reset_index(drop=True)
    if view.empty:
        st.info("No tickets match this filter.")
        return None
    st.caption("Click a row to open the ticket. Most urgent and oldest open tickets are first.")
    event = st.dataframe(view[QUEUE_COLUMNS], hide_index=True, on_select="rerun",
                         selection_mode="single-row", key="queue_table")
    rows = event.selection.rows if event and event.selection else []
    return view.iloc[rows[0] if rows and rows[0] < len(view) else 0]["ticket_id"]


# ----------------------------------------------------------------- detail
def _header(ticket):
    st.subheader(f"{ticket['ticket_id']} - {ticket['short_description']}")
    sla_text, breached = portal.sla_status(ticket["opened_at"], ticket["sla_target_hours"],
                                           ticket["resolved_at"])
    a, b, c, d = st.columns(4)
    a.markdown(f"Status  \n:{STATUS_COLOR.get(ticket['status'], 'gray')}-background[**{ticket['status']}**]")
    b.metric("Priority", f"{ticket['priority']} ({config.PRIORITY_NAMES[ticket['priority']]})")
    c.metric("SLA", sla_text, delta="breached" if breached else None, delta_color="inverse")
    d.metric("Assigned to", ticket["agent_name"] or "Nobody yet")
    st.caption(f"Raised by **{ticket['requester_name']}** from {ticket['site']} on {ticket['opened_at']} - "
               f"{ticket['category']} / {ticket['subcategory']} - team {ticket['team_name']}")
    with st.container(border=True):
        st.write(ticket["description"])


def _take_button(ticket, agent_name):
    if ticket["status"] not in portal.OPEN_STATUSES or ticket["agent_name"] == agent_name:
        return
    if st.button("Take this ticket", type="primary" if ticket["status"] == "Open" else "secondary"):
        try:
            portal.assign_ticket(ticket["ticket_id"], agent_name)
        except portal.PortalError as exc:
            st.warning(str(exc))
            return
        st.rerun()


def _ai_tab(ticket, matches):
    st.markdown("**Most similar solved tickets** (instant search, no AI involved)")
    if matches.empty or matches["similarity"].max() < config.SIMILARITY_THRESHOLD:
        best = 0 if matches.empty else matches["similarity"].max()
        st.info(f"No strongly similar past ticket (best similarity {best}; the cut-off is "
                f"{config.SIMILARITY_THRESHOLD}). Treat this as a new kind of problem.")
    if not matches.empty:
        st.dataframe(matches[MATCH_COLUMNS], hide_index=True)
    cache = S.setdefault("desk_ai_cache", {})
    tid = ticket["ticket_id"]
    if st.button("Get AI summary + suggested fix", type="primary", key=f"ai_btn_{tid}"):
        with st.spinner("Asking the AI..."):
            cache[tid] = {"summary": summarize_ticket(ticket), "fix": suggest_fix(_ticket_text(ticket), matches)}
    result = cache.get(tid)
    if not result:
        return
    st.markdown("**Summary**")
    st.markdown(result["summary"]["summary"].replace("\n", "  \n"))
    st.caption(f"model: {result['summary']['provider']}")
    st.markdown("**Suggested fix**")
    fix = result["fix"]
    (st.info if fix["status"] == "no_match" else st.markdown)(fix["suggestion"])
    if fix["status"] == "ok":
        st.caption(f"Cited tickets: {', '.join(fix['cited_ids']) or 'none'} - model: {fix['provider']}")
        if fix["invalid_citations"]:
            st.warning("The model cited tickets that were not retrieved: " + ", ".join(fix["invalid_citations"]))


def _ask(ticket, matches, agent_name, question, history):
    """Send one question to the copilot, show the exchange, store it and log it."""
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                out = chat_reply(ticket, matches, history, question)
            except ValueError as exc:
                st.warning(str(exc))
                return
        st.markdown(out["reply"])
        st.caption(f"model: {out['provider']} - cited: {', '.join(out['cited_ids']) or 'none'}")
        if out["invalid_citations"]:
            st.warning("The model cited tickets that were not retrieved: " + ", ".join(out["invalid_citations"]))
    history += [{"role": "user", "content": question},
                {"role": "assistant", "content": out["reply"], "meta": out["provider"]}]
    portal.record_event(ticket["ticket_id"], agent_name, "ai_chat", f"Q: {question} | A: {out['reply']}")


def _chat_tab(ticket, matches, agent_name):
    history = S.setdefault("desk_chat", {}).setdefault(ticket["ticket_id"], [])
    st.caption("The AI sees this ticket and the similar solved tickets above, and can only give advice - "
               "it cannot change anything. Answers cite ticket ids; advice from outside the past cases is labelled.")
    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    pending = S.pop("pending_question", None)
    if not history and pending is None:
        cols = st.columns(2)
        for i, q in enumerate(STARTER_QUESTIONS):
            cols[i % 2].button(q, key=f"starter_{i}", on_click=S.__setitem__, args=("pending_question", q))
    question = st.chat_input("Ask the AI about this ticket...", key="desk_chat_input") or pending
    if question:
        _ask(ticket, matches, agent_name, question, history)


def _fill_notes(key, text):
    S[key] = text


def _resolve_tab(ticket, agent_name, matches):
    if ticket["status"] not in portal.OPEN_STATUSES:
        st.success(f"Resolved by **{ticket['agent_name']}** on {ticket['resolved_at']} "
                   f"({ticket['resolution_hours']} h) - code **{ticket['resolution_code']}**")
        st.write(ticket["resolution_notes"])
        st.caption("This resolution is now part of the knowledge base: after the similarity index is "
                   "rebuilt, it can be suggested for similar future tickets.")
        return
    key = f"notes_{ticket['ticket_id']}"
    fix = S.get("desk_ai_cache", {}).get(ticket["ticket_id"], {}).get("fix")
    if fix and fix["status"] == "ok":
        st.button("Use the AI's suggested fix as a starting point", on_click=_fill_notes,
                  args=(key, fix["suggestion"]))
    codes = portal.resolution_codes()
    top_code = matches.iloc[0]["resolution_code"] if not matches.empty else None  # default: most similar fix
    code = st.selectbox("Resolution code", codes, index=codes.index(top_code) if top_code in codes else 0,
                        key=f"code_{ticket['ticket_id']}")
    notes = st.text_area("What did you do to fix it?", key=key, height=140, max_chars=portal.MAX_NOTES,
                         help="Write it so a colleague could repeat the fix. It is saved as the resolution.")
    if st.button("Resolve ticket", type="primary"):
        try:
            result = portal.resolve_ticket(ticket["ticket_id"], agent_name, notes, code)
        except portal.PortalError as exc:
            st.warning(str(exc))
            return
        clear_caches()  # rebuild the similarity index (it now includes this fix) and refresh the dashboard
        S["desk_flash"] = (f"{ticket['ticket_id']} resolved in {result['resolution_hours']} h - "
                           + ("SLA breached." if result["sla_breached"] else "within SLA."))
        st.rerun()


def render():
    st.header("Agent Desk")
    st.caption("IT agent view: pick up tickets raised through the portal and solve them with help from past cases.")
    if not require_data():
        return
    portal.refresh_open_ticket_clocks()
    queue = portal.portal_queue()
    if S.get("desk_flash"):
        st.success(S.pop("desk_flash"))
    if queue.empty:
        st.info("No portal tickets yet. Open **Raise a Ticket**, submit one as an employee, then come back here.")
        return
    counts = queue["status"].value_counts()
    a, b, c = st.columns(3)
    a.metric("Open", int(counts.get("Open", 0)), border=True)
    b.metric("In progress", int(counts.get("In Progress", 0)), border=True)
    c.metric("Resolved", int(counts.get("Resolved", 0)), border=True)

    agent_name = _pick_agent(queue)
    if agent_name is None:
        return
    ticket_id = _queue_table(queue, agent_name)
    if ticket_id is None:
        return
    st.divider()
    ticket = portal.get_portal_ticket(ticket_id)
    _header(ticket)
    _take_button(ticket, agent_name)
    matches = _matches(ticket)
    tab_ai, tab_chat, tab_resolve, tab_log = st.tabs(["AI suggestions", "Chat with AI", "Resolve", "Timeline"])
    with tab_ai:
        _ai_tab(ticket, matches)
    with tab_chat:
        _chat_tab(ticket, matches, agent_name)
    with tab_resolve:
        _resolve_tab(ticket, agent_name, matches)
    with tab_log:
        events = portal.get_events(ticket_id)
        st.dataframe(events, hide_index=True)

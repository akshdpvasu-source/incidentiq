"""Raise a Ticket page: the EMPLOYEE's view. Submit a problem, then track it.

The ticket is saved as an Open ticket and lands in the Agent Desk queue. If the employee is not
sure of the category, IncidentIQ guesses it from the most similar solved tickets (TF-IDF vote).
"""
import streamlit as st

from src import portal
from views.common import clear_caches, require_data, similar_index

S = st.session_state
NOT_SURE_LABEL = "Not sure - let IncidentIQ decide"
STATUS_TEXT = {"Open": (":orange-background[**Waiting for an agent**]", "Your ticket is in the queue."),
               "In Progress": (":blue-background[**Being worked on**]", "An agent has taken your ticket."),
               "Resolved": (":green-background[**Fixed**]", "The agent has resolved your ticket."),
               "Closed": (":green-background[**Closed**]", "This ticket is closed.")}


def _classify(category_choice, text):
    """Return (category, subcategory, note). Uses the AI-free TF-IDF vote; never blocks submission."""
    guess = None
    try:
        guess = portal.predict_category(similar_index(), text)
    except Exception:  # no index yet (no solved tickets) - the form still works with a manual category
        pass
    if category_choice == NOT_SURE_LABEL:
        if guess is None:
            return None, None, "We could not work out the category from your description. Please choose one."
        category, sub, strength = guess
        return category, sub, (f"Category guessed from similar solved tickets: **{category}** / {sub} "
                               f"(match strength {strength:.2f}).")
    sub = guess[1] if guess and guess[0] == category_choice else None
    return category_choice, sub, ""


def _submit_form():
    st.subheader("Describe your problem")
    with st.form("raise_ticket"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Your name", placeholder="e.g. Priya Sharma")
        email = c2.text_input("Email (optional)", placeholder="priya@company.com")
        c1, c2 = st.columns(2)
        site = c1.selectbox("Where are you working from?", portal.SITES)
        urgency = c2.selectbox("How urgent is it?", list(portal.URGENCY_CHOICES), index=2)
        title = st.text_input("Short title", max_chars=portal.MAX_SHORT,
                              placeholder="e.g. Cannot connect to VPN from home")
        description = st.text_area("What is happening?", max_chars=portal.MAX_DESC, height=140,
                                   placeholder="What did you try, what error do you see, when did it start, "
                                               "who else is affected?")
        category_choice = st.selectbox("Category", [NOT_SURE_LABEL] + list(portal.category_options()))
        submitted = st.form_submit_button("Submit ticket", type="primary")
    if not submitted:
        return
    try:
        category, sub, note = _classify(category_choice, f"{title}. {description}")
        if category is None:
            st.warning(note)
            return
        ticket_id = portal.create_ticket(name, email, site, title, description, category,
                                         portal.URGENCY_CHOICES[urgency], subcategory=sub)
    except portal.PortalError as exc:
        st.warning(str(exc))
        return
    clear_caches()  # dashboard numbers and the ticket count in the sidebar change
    S["raise_last_id"] = ticket_id  # pre-fills the tracker, even after visiting another page
    S["raise_submitted"] = {"id": ticket_id, "note": note}
    st.rerun()


def _tracker():
    st.subheader("Track a ticket")
    last = S.get("raise_last_id", "")
    ticket_id = st.text_input("Ticket number", value=last, key=f"raise_track_{last}",
                              placeholder="e.g. INC0900001")
    if not ticket_id.strip():
        return
    info = portal.track_ticket(ticket_id)
    if info is None:
        st.info("No ticket raised through this form has that number.")
        return
    label, message = STATUS_TEXT.get(info["status"], (info["status"], ""))
    st.markdown(f"**{info['ticket_id']}** - {info['short_description']}")
    st.markdown(f"{label}  {message}")
    a, b, c = st.columns(3)
    a.metric("Priority", info["priority"])
    b.metric("Handled by", info["agent_name"] or "Not assigned yet")
    due, _ = portal.sla_status(info["opened_at"], info["sla_target_hours"], info["resolved_at"])
    c.metric("Target (SLA)", due)
    if info["resolution_notes"]:
        st.success(f"**How it was fixed:** {info['resolution_notes']}")


def render():
    st.header("Raise a Ticket")
    st.caption("Employee view: tell IT what is wrong. Your ticket goes to the Agent Desk queue.")
    if not require_data():
        return
    done = S.pop("raise_submitted", None)
    if done:
        st.success(f"Ticket **{done['id']}** submitted. Keep this number to track it below. "
                   "An agent will pick it up from the Agent Desk.")
        if done["note"]:
            st.info(done["note"])
    _submit_form()
    st.divider()
    _tracker()

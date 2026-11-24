"""Raise-a-Ticket + Agent Desk backend (no Streamlit code in here, so it is easy to test).

The story it supports:
    employee raises a ticket  ->  agent picks it up  ->  AI suggests fixes from past cases
    ->  agent resolves it  ->  the resolution joins the knowledge base for next time.

Portal tickets are written to the same `tickets` table as the CSV/API tickets, so the
dashboard and the 8 analytics queries see them automatically. Two small extra tables hold
what the CSV data does not have: `portal_requests` (who asked) and `ticket_events` (audit
trail). Neither is exposed to the text-to-SQL AI.

Ticket ids are INC09xxxxx so they never collide with generated (INC01..., INC05...) ids and
still match the INC\\d{7} citation pattern used by the similar-incident search.
"""
import re
from datetime import datetime

import pandas as pd

from src import config
from src.catalog import CATEGORIES, CATEGORY_TEAMS, TEAMS
from src.load_db import connect, init_db, read_table

PORTAL_BATCH = "B-PORTAL"
ID_PREFIX = "INC09"
FIRST_ID = 900001
NOT_SURE = "Not sure"
CATEGORY_GUESS_THRESHOLD = 0.30  # best similarity needed before we auto-pick a category
OPEN_STATUSES = config.OPEN_STATUSES  # ("Open", "In Progress")

# What the employee sees -> internal priority. Wording describes impact, not jargon.
URGENCY_CHOICES = {
    "Critical - a service is down for many people": "P1",
    "High - I cannot do my work": "P2",
    "Medium - my work is slowed down": "P3",
    "Low - minor issue or a question": "P4",
}
SITES = ["London office", "Bangalore campus", "New York HQ", "Singapore office",
         "Frankfurt office", "Home office"]
DEFAULT_RESOLUTION_CODES = ["Restart", "Config Change", "Hardware Replacement", "Workaround",
                            "User Education", "Capacity Increase", "Password Reset"]
MAX_SHORT, MAX_DESC, MAX_NOTES = 150, 2000, 2000


class PortalError(ValueError):
    """Bad input or an action that is not allowed (the UI shows the message as a warning)."""


# ------------------------------------------------------------------ helpers
def _now_str(now):
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _clean(text, limit, label, required=False):
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if required and not value:
        raise PortalError(f"{label} is required.")
    if len(value) > limit:
        raise PortalError(f"{label} is too long (maximum {limit} characters).")
    return value


def _is_portal_ticket(conn, ticket_id):
    return conn.execute("SELECT 1 FROM portal_requests WHERE ticket_id = ?", (ticket_id,)).fetchone() is not None


def log_event(conn, ticket_id, actor, event_type, detail="", now=None):
    conn.execute("INSERT INTO ticket_events (ticket_id, at, actor, event_type, detail) VALUES (?,?,?,?,?)",
                 (ticket_id, _now_str(now), actor, event_type, detail[:MAX_NOTES]))


def record_event(ticket_id, actor, event_type, detail="", now=None, db_path=None):
    """Write one audit-trail line for a portal ticket (own connection, own transaction)."""
    conn = connect(db_path)
    try:
        with conn:
            log_event(conn, ticket_id, actor, event_type, detail, now)
    finally:
        conn.close()


def category_options():
    """{category: {"subs": [...], "services": [...]}} for the form drop-downs."""
    return {c: {"subs": list(v["subs"]), "services": list(v["services"])} for c, v in CATEGORIES.items()}


def predict_category(index, text, top_k=5):
    """Guess (category, subcategory, strength) from the most similar solved tickets, or None.

    Each of the top matches votes for its category with its similarity as the weight. `strength`
    is the best similarity among the winning category's matches (0-1). A wrong guess is worse
    than asking, so the bar is stricter than the 0.2 used for suggesting fixes: a single shared
    word like "machine" must not file a coffee-machine complaint under virtual machines.
    """
    matches = index.find_similar(text, top_k=top_k)
    if matches.empty or matches["similarity"].max() < CATEGORY_GUESS_THRESHOLD:
        return None
    matches = matches[matches["similarity"] >= config.SIMILARITY_THRESHOLD]
    votes = matches.groupby("category")["similarity"].sum().sort_values(ascending=False)
    category = votes.index[0]
    winners = matches[matches["category"] == category]
    sub = winners.groupby("subcategory")["similarity"].sum().idxmax()
    return category, sub, round(float(winners["similarity"].max()), 2)


# ------------------------------------------------------------ user side
def create_ticket(requester_name, requester_email, site, short_description, description, category,
                  priority, subcategory=None, affected_service=None, now=None, db_path=None):
    """Save a new Open ticket raised by an employee and return its ticket_id."""
    name = _clean(requester_name, 80, "Your name", required=True)
    email = _clean(requester_email, 120, "Email")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise PortalError("That email address does not look valid.")
    short = _clean(short_description, MAX_SHORT, "Title", required=True)
    desc = _clean(description, MAX_DESC, "Description", required=True)
    site = _clean(site, 60, "Site") or "Unknown"
    if category not in CATEGORY_TEAMS:
        raise PortalError("Please choose a category.")
    if priority not in config.SLA_HOURS:
        raise PortalError("Unknown urgency.")
    sub = subcategory if subcategory in CATEGORIES[category]["subs"] else "Unclassified"
    service = affected_service if affected_service in CATEGORIES[category]["services"] else "Unknown"
    team = CATEGORY_TEAMS[category][0]

    when = now or datetime.now()
    stamp = _now_str(when)
    init_db(db_path)
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")  # lock so two submissions cannot get the same id
        last = conn.execute("SELECT MAX(ticket_id) FROM tickets WHERE ticket_id LIKE ?",
                            (ID_PREFIX + "%",)).fetchone()[0]
        ticket_id = f"INC{(int(last[3:]) + 1) if last else FIRST_ID:07d}"
        conn.execute("INSERT OR IGNORE INTO batches (batch_id, source_name, source_type, ingested_at, "
                     "input_rows, loaded_rows, rejected_rows, status) VALUES (?, 'Raise-a-Ticket portal', "
                     "'portal', ?, 0, 0, 0, 'loaded')", (PORTAL_BATCH, stamp))
        conn.execute("INSERT OR IGNORE INTO teams (team_name, region) VALUES (?, ?)",
                     (team, TEAMS.get(team, "Unknown")))
        team_id = conn.execute("SELECT team_id FROM teams WHERE team_name = ?", (team,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO categories (category, subcategory, affected_service) "
                     "VALUES (?, ?, ?)", (category, sub, service))
        category_id = conn.execute("SELECT category_id FROM categories WHERE category = ? AND "
                                   "subcategory = ? AND affected_service = ?",
                                   (category, sub, service)).fetchone()[0]
        conn.execute(
            "INSERT INTO tickets (ticket_id, batch_id, category_id, team_id, agent_id, priority, status, "
            "opened_at, resolved_at, resolution_hours, sla_target_hours, sla_breached, ticket_age_days, "
            "opened_hour, opened_weekday, opened_month, reopen_count, short_description, description) "
            "VALUES (?, ?, ?, ?, NULL, ?, 'Open', ?, NULL, NULL, ?, 0, 0.0, ?, ?, ?, 0, ?, ?)",
            (ticket_id, PORTAL_BATCH, category_id, team_id, priority, stamp, config.SLA_HOURS[priority],
             when.hour, when.strftime("%A"), when.strftime("%Y-%m"), short, desc))
        conn.execute("INSERT INTO portal_requests (ticket_id, requester_name, requester_email, site, "
                     "submitted_at) VALUES (?, ?, ?, ?, ?)", (ticket_id, name, email or None, site, stamp))
        conn.execute("UPDATE batches SET input_rows = input_rows + 1, loaded_rows = loaded_rows + 1 "
                     "WHERE batch_id = ?", (PORTAL_BATCH,))
        log_event(conn, ticket_id, name, "submitted", f"{priority} / {category} / {sub}", when)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return ticket_id


def track_ticket(ticket_id, db_path=None):
    """What the employee may see about their ticket (None if it is not a portal ticket)."""
    ticket_id = (ticket_id or "").strip().upper()
    df = read_table(
        "SELECT t.ticket_id, t.status, t.priority, t.opened_at, t.resolved_at, t.short_description, "
        "t.sla_target_hours, a.agent_name, r.resolution_notes FROM tickets t "
        "JOIN portal_requests p ON p.ticket_id = t.ticket_id "
        "LEFT JOIN agents a ON a.agent_id = t.agent_id "
        "LEFT JOIN resolutions r ON r.ticket_id = t.ticket_id WHERE t.ticket_id = ?", (ticket_id,), db_path)
    if df.empty:
        return None
    return {k: (None if pd.isna(v) else v) for k, v in df.to_dict("records")[0].items()}


# ----------------------------------------------------------- agent side
def list_agents(db_path=None):
    """All agents with their team, for the 'Working as' picker."""
    return read_table("SELECT a.agent_name, tm.team_name, a.experience_level FROM agents a "
                      "LEFT JOIN teams tm ON tm.team_id = a.team_id ORDER BY tm.team_name, a.agent_name",
                      db_path=db_path)


def resolution_codes(db_path=None):
    """Resolution codes already used in the data, plus sensible defaults."""
    df = read_table("SELECT DISTINCT resolution_code FROM resolutions WHERE resolution_code IS NOT NULL "
                    "ORDER BY resolution_code", db_path=db_path)
    codes = df["resolution_code"].tolist() if not df.empty else []
    return codes or DEFAULT_RESOLUTION_CODES


def portal_queue(db_path=None):
    """Every portal ticket, open ones first (most urgent, then oldest)."""
    df = read_table(
        "SELECT t.ticket_id, t.status, t.priority, t.opened_at, t.short_description, c.category, "
        "tm.team_name, a.agent_name, p.requester_name, p.site, t.sla_target_hours "
        "FROM tickets t JOIN portal_requests p ON p.ticket_id = t.ticket_id "
        "LEFT JOIN categories c ON c.category_id = t.category_id "
        "LEFT JOIN teams tm ON tm.team_id = t.team_id LEFT JOIN agents a ON a.agent_id = t.agent_id",
        db_path=db_path)
    if df.empty:
        return df
    df["is_open"] = df["status"].isin(OPEN_STATUSES)
    df["due_at"] = (pd.to_datetime(df["opened_at"]) + pd.to_timedelta(df["sla_target_hours"], unit="h")
                    ).dt.strftime("%Y-%m-%d %H:%M")
    return df.sort_values(["is_open", "priority", "opened_at"], ascending=[False, True, True]
                          ).drop(columns="is_open").reset_index(drop=True)


def get_portal_ticket(ticket_id, db_path=None):
    """One portal ticket with requester, team, agent and resolution (None if not found)."""
    df = read_table(
        "SELECT t.ticket_id, t.status, t.priority, t.opened_at, t.resolved_at, t.resolution_hours, "
        "t.sla_target_hours, t.sla_breached, t.short_description, t.description, c.category, "
        "c.subcategory, c.affected_service, tm.team_name, a.agent_name, p.requester_name, "
        "p.requester_email, p.site, r.resolution_notes, r.resolution_code "
        "FROM tickets t JOIN portal_requests p ON p.ticket_id = t.ticket_id "
        "LEFT JOIN categories c ON c.category_id = t.category_id "
        "LEFT JOIN teams tm ON tm.team_id = t.team_id LEFT JOIN agents a ON a.agent_id = t.agent_id "
        "LEFT JOIN resolutions r ON r.ticket_id = t.ticket_id WHERE t.ticket_id = ?", (ticket_id,), db_path)
    if df.empty:
        return None
    return {k: (None if pd.isna(v) else v) for k, v in df.to_dict("records")[0].items()}


def get_events(ticket_id, db_path=None):
    return read_table("SELECT at, actor, event_type, detail FROM ticket_events WHERE ticket_id = ? "
                      "ORDER BY event_id", (ticket_id,), db_path)


def _agent_row(conn, agent_name):
    row = conn.execute("SELECT a.agent_id, a.team_id FROM agents a WHERE a.agent_name = ?",
                       (agent_name,)).fetchone()
    if row is None:
        raise PortalError(f"Unknown agent: {agent_name}")
    return row


def _open_portal_ticket(conn, ticket_id):
    if not _is_portal_ticket(conn, ticket_id):
        raise PortalError(f"{ticket_id} is not a portal ticket.")
    status = conn.execute("SELECT status FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()[0]
    if status not in OPEN_STATUSES:
        raise PortalError(f"{ticket_id} is already {status}.")


def assign_ticket(ticket_id, agent_name, now=None, db_path=None):
    """Agent takes the ticket: it moves to the agent's team and becomes In Progress."""
    conn = connect(db_path)
    try:
        _open_portal_ticket(conn, ticket_id)
        agent_id, team_id = _agent_row(conn, agent_name)
        with conn:
            conn.execute("UPDATE tickets SET agent_id = ?, team_id = COALESCE(?, team_id), "
                         "status = 'In Progress' WHERE ticket_id = ?", (agent_id, team_id, ticket_id))
            log_event(conn, ticket_id, agent_name, "assigned", "Agent took the ticket", now)
    finally:
        conn.close()


def resolve_ticket(ticket_id, agent_name, resolution_notes, resolution_code, now=None, db_path=None):
    """Close the ticket with notes. The notes join the knowledge base (similar-incident search)."""
    notes = _clean(resolution_notes, MAX_NOTES, "Resolution notes", required=True)
    if len(notes) < 15:
        raise PortalError("Please describe what you did in a little more detail (15+ characters).")
    code = _clean(resolution_code, 60, "Resolution code", required=True)
    when = now or datetime.now()
    conn = connect(db_path)
    try:
        _open_portal_ticket(conn, ticket_id)
        agent_id, team_id = _agent_row(conn, agent_name)
        opened, target = conn.execute("SELECT opened_at, sla_target_hours FROM tickets WHERE ticket_id = ?",
                                      (ticket_id,)).fetchone()
        hours = round((when - datetime.strptime(opened, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600, 2)
        with conn:
            conn.execute("UPDATE tickets SET status = 'Resolved', resolved_at = ?, resolution_hours = ?, "
                         "sla_breached = ?, ticket_age_days = ?, agent_id = COALESCE(agent_id, ?), "
                         "team_id = COALESCE(team_id, ?) WHERE ticket_id = ?",
                         (_now_str(when), hours, int(hours > target), round(hours / 24, 2), agent_id,
                          team_id, ticket_id))
            conn.execute("DELETE FROM resolutions WHERE ticket_id = ?", (ticket_id,))
            conn.execute("INSERT INTO resolutions (ticket_id, resolution_notes, resolution_code) "
                         "VALUES (?, ?, ?)", (ticket_id, notes, code))
            log_event(conn, ticket_id, agent_name, "resolved", f"{code}: {notes}", when)
    finally:
        conn.close()
    return {"ticket_id": ticket_id, "resolution_hours": hours, "sla_breached": hours > target}


def refresh_open_ticket_clocks(now=None, db_path=None):
    """Keep age and SLA-breach flags of still-open portal tickets current (they change by the minute)."""
    when = now or datetime.now()
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT t.ticket_id, t.opened_at, t.sla_target_hours FROM tickets t "
                            "JOIN portal_requests p ON p.ticket_id = t.ticket_id "
                            "WHERE t.status IN ('Open', 'In Progress')").fetchall()
        with conn:
            for tid, opened, target in rows:
                hours = (when - datetime.strptime(opened, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
                conn.execute("UPDATE tickets SET ticket_age_days = ?, sla_breached = ? WHERE ticket_id = ?",
                             (round(hours / 24, 2), int(hours > target), tid))
    finally:
        conn.close()
    return len(rows)


def sla_status(opened_at, sla_target_hours, resolved_at=None, now=None):
    """Plain-English SLA line, e.g. ('3h 12m left', False) or ('breached by 1h 05m', True)."""
    fmt = "%Y-%m-%d %H:%M:%S"
    end = datetime.strptime(resolved_at, fmt) if resolved_at else (now or datetime.now())
    left = datetime.strptime(opened_at, fmt) + pd.Timedelta(hours=float(sla_target_hours)) - end
    minutes = int(abs(left.total_seconds()) // 60)
    text = f"{minutes // 60}h {minutes % 60:02d}m"
    if left.total_seconds() >= 0:
        return (f"met with {text} to spare" if resolved_at else f"{text} left"), False
    return f"breached by {text}", True

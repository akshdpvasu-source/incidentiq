"""Raise a Ticket / Agent Desk backend: create, queue, assign, resolve, track, safety."""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from src import analytics, config, portal
from src.ai.similar_incidents import SimilarIncidentIndex
from src.ai.sql_guard import execute_readonly, validate_sql
from src.generate_data import generate_tickets
from src.ingest_files import ingest_file
from src.load_db import connect, init_db, read_table
from src.load_db import load_batch
from src.transform import clean

NOW = datetime(2026, 11, 20, 10, 0, 0)
VPN_TEXT = ("Cannot connect to VPN from home. VPN connection fails with a timeout during "
            "authentication for remote users.")


@pytest.fixture()
def db(tmp_path):
    """A small database of past (mostly solved) tickets, loaded through the real pipeline."""
    path = tmp_path / "t.db"
    raw = tmp_path / "past.csv"
    generate_tickets(600, 7, "Test", 100000).to_csv(raw, index=False)
    df, batch = ingest_file(raw, path)
    cleaned, rejected, _ = clean(df, batch)
    load_batch(cleaned, rejected, batch, path)
    return path


def _new(db, **overrides):
    args = dict(requester_name="Priya Sharma", requester_email="priya@example.com", site="Home office",
                short_description="Cannot connect to VPN", description=VPN_TEXT, category="VPN",
                priority="P2", subcategory="Cannot connect", now=NOW, db_path=db)
    args.update(overrides)
    return portal.create_ticket(**args)


def _agent(db, team=None):
    agents = portal.list_agents(db)
    pick = agents[agents["team_name"] == team] if team else agents
    return pick.iloc[0]["agent_name"]


# ----------------------------------------------------------------- creating
def test_create_ticket_saves_an_open_unassigned_ticket(db):
    first, second = _new(db), _new(db)
    assert (first, second) == ("INC0900001", "INC0900002")          # sequential, own id range
    t = portal.get_portal_ticket(first, db)
    assert (t["status"], t["priority"], t["agent_name"]) == ("Open", "P2", None)
    assert t["team_name"] == "NetOps-Team"                          # routed by category
    assert (t["sla_target_hours"], t["requester_name"], t["site"]) == (8, "Priya Sharma", "Home office")
    assert (t["category"], t["subcategory"], t["affected_service"]) == ("VPN", "Cannot connect", "Unknown")
    row = read_table("SELECT opened_hour, opened_weekday, opened_month, sla_breached FROM tickets "
                     "WHERE ticket_id = ?", (first,), db).iloc[0]
    assert tuple(row) == (10, "Friday", "2026-11", 0)


def test_portal_batch_reconciles_like_every_other_batch(db):
    _new(db), _new(db)
    b = read_table("SELECT * FROM batches WHERE batch_id = ?", (portal.PORTAL_BATCH,), db).iloc[0]
    assert (b["input_rows"], b["loaded_rows"], b["rejected_rows"], b["status"]) == (2, 2, 0, "loaded")
    n = read_table("SELECT COUNT(*) AS n FROM tickets WHERE batch_id = ?", (portal.PORTAL_BATCH,), db)["n"][0]
    assert n == 2


@pytest.mark.parametrize("bad, message", [
    (dict(requester_name=" "), "name is required"),
    (dict(requester_email="not-an-email"), "email"),
    (dict(short_description=""), "Title is required"),
    (dict(description="   "), "Description is required"),
    (dict(short_description="x" * 151), "too long"),
    (dict(category="Toaster"), "category"),
    (dict(priority="P9"), "urgency"),
])
def test_invalid_input_is_rejected_and_nothing_is_saved(db, bad, message):
    with pytest.raises(portal.PortalError, match=f"(?i){message}"):
        _new(db, **bad)
    assert read_table("SELECT COUNT(*) AS n FROM portal_requests", db_path=db)["n"][0] == 0


def test_unknown_subcategory_becomes_unclassified_and_email_is_optional(db):
    tid = _new(db, subcategory="Nonsense", requester_email="")
    t = portal.get_portal_ticket(tid, db)
    assert t["subcategory"] == "Unclassified" and t["requester_email"] is None


def test_form_input_is_stored_as_data_not_executed(db):
    nasty = "x'); DROP TABLE tickets; --"
    tid = _new(db, short_description=nasty, requester_name=nasty)
    assert portal.get_portal_ticket(tid, db)["short_description"] == nasty
    assert read_table("SELECT COUNT(*) AS n FROM tickets", db_path=db)["n"][0] > 600 * 0.9


def test_portal_tickets_flow_into_the_analytics(db):
    before = analytics.kpis(db_path=db)["total_tickets"]
    _new(db)
    assert analytics.kpis(db_path=db)["total_tickets"] == before + 1


# ------------------------------------------------------------ agent actions
def test_assign_moves_ticket_to_agents_team_and_in_progress(db):
    tid = _new(db)
    agent = _agent(db, "ServiceDesk-L1")
    portal.assign_ticket(tid, agent, NOW, db)
    t = portal.get_portal_ticket(tid, db)
    assert (t["status"], t["agent_name"], t["team_name"]) == ("In Progress", agent, "ServiceDesk-L1")
    assert list(portal.get_events(tid, db)["event_type"]) == ["submitted", "assigned"]


def test_assign_and_resolve_reject_bad_targets(db):
    tid = _new(db)
    with pytest.raises(portal.PortalError, match="Unknown agent"):
        portal.assign_ticket(tid, "Nobody Here", NOW, db)
    generated = read_table("SELECT ticket_id FROM tickets WHERE batch_id != ? LIMIT 1",
                           (portal.PORTAL_BATCH,), db)["ticket_id"][0]
    with pytest.raises(portal.PortalError, match="not a portal ticket"):
        portal.assign_ticket(generated, _agent(db), NOW, db)       # agents cannot touch CSV tickets here
    with pytest.raises(portal.PortalError, match="not a portal ticket"):
        portal.resolve_ticket(generated, _agent(db), "Restarted the service and checked.", "Restart", NOW, db)


def test_resolve_records_time_sla_and_notes(db):
    tid, agent = _new(db), _agent(db)
    result = portal.resolve_ticket(tid, agent, "Renewed the expired client certificate; VPN works again.",
                                   "Config Change", NOW + timedelta(hours=10), db)
    assert result == {"ticket_id": tid, "resolution_hours": 10.0, "sla_breached": True}   # P2 target is 8 h
    t = portal.get_portal_ticket(tid, db)
    assert (t["status"], t["resolved_at"], t["resolution_hours"], t["sla_breached"]) == \
           ("Resolved", "2026-11-20 20:00:00", 10.0, 1)
    assert (t["resolution_code"], t["agent_name"]) == ("Config Change", agent)
    assert "expired client certificate" in t["resolution_notes"]
    assert [e for e in portal.get_events(tid, db)["event_type"]] == ["submitted", "resolved"]


def test_resolve_within_sla_and_cannot_resolve_twice(db):
    tid, agent = _new(db), _agent(db)
    ok = portal.resolve_ticket(tid, agent, "Restarted the VPN concentrator and verified.", "Restart",
                               NOW + timedelta(hours=2), db)
    assert ok["sla_breached"] is False
    with pytest.raises(portal.PortalError, match="already Resolved"):
        portal.resolve_ticket(tid, agent, "Doing it again for no reason.", "Restart", NOW, db)
    with pytest.raises(portal.PortalError, match="already Resolved"):
        portal.assign_ticket(tid, agent, NOW, db)


def test_resolve_needs_real_notes(db):
    tid = _new(db)
    for notes in ("", "fixed", "   "):
        with pytest.raises(portal.PortalError):
            portal.resolve_ticket(tid, _agent(db), notes, "Restart", NOW, db)
    assert portal.get_portal_ticket(tid, db)["status"] == "Open"


def test_resolved_portal_ticket_joins_the_knowledge_base(db):
    tid = _new(db)
    portal.resolve_ticket(tid, _agent(db), "Reset the user's VPN profile and re-enrolled the device.",
                          "Config Change", NOW + timedelta(hours=1), db)
    assert tid in set(SimilarIncidentIndex(db).docs["ticket_id"])


# ------------------------------------------------------------ queue / clock
def test_queue_puts_urgent_open_tickets_first_and_resolved_last(db):
    low, high, done = _new(db, priority="P4"), _new(db, priority="P1"), _new(db, priority="P3")
    portal.resolve_ticket(done, _agent(db), "Cleared the stuck session and confirmed.", "Restart", NOW, db)
    queue = portal.portal_queue(db)
    assert list(queue["ticket_id"]) == [high, low, done]
    assert queue.loc[0, "due_at"] == "2026-11-20 14:00"             # P1 = 4 h


def test_refresh_clocks_flags_open_tickets_that_run_out_of_time(db):
    tid = _new(db, priority="P1")
    assert portal.refresh_open_ticket_clocks(NOW + timedelta(hours=1), db) == 1
    assert read_table("SELECT sla_breached AS b FROM tickets WHERE ticket_id = ?", (tid,), db)["b"][0] == 0
    portal.refresh_open_ticket_clocks(NOW + timedelta(hours=5), db)
    row = read_table("SELECT sla_breached AS b, ticket_age_days AS a FROM tickets WHERE ticket_id = ?",
                     (tid,), db).iloc[0]
    assert (row["b"], row["a"]) == (1, 0.21)


def test_sla_status_wording():
    opened = "2026-11-20 10:00:00"
    assert portal.sla_status(opened, 8, now=NOW + timedelta(hours=2, minutes=30)) == ("5h 30m left", False)
    assert portal.sla_status(opened, 8, now=NOW + timedelta(hours=9, minutes=5)) == ("breached by 1h 05m", True)
    assert portal.sla_status(opened, 8, "2026-11-20 12:00:00") == ("met with 6h 00m to spare", False)


# ------------------------------------------------------------------ tracking
def test_track_shows_only_user_safe_fields_for_portal_tickets(db):
    tid = _new(db)
    info = portal.track_ticket(tid.lower(), db)                     # case-insensitive
    assert info["status"] == "Open" and info["agent_name"] is None
    assert "requester_email" not in info and "requester_name" not in info
    portal.resolve_ticket(tid, _agent(db), "Renewed the client certificate and tested the login.", "Restart",
                          NOW + timedelta(hours=1), db)
    assert "client certificate" in portal.track_ticket(tid, db)["resolution_notes"]


def test_track_ignores_csv_tickets_and_unknown_numbers(db):
    generated = read_table("SELECT ticket_id FROM tickets LIMIT 1", db_path=db)["ticket_id"][0]
    assert portal.track_ticket(generated, db) is None
    assert portal.track_ticket("INC9999999", db) is None
    assert portal.track_ticket("", db) is None


# ------------------------------------------------------------ auto category
def test_category_is_guessed_from_similar_solved_tickets(db):
    index = SimilarIncidentIndex(db)
    category, sub, strength = portal.predict_category(index, VPN_TEXT)
    assert category == "VPN" and portal.CATEGORY_GUESS_THRESHOLD <= strength <= 1
    assert portal.predict_category(index, "zzqq blorf wibble") is None   # nothing similar -> no guess


class FakeIndex:
    """Stands in for SimilarIncidentIndex so the voting rules can be tested exactly."""

    def __init__(self, rows):
        self.rows = rows

    def find_similar(self, text, top_k=5):
        return pd.DataFrame(self.rows, columns=["category", "subcategory", "similarity"])


def test_weak_matches_do_not_get_a_guess_even_though_the_search_would_return_them():
    # 0.25 is above the 0.2 search cut-off but a coffee machine is not a virtual machine
    weak = FakeIndex([("Cloud Infrastructure", "VM down", 0.25), ("Cloud Infrastructure", "VM down", 0.22)])
    assert portal.predict_category(weak, "coffee machine broken") is None
    assert portal.predict_category(FakeIndex([]), "anything") is None


def test_category_vote_sums_similarity_and_reports_the_best_match_as_strength():
    index = FakeIndex([("VPN", "Cannot connect", 0.50), ("VPN", "Cannot connect", 0.45),
                       ("Network", "Slow network", 0.31), ("Network", "Slow network", 0.30)])
    assert portal.predict_category(index, "x") == ("VPN", "Cannot connect", 0.5)          # 0.95 beats 0.61
    # two moderate matches together outvote one strong one: the vote is a sum, not "best single hit"
    assert portal.predict_category(FakeIndex([("Network", "Wi-Fi issue", 0.40), ("VPN", "Cannot connect", 0.31),
                                              ("VPN", "Cannot connect", 0.30)]), "x")[:2] == ("VPN", "Cannot connect")


def test_matches_below_the_search_cutoff_do_not_vote():
    # without the cut-off, three 0.19 VPN hits (0.92 in total) would beat Network's 0.61
    index = FakeIndex([("Network", "Slow network", 0.31), ("Network", "Slow network", 0.30),
                       ("VPN", "Cannot connect", 0.35), ("VPN", "Cannot connect", 0.19),
                       ("VPN", "Cannot connect", 0.19), ("VPN", "Cannot connect", 0.19)])
    assert portal.predict_category(index, "x") == ("Network", "Slow network", 0.31)


# ------------------------------------------------------------------- safety
def test_ai_cannot_read_requester_data_or_the_audit_trail(db):
    _new(db)
    assert not {"portal_requests", "ticket_events"} & config.APPROVED_TABLES
    ok, reason, _ = validate_sql("SELECT requester_email FROM portal_requests")
    assert not ok and "Table not allowed" in reason
    with pytest.raises(pd.errors.DatabaseError, match="prohibited"):   # engine-level block, even with no static check
        execute_readonly("SELECT requester_email FROM portal_requests", db)


def test_schema_upgrade_is_idempotent_on_an_existing_database(db):
    init_db(db)
    init_db(db)
    conn = connect(db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert {"portal_requests", "ticket_events", "tickets"} <= names

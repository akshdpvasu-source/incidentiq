"""Phase 5 acceptance: each SQL query matches hand-calculated answers on a
20-ticket dataset, and the planted patterns A-D appear in the generated data.

Hand dataset (TeamA = Ann + Bob, TeamB = Cid):
 id  opened            day h  category team agent pri status   res_h tgt breached
 T01 2026-01-05 09:00  Mon 9  Network  A    Ann   P3  Closed   10    24  0
 T02 2026-01-05 09:30  Mon 9  Network  A    Ann   P3  Closed   30    24  1
 T03 2026-01-06 10:00  Tue 10 VPN      A    Bob   P2  Closed    4     8  0
 T04 2026-01-12 09:15  Mon 9  Email    B    Cid   P4  Closed   80    72  1
 T05 2026-01-14 14:00  Wed 14 VPN      A    Bob   P3  Closed   20    24  0
 T06 2026-02-02 09:00  Mon 9  VPN      A    Ann   P1  Closed    2     4  0
 T07 2026-02-03 11:00  Tue 11 VPN      A    Bob   P2  Closed   10     8  1
 T08 2026-02-04 10:00  Wed 10 VPN      B    Cid   P3  Closed   30    24  1
 T09 2026-02-09 09:45  Mon 9  Network  B    Cid   P3  Closed   12    24  0
 T10 2026-02-10 15:00  Tue 15 VPN      A    Ann   P4  Closed   50    72  0
 T11 2026-02-16 09:00  Mon 9  VPN      B    Cid   P3  Resolved 26    24  1
 T12 2026-02-20 16:00  Fri 16 Email    B    Cid   P4  Closed   10    72  0
 T13 2026-03-02 09:00  Mon 9  VPN      A    Bob   P2  Closed    6     8  0
 T14 2026-03-03 10:00  Tue 10 VPN      B    Cid   P3  Closed   40    24  1
 T15 2026-03-04 11:00  Wed 11 VPN      A    Ann   P3  Closed    8    24  0
 T16 2026-03-09 09:30  Mon 9  VPN      A    Bob   P4  Closed   30    72  0
 T17 2026-03-10 13:00  Tue 13 VPN      B    Cid   P2  Closed   12     8  1
 T18 2026-03-20 10:00  Fri 10 Network  A    Ann   P1  Open      -     4  1 (age 12 d)
 T19 2026-03-25 09:00  Wed 9  Email    B    Cid   P3  In Prog   -    24  1 (age 7 d)
 T20 2026-03-31 12:00  Tue 12 VPN      A    Bob   P3  Open      -    24  0 (age 0.5 d)
Services: Network = Core Switch, VPN = VPN Gateway, Email = Exchange.
"""
import pytest

from src import analytics
from src.load_db import connect, init_db

ROWS = """
T01 2026-01-05_09:00:00 Monday 9 1 1 1 P3 Closed 10 24 0 -
T02 2026-01-05_09:30:00 Monday 9 1 1 1 P3 Closed 30 24 1 -
T03 2026-01-06_10:00:00 Tuesday 10 2 1 2 P2 Closed 4 8 0 -
T04 2026-01-12_09:15:00 Monday 9 3 2 3 P4 Closed 80 72 1 -
T05 2026-01-14_14:00:00 Wednesday 14 2 1 2 P3 Closed 20 24 0 -
T06 2026-02-02_09:00:00 Monday 9 2 1 1 P1 Closed 2 4 0 -
T07 2026-02-03_11:00:00 Tuesday 11 2 1 2 P2 Closed 10 8 1 -
T08 2026-02-04_10:00:00 Wednesday 10 2 2 3 P3 Closed 30 24 1 -
T09 2026-02-09_09:45:00 Monday 9 1 2 3 P3 Closed 12 24 0 -
T10 2026-02-10_15:00:00 Tuesday 15 2 1 1 P4 Closed 50 72 0 -
T11 2026-02-16_09:00:00 Monday 9 2 2 3 P3 Resolved 26 24 1 -
T12 2026-02-20_16:00:00 Friday 16 3 2 3 P4 Closed 10 72 0 -
T13 2026-03-02_09:00:00 Monday 9 2 1 2 P2 Closed 6 8 0 -
T14 2026-03-03_10:00:00 Tuesday 10 2 2 3 P3 Closed 40 24 1 -
T15 2026-03-04_11:00:00 Wednesday 11 2 1 1 P3 Closed 8 24 0 -
T16 2026-03-09_09:30:00 Monday 9 2 1 2 P4 Closed 30 72 0 -
T17 2026-03-10_13:00:00 Tuesday 13 2 2 3 P2 Closed 12 8 1 -
T18 2026-03-20_10:00:00 Friday 10 1 1 1 P1 Open - 4 1 12.0
T19 2026-03-25_09:00:00 Wednesday 9 3 2 3 P3 In_Progress - 24 1 7.0
T20 2026-03-31_12:00:00 Tuesday 12 2 1 2 P3 Open - 24 0 0.5
"""


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("hand") / "hand.db"
    init_db(path)
    conn = connect(path)
    with conn:
        conn.execute("INSERT INTO batches (batch_id, status) VALUES ('B-HAND', 'loaded')")
        conn.executemany("INSERT INTO teams (team_id, team_name, region) VALUES (?, ?, ?)",
                         [(1, "TeamA", "NA"), (2, "TeamB", "APAC")])
        conn.executemany("INSERT INTO agents (agent_id, agent_name, team_id) VALUES (?, ?, ?)",
                         [(1, "Ann", 1), (2, "Bob", 1), (3, "Cid", 2)])
        conn.executemany("INSERT INTO categories VALUES (?, ?, ?, ?)",
                         [(1, "Network", "Connectivity loss", "Core Switch"),
                          (2, "VPN", "Cannot connect", "VPN Gateway"),
                          (3, "Email", "Mailbox full", "Exchange")])
        for line in ROWS.strip().splitlines():
            tid, opened, day, hour, cat, team, agent, pri, status, res, tgt, br, age = line.split()
            res_h = None if res == "-" else float(res)
            conn.execute(
                "INSERT INTO tickets (ticket_id, batch_id, category_id, team_id, agent_id, priority, "
                "status, opened_at, resolution_hours, sla_target_hours, sla_breached, ticket_age_days, "
                "opened_hour, opened_weekday, opened_month, reopen_count) "
                "VALUES (?, 'B-HAND', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (tid, int(cat), int(team), int(agent), pri, status.replace("_", " "),
                 opened.replace("_", " "), res_h, float(tgt), int(br),
                 res_h / 24 if res_h is not None else float(age), int(hour), day, opened[:7]))
    conn.close()
    return path


def q(db, name, **filters):
    return analytics.run_query(name, filters, db_path=db)


def test_q1_top_categories(db):
    df = q(db, "q1_top_categories")
    assert df["subcategory"].tolist() == ["Cannot connect", "Connectivity loss", "Mailbox full"]
    assert df["ticket_count"].tolist() == [13, 4, 3]
    assert df["share_pct"].tolist() == [65.0, 20.0, 15.0]
    assert df["avg_resolution_hours"].tolist() == [19.83, 17.33, 45.0]


def test_q2_breach_by_team_and_priority(db):
    df = q(db, "q2_sla_breach_by_team")
    got = {(r.team_name, r.priority): (r.tickets, r.breached, r.breach_rate_pct) for r in df.itertuples()}
    assert got == {("TeamA", "P1"): (2, 1, 50.0), ("TeamA", "P2"): (3, 1, 33.33),
                   ("TeamA", "P3"): (5, 1, 20.0), ("TeamA", "P4"): (2, 0, 0.0),
                   ("TeamB", "P2"): (1, 1, 100.0), ("TeamB", "P3"): (5, 4, 80.0),
                   ("TeamB", "P4"): (2, 1, 50.0)}


def test_q3_mttr_by_category_month(db):
    df = q(db, "q3_mttr_by_category_month")
    got = {(r.category, r.month): (r.resolved_tickets, r.mttr_hours) for r in df.itertuples()}
    assert got == {("Network", "2026-01"): (2, 20.0), ("Network", "2026-02"): (1, 12.0),
                   ("VPN", "2026-01"): (2, 12.0), ("VPN", "2026-02"): (5, 23.6),
                   ("VPN", "2026-03"): (5, 19.2), ("Email", "2026-01"): (1, 80.0),
                   ("Email", "2026-02"): (1, 10.0)}


def test_q4_peaks(db):
    df = q(db, "q4_peaks_hour_weekday")
    assert df["tickets"].sum() == 20
    top = df.sort_values("tickets", ascending=False).iloc[0]
    assert (top["weekday"], top["hour"], top["tickets"]) == ("Monday", 9, 8)
    assert df[(df.weekday == "Tuesday") & (df.hour == 10)]["tickets"].item() == 2


def test_q5_repeat_offenders(db):
    df = q(db, "q5_repeat_offender_services", threshold=2.0, min_tickets=3)
    assert len(df) == 6  # 3 services x (Feb, Mar) have a previous month
    flagged = df[df.flagged == 1]
    assert flagged[["affected_service", "month", "tickets", "prev_tickets", "growth_ratio"]].values.tolist() \
        == [["VPN Gateway", "2026-02", 5, 2, 2.5]]
    core_feb = df[(df.affected_service == "Core Switch") & (df.month == "2026-02")].iloc[0]
    assert (core_feb.tickets, core_feb.prev_tickets, core_feb.growth_ratio) == (1, 2, 0.5)


def test_q6_month_over_month(db):
    df = q(db, "q6_mom_trend")
    assert df["month"].tolist() == ["2026-01", "2026-02", "2026-03"]
    assert df["tickets"].tolist() == [5, 7, 8]
    assert df["breached"].tolist() == [2, 3, 4]
    assert df["breach_rate_pct"].tolist() == [40.0, 42.86, 50.0]
    assert df["volume_change"].tolist()[1:] == [2, 1]
    assert df["volume_change_pct"].tolist()[1:] == [40.0, 14.29]
    assert df["breach_rate_change"].tolist()[1:] == [2.86, 7.14]


def test_q7_oldest_open(db):
    df = q(db, "q7_oldest_open_at_risk")
    assert df["ticket_id"].tolist() == ["T18", "T19", "T20"]
    assert df["age_rank_in_priority"].tolist() == [1, 1, 2]
    assert df["sla_used_pct"].tolist() == [7200.0, 700.0, 50.0]
    assert q(db, "q7_oldest_open_at_risk", top_n=1)["ticket_id"].tolist() == ["T18", "T19"]


def test_q8_workload(db):
    df = q(db, "q8_workload").set_index("agent_name")
    assert df.loc["Ann", ["tickets_handled", "resolved", "avg_resolution_hours", "breach_rate_pct"]].tolist() \
        == [6, 5, 20.0, 33.33]
    assert df.loc["Bob", ["tickets_handled", "resolved", "avg_resolution_hours", "breach_rate_pct"]].tolist() \
        == [6, 5, 14.0, 16.67]
    assert df.loc["Cid", ["tickets_handled", "resolved", "avg_resolution_hours", "breach_rate_pct"]].tolist() \
        == [8, 7, 30.0, 75.0]
    assert df.loc[["Ann", "Bob"], "workload_rank_in_team"].tolist() == [1, 1]  # tie
    assert df.loc[["Bob", "Ann"], "speed_rank_in_team"].tolist() == [1, 2]


def test_kpis_and_filters(db):
    k = analytics.kpis(db_path=db)
    assert (k["total_tickets"], k["open_tickets"], k["sla_breach_pct"], k["mttr_hours"]) == (20, 3, 45.0, 22.35)
    assert q(db, "q1_top_categories", team="TeamB")["ticket_count"].sum() == 8
    feb = q(db, "q6_mom_trend", start_date="2026-02-01", end_date="2026-02-28")
    assert feb["month"].tolist() == ["2026-02"] and feb["tickets"].tolist() == [7]
    assert q(db, "q2_sla_breach_by_team", priority="P1", category="All")["tickets"].sum() == 2


def test_read_only_connection_blocks_writes(db):
    conn = analytics.read_only_connection(db)
    with pytest.raises(Exception):
        conn.execute("DELETE FROM tickets")
    conn.close()


# ---------------------------------------------------------------- real data
@pytest.fixture(scope="module")
def real_db(tmp_path_factory):
    """Generate the full 20k dataset, clean it and load it into a temp DB."""
    from src import config
    from src.generate_data import generate_tickets
    from src.load_db import register_batch, load_batch
    from src.transform import clean

    path = tmp_path_factory.mktemp("real") / "real.db"
    raw = generate_tickets(config.N_TICKETS, config.SEED, "ServiceNow-Export", 100000)
    batch = register_batch("generated", "file", len(raw), db_path=path)
    cleaned, rejected, _ = clean(raw, batch)
    load_batch(cleaned, rejected, batch, db_path=path)
    return path


def test_pattern_a_vpn_gateway_spike(real_db):
    from src import config
    df = analytics.run_query("q5_repeat_offender_services", db_path=real_db)
    top = df.iloc[0]
    assert (top.affected_service, top.month, top.flagged) == ("VPN Gateway", config.VPN_SPIKE_MONTH, 1)
    assert top.growth_ratio >= 2.5


def test_pattern_b_weak_team(real_db):
    df = analytics.run_query("q2_sla_breach_by_team", db_path=real_db)
    team = df.groupby("team_name")[["tickets", "breached"]].sum()
    rate = team["breached"] / team["tickets"]
    others = team.drop("Infra-Team-B")
    ratio = rate["Infra-Team-B"] / (others["breached"].sum() / others["tickets"].sum())
    assert rate.idxmax() == "Infra-Team-B"
    assert 1.6 <= ratio <= 2.5


def test_pattern_c_monday_morning_and_release_peaks(real_db):
    df = analytics.run_query("q4_peaks_hour_weekday", db_path=real_db)
    top = df.sort_values("tickets", ascending=False).iloc[0]
    assert top["weekday"] == "Monday" and top["hour"] in (9, 10)
    evening = df[df.hour.between(18, 22)].groupby("weekday")["tickets"].sum()
    weekday_evenings = evening.drop(["Saturday", "Sunday", "Friday"])
    assert evening["Friday"] > 1.5 * weekday_evenings.mean()  # release evenings


def test_pattern_d_password_reset(real_db):
    df = analytics.run_query("q1_top_categories", db_path=real_db)
    assert df.iloc[0]["subcategory"] == "Password reset"
    assert df["avg_resolution_hours"].idxmin() == df.index[0]


def test_real_data_breach_rate_and_reconciliation(real_db):
    k = analytics.kpis(db_path=real_db)
    assert k["total_tickets"] == 20000
    assert 12 <= k["sla_breach_pct"] <= 18

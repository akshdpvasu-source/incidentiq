"""Phase 6b acceptance: the SQL guard allows safe reads and blocks every attack."""
import json
from pathlib import Path

import pytest

from src import config
from src.ai.sql_guard import execute_readonly, run_safe_query, validate_sql
from src.ai.text_to_sql import answer_question, extract_sql, ungrounded_numbers
from src.load_db import connect, init_db

ATTACKS = [
    ("DROP TABLE tickets", "Only SELECT"),
    ("DELETE FROM tickets", "Only SELECT"),
    ("UPDATE tickets SET status = 'Closed'", "Only SELECT"),
    ("INSERT INTO teams (team_name) VALUES ('x')", "Only SELECT"),
    ("PRAGMA table_info(tickets)", "Only SELECT"),
    ("ATTACH DATABASE 'evil.db' AS evil", "Only SELECT"),
    ("SELECT 1; DELETE FROM tickets", "Multiple statements"),
    ("sElEcT * FrOm tickets; dRoP tAbLe tickets", "Multiple statements"),
    ("SELECT * FROM tickets WHERE status = 'Open' ; DROP TABLE teams", "Multiple statements"),
    ("SELECT * FROM tickets -- AND priority = 'P1'", "comments"),
    ("SELECT * FROM tickets /* hidden */ WHERE 1 = 1", "comments"),
    ("SELECT * FROM tickets WHERE 1 = 1 --", "comments"),
    ("WITH x AS (DELETE FROM tickets RETURNING *) SELECT * FROM x", "Forbidden keyword: DELETE"),
    ("SELECT * FROM tickets WHERE ticket_id IN (SELECT REPLACE(ticket_id, 'a', 'b') FROM tickets)",
     "Forbidden keyword: REPLACE"),
    ("SELECT * FROM ai_query_log", "Table not allowed"),
    ("SELECT * FROM rejected_records", "Table not allowed"),
    ("SELECT name, sql FROM sqlite_master", "Table not allowed"),
    ('SELECT name FROM "sqlite_master"', "Table not allowed"),
    ("SELECT * FROM tickets, rejected_records", "Table not allowed"),
    ("SELECT * FROM tickets t, secret_table s", "Table not allowed: secret_table"),
    ("SELECT * FROM unknown_table", "Table not allowed"),
    ("SELECT * FROM v_tickets", "Table not allowed"),
    ("SELECT * FROM pragma_table_info('tickets')", "Table not allowed"),
    ("SELECT load_extension('/tmp/evil.so')", "Forbidden function"),
    ("SELECT * FROM tickets WHERE status = 'Open", "Unbalanced quotes"),
    ("", "Empty"),
]


@pytest.mark.parametrize("sql,reason", ATTACKS)
def test_attacks_are_blocked(sql, reason):
    ok, why, safe = validate_sql(sql)
    assert not ok and safe is None
    assert reason.lower() in why.lower()


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM tickets",
    "select priority, count(*) from tickets group by priority",
    "WITH x AS (SELECT team_id, COUNT(*) AS n FROM tickets GROUP BY team_id) "
    "SELECT tm.team_name, x.n FROM x JOIN teams tm ON tm.team_id = x.team_id",
    "SELECT t.ticket_id FROM tickets t JOIN resolutions r ON r.ticket_id = t.ticket_id",
    "SELECT * FROM tickets WHERE description LIKE '%delete%' OR short_description = 'drop; --'",
    "SELECT * FROM (SELECT ticket_id FROM tickets) sub",
    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3) SELECT n FROM r",
])
def test_safe_queries_pass(sql):
    ok, why, safe = validate_sql(sql)
    assert ok, why
    assert safe.rstrip().upper().endswith(f"LIMIT {config.MAX_ROWS}")


def test_limit_rules():
    assert validate_sql("SELECT * FROM tickets LIMIT 10;")[2] == "SELECT * FROM tickets LIMIT 10"
    assert validate_sql("SELECT * FROM tickets LIMIT 5000")[2] == "SELECT * FROM tickets LIMIT 200"
    assert validate_sql("SELECT * FROM tickets LIMIT 5 OFFSET 10")[2].endswith("LIMIT 5 OFFSET 10")


def test_benchmark_gold_sql_is_valid_or_expected_blocked():
    items = json.loads((Path(__file__).parent / "benchmark_questions.json").read_text())
    assert len(items) == 20
    for item in items:
        ok, why, _ = validate_sql(item["gold_sql"])
        assert ok == (item["check"] != "refused"), (item["id"], why)


# ----------------------------------------------------------- execution layer
@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "guard.db"
    init_db(path)
    conn = connect(path)
    with conn:
        conn.execute("INSERT INTO teams (team_name, region) VALUES ('TeamA', 'NA')")
        conn.executemany("INSERT INTO tickets (ticket_id, team_id, priority, status) VALUES (?, 1, ?, ?)",
                         [("T1", "P1", "Open"), ("T2", "P3", "Closed")])
        conn.execute("INSERT INTO ai_query_log (question) VALUES ('secret')")
    conn.close()
    return path


def test_run_safe_query_returns_rows(db):
    result = run_safe_query("SELECT priority FROM tickets ORDER BY priority", db)
    assert result["status"] == "Valid"
    assert result["df"]["priority"].tolist() == ["P1", "P3"]


def test_run_safe_query_blocks_and_reports_errors(db):
    assert run_safe_query("DROP TABLE tickets", db)["status"] == "Blocked"
    failed = run_safe_query("SELECT no_such_column FROM tickets", db)
    assert failed["status"] == "Failed" and "no_such_column" in failed["reason"]


def test_engine_level_protection_even_without_static_checks(db):
    for sql in ("SELECT * FROM ai_query_log", "SELECT * FROM sqlite_master", "DELETE FROM tickets"):
        with pytest.raises(Exception):
            execute_readonly(sql, db)
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 2  # nothing deleted
    conn.close()


# ------------------------------------------------- text-to-SQL with a fake LLM
def _fake(*answers):
    replies = iter(answers)
    return lambda question, error=None, previous_sql=None: next(replies)


def test_text_to_sql_valid_and_logged(db):
    r = answer_question("How many tickets?", db, explain_answer=False,
                        generate_fn=_fake("SELECT COUNT(*) AS n FROM tickets"))
    assert r["status"] == "Valid" and r["dataframe"]["n"].item() == 2
    conn = connect(db)
    assert conn.execute("SELECT validation_status, row_count FROM ai_query_log ORDER BY log_id DESC "
                        "LIMIT 1").fetchone() == ("Valid", 1)
    conn.close()


def test_text_to_sql_blocks_malicious_sql_and_logs_it(db):
    r = answer_question("Show tickets", db, explain_answer=False,
                        generate_fn=_fake("SELECT 1; DELETE FROM tickets"))
    assert r["status"] == "Blocked"
    r2 = answer_question("delete all tickets", db, generate_fn=_fake("should not be called"))
    assert r2["status"] == "Blocked"
    conn = connect(db)
    statuses = [s for (s,) in conn.execute("SELECT validation_status FROM ai_query_log WHERE log_id > 1")]
    assert statuses == ["Blocked", "Blocked"]
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 2
    conn.close()


def test_text_to_sql_retries_once_after_error(db):
    r = answer_question("Priorities?", db, explain_answer=False,
                        generate_fn=_fake("SELECT prio FROM tickets", "SELECT priority FROM tickets"))
    assert r["status"] == "Valid" and len(r["dataframe"]) == 2


def test_cannot_answer_and_helpers(db):
    r = answer_question("CEO salary?", db, generate_fn=_fake("CANNOT_ANSWER"))
    assert r["status"] == "Failed" and r["sql"] == "CANNOT_ANSWER"
    assert extract_sql("Here you go:\n```sql\nSELECT 1\n```") == "SELECT 1"
    assert extract_sql("Sure! The query is:\nSELECT 2") == "SELECT 2"
    import pandas as pd
    df = pd.DataFrame({"team": ["Infra-Team-B"], "rate": [28.13]})
    assert ungrounded_numbers("Infra-Team-B has a 28.13% breach rate.", df) == []
    assert ungrounded_numbers("Infra-Team-B has a 45.5% breach rate.", df) == ["45.5"]

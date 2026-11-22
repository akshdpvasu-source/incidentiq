"""Phase 4 - SQLite helpers and the batch loader.

connect / init_db / register_batch are also used by the ingestion step,
because every ingestion run must create a row in the `batches` table.
load_batch() writes one cleaned batch inside a single transaction: if
anything fails it rolls back and marks the batch 'failed'.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from src import config
from src.catalog import TEAMS

TICKET_COLS = ["ticket_id", "batch_id", "category_id", "team_id", "agent_id", "priority", "status",
               "opened_at", "resolved_at", "resolution_hours", "sla_target_hours", "sla_breached",
               "ticket_age_days", "opened_hour", "opened_weekday", "opened_month", "reopen_count",
               "short_description", "description"]


def connect(db_path=None):
    """Open a normal read/write connection with foreign keys switched on."""
    conn = sqlite3.connect(Path(db_path or config.DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None):
    """Create all tables/views if they do not exist yet (idempotent)."""
    with connect(db_path) as conn:
        conn.executescript(config.SCHEMA_PATH.read_text())
    conn.close()


def register_batch(source_name, source_type, input_rows, file_size=None, db_path=None):
    """Record a new ingestion run and return its id, e.g. B-20260115-003."""
    init_db(db_path)
    conn = connect(db_path)
    with conn:
        prefix = f"B-{datetime.now():%Y%m%d}-"
        last = conn.execute("SELECT MAX(batch_id) FROM batches WHERE batch_id LIKE ?",
                            (prefix + "%",)).fetchone()[0]
        batch_id = f"{prefix}{int(last[-3:]) + 1 if last else 1:03d}"
        conn.execute("INSERT INTO batches (batch_id, source_name, source_type, ingested_at, "
                     "input_rows, status, file_size) VALUES (?, ?, ?, ?, ?, 'ingested', ?)",
                     (batch_id, source_name, source_type, datetime.now().isoformat(timespec="seconds"),
                      int(input_rows), file_size))
    conn.close()
    return batch_id


def _values(df, cols):
    """DataFrame columns -> list of tuples with plain Python values (NaN -> None)."""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    columns = [[None if pd.isna(v) else v for v in out[c].tolist()] for c in cols]
    return list(zip(*columns))


def _upsert_dimensions(conn, df):
    """Insert any new teams, agents and categories; return lookup dicts name -> id."""
    roster_path = config.REFERENCE_DIR / "agents.csv"
    roster = pd.read_csv(roster_path) if roster_path.exists() else pd.DataFrame(
        columns=["agent_name", "team_name", "experience_level"])
    teams = set(df["assignment_team"].dropna()) | set(roster["team_name"])
    conn.executemany("INSERT OR IGNORE INTO teams (team_name, region) VALUES (?, ?)",
                     [(t, TEAMS.get(t, "Unknown")) for t in sorted(teams)])
    team_ids = dict(conn.execute("SELECT team_name, team_id FROM teams"))

    level = dict(zip(roster["agent_name"], roster["experience_level"]))
    agents = df.dropna(subset=["assigned_agent"]).drop_duplicates("assigned_agent")
    conn.executemany("INSERT OR IGNORE INTO agents (agent_name, team_id, experience_level) "
                     "VALUES (?, ?, ?)",
                     [(a, team_ids.get(t), level.get(a, "Unknown"))
                      for a, t in zip(agents["assigned_agent"], agents["assignment_team"])])
    agent_ids = dict(conn.execute("SELECT agent_name, agent_id FROM agents"))

    cats = df[["category", "subcategory", "affected_service"]].drop_duplicates()
    conn.executemany("INSERT OR IGNORE INTO categories (category, subcategory, affected_service) "
                     "VALUES (?, ?, ?)", _values(cats, list(cats.columns)))
    cat_ids = {(c, s, a): i for i, c, s, a in conn.execute(
        "SELECT category_id, category, subcategory, affected_service FROM categories")}
    return team_ids, agent_ids, cat_ids


def load_batch(cleaned, rejected, batch_id, db_path=None):
    """Load one cleaned batch. Existing ticket_ids are updated (latest extract wins)."""
    init_db(db_path)
    conn = connect(db_path)
    status = conn.execute("SELECT status FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
    if status is None:
        raise ValueError(f"Unknown batch {batch_id}; ingest the data first.")
    if status[0] == "loaded":
        raise ValueError(f"Batch {batch_id} is already loaded. Ingest again to create a new batch.")
    try:
        with conn:  # one transaction: commit on success, roll back on any error
            team_ids, agent_ids, cat_ids = _upsert_dimensions(conn, cleaned)
            t = cleaned.copy()
            t["batch_id"] = batch_id
            t["team_id"] = t["assignment_team"].map(team_ids)
            t["agent_id"] = t["assigned_agent"].map(agent_ids)
            t["category_id"] = [cat_ids.get(k) for k in zip(t["category"], t["subcategory"],
                                                            t["affected_service"])]
            updates = ", ".join(f"{c} = excluded.{c}" for c in TICKET_COLS[1:])
            conn.executemany(f"INSERT INTO tickets ({', '.join(TICKET_COLS)}) VALUES "
                             f"({', '.join('?' * len(TICKET_COLS))}) "
                             f"ON CONFLICT(ticket_id) DO UPDATE SET {updates}", _values(t, TICKET_COLS))
            conn.executemany("DELETE FROM resolutions WHERE ticket_id = ?", [(i,) for i in t["ticket_id"]])
            notes = t[t["resolution_notes"].notna() | t["resolution_code"].notna()]
            conn.executemany("INSERT INTO resolutions (ticket_id, resolution_notes, resolution_code) "
                             "VALUES (?, ?, ?)",
                             _values(notes, ["ticket_id", "resolution_notes", "resolution_code"]))
            conn.executemany("INSERT INTO rejected_records (batch_id, raw_ticket_id, reason_code, "
                             "raw_payload) VALUES (?, ?, ?, ?)",
                             [(batch_id, *row) for row in
                              _values(rejected, ["raw_ticket_id", "reason_code", "raw_payload"])])
            conn.execute("UPDATE batches SET loaded_rows = ?, rejected_rows = ?, status = 'loaded' "
                         "WHERE batch_id = ?", (len(cleaned), len(rejected), batch_id))
    except Exception:
        with conn:
            conn.execute("UPDATE batches SET status = 'failed' WHERE batch_id = ?", (batch_id,))
        conn.close()
        raise
    conn.close()
    return {"batch_id": batch_id, "loaded_rows": len(cleaned), "rejected_rows": len(rejected)}


def read_table(sql, params=(), db_path=None):
    """Small helper for the app: run a trusted query and return a DataFrame."""
    path = Path(db_path or config.DB_PATH)
    if not path.exists():
        return pd.DataFrame()
    conn = connect(path)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()

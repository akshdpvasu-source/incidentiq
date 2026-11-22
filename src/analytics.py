"""Phase 5 - run the 8 SQL analytics queries stored in sql/.

    run_query("q2_sla_breach_by_team", {"team": "Infra-Team-B"}) -> DataFrame

Filters (all optional): start_date, end_date (inclusive, YYYY-MM-DD),
team, priority, category. Extra knobs: threshold / min_tickets (Q5), top_n (Q7).
Queries always run on a read-only connection.

Run:  python src/analytics.py      (prints the first rows of every query)
"""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src import config

QUERIES = {
    "q1_top_categories": "Top recurring issue categories",
    "q2_sla_breach_by_team": "SLA breach rate by team and priority",
    "q3_mttr_by_category_month": "MTTR by category and month",
    "q4_peaks_hour_weekday": "Incident peaks by hour and weekday",
    "q5_repeat_offender_services": "Repeat-offender services (month vs previous month)",
    "q6_mom_trend": "Month-over-month volume and breach trend",
    "q7_oldest_open_at_risk": "Oldest open tickets at highest breach risk",
    "q8_workload": "Agent workload vs resolution speed",
}
DEFAULTS = {"start_date": None, "end_date": None, "team": None, "priority": None, "category": None,
            "threshold": 2.5, "min_tickets": 20, "top_n": 10}


def read_only_connection(db_path=None):
    """Open SQLite in read-only mode (writes fail at the database level)."""
    path = Path(db_path or config.DB_PATH).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Database not found at {path}. Run the pipeline first.")
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def load_sql(name):
    return (config.SQL_DIR / f"{name}.sql").read_text()


def _params(filters):
    """Merge user filters with defaults; make end_date inclusive; drop 'All' values."""
    params = dict(DEFAULTS)
    for key, value in (filters or {}).items():
        params[key] = None if value in ("", "All") else value
    for key in ("start_date", "end_date"):
        if isinstance(params[key], str) and params[key]:
            params[key] = date.fromisoformat(params[key][:10])
    if params["end_date"]:
        params["end_date"] = params["end_date"] + timedelta(days=1)  # "< next day" = inclusive
    for key in ("start_date", "end_date"):
        if params[key]:
            params[key] = params[key].isoformat()
    return params


def run_query(name, filters=None, db_path=None):
    """Run sql/<name>.sql with the given filters and return a DataFrame."""
    if name not in QUERIES and name != "kpi_summary":
        raise KeyError(f"Unknown query {name}. Choose from {list(QUERIES)}")
    conn = read_only_connection(db_path)
    try:
        return pd.read_sql_query(load_sql(name), conn, params=_params(filters))
    finally:
        conn.close()


def kpis(filters=None, db_path=None):
    """Total tickets, open tickets, SLA breach % and MTTR as a dict."""
    return run_query("kpi_summary", filters, db_path).to_dict("records")[0]


if __name__ == "__main__":
    pd.set_option("display.width", 160)
    print("KPIs:", kpis())
    for query, title in QUERIES.items():
        print(f"\n=== {query}: {title}")
        print(run_query(query).head(8).to_string(index=False))

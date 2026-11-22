"""Phase 6f (optional) - one-page weekly executive report.

KPIs are computed with SQL for the last 7 days of data vs the 7 days before;
the LLM only turns those verified numbers into prose. Without an LLM a
plain template is used. Saved to data/reports/weekly_report_<date>.md.

Run:  python src/ai/report.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from src import config
from src.ai import llm_client
from src.ai.text_to_sql import ungrounded_numbers
from src.load_db import read_table

WINDOW = """SELECT COUNT(*) AS tickets,
  ROUND(100.0 * SUM(sla_breached) / NULLIF(COUNT(*), 0), 2) AS breach_rate_pct,
  ROUND(AVG(resolution_hours), 2) AS mttr_hours,
  SUM(CASE WHEN priority = 'P1' THEN 1 ELSE 0 END) AS p1_tickets
FROM v_tickets WHERE opened_at >= datetime(:end, '-{start} days') AND opened_at < datetime(:end, '-{stop} days')"""
TOP = """SELECT {col} AS name, COUNT(*) AS tickets FROM v_tickets
WHERE opened_at >= datetime(:end, '-7 days') GROUP BY {col} ORDER BY tickets DESC LIMIT 3"""
SYSTEM = ("Write a one-page weekly IT incident report for executives in Markdown with sections: "
          "Summary, Key numbers, Watch list, Recommended focus. Use ONLY the numbers in the JSON you "
          "are given - do not calculate or invent any other numbers. Call reasons 'possible factors'.")


def build_kpis(db_path=None):
    """All numbers for the report, straight from SQL."""
    end = read_table("SELECT MAX(opened_at) AS m FROM tickets", db_path=db_path)["m"].iloc[0]
    params = {"end": end}
    this_week = read_table(WINDOW.format(start=7, stop=0), params, db_path).to_dict("records")[0]
    last_week = read_table(WINDOW.format(start=14, stop=7), params, db_path).to_dict("records")[0]
    backlog = read_table("SELECT COUNT(*) AS n FROM tickets WHERE status IN ('Open','In Progress')",
                         db_path=db_path)["n"].iloc[0]
    return {"week_ending": end[:10], "this_week": this_week, "previous_week": last_week,
            "open_backlog": int(backlog),
            "top_categories": read_table(TOP.format(col="category"), params, db_path).to_dict("records"),
            "top_services": read_table(TOP.format(col="affected_service"), params, db_path).to_dict("records")}


def template_report(k):
    tw, pw = k["this_week"], k["previous_week"]
    return (f"# Weekly incident report - week ending {k['week_ending']}\n\n"
            f"## Key numbers\n| Metric | This week | Previous week |\n|---|---|---|\n"
            f"| Tickets | {tw['tickets']} | {pw['tickets']} |\n"
            f"| SLA breach % | {tw['breach_rate_pct']} | {pw['breach_rate_pct']} |\n"
            f"| MTTR (hours) | {tw['mttr_hours']} | {pw['mttr_hours']} |\n"
            f"| P1 tickets | {tw['p1_tickets']} | {pw['p1_tickets']} |\n\n"
            f"Open backlog: {k['open_backlog']}\n\n## Watch list\n"
            + "".join(f"- {s['name']}: {s['tickets']} tickets\n" for s in k["top_services"]))


def write_report(db_path=None):
    """Build KPIs, write the report (LLM if available) and save it. Returns (markdown, path, provider)."""
    kpis = build_kpis(db_path)
    try:
        text = llm_client.ask_llm(SYSTEM, json.dumps(kpis, default=str, indent=2), max_tokens=900)
        provider = llm_client.LAST_PROVIDER
        unverified = ungrounded_numbers(text, pd.DataFrame(), json.dumps(kpis, default=str))
        if unverified:
            text += ("\n\n> **Check:** these numbers are not in the SQL results (the model computed or "
                     f"invented them): {', '.join(unverified)}")
    except llm_client.LLMUnavailableError:
        text, provider = template_report(kpis), "template (no LLM)"
    text += f"\n\n---\n_Numbers computed by SQL; prose by {provider}. Generated {datetime.now():%Y-%m-%d %H:%M}._\n"
    path = config.REPORTS_DIR / f"weekly_report_{kpis['week_ending']}.md"
    path.write_text(text)
    return text, path, provider


if __name__ == "__main__":
    markdown, out, _ = write_report()
    print(markdown)
    print(f"Saved to {out}")

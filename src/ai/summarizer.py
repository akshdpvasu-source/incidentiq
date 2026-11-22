"""Phase 6d - 3-part ticket summary: Problem / Actions taken / Current status.

The LLM may only use facts from the ticket. Output is capped at 80 words.
If no LLM is reachable, a plain extractive summary is returned and labelled.
"""
import pandas as pd

from src import config
from src.ai import llm_client
from src.load_db import read_table

MAX_WORDS = 80
SYSTEM = ("Summarize the IT incident ticket in exactly three labelled lines:\n"
          "Problem: ...\nActions taken: ...\nCurrent status: ...\n"
          f"Maximum {MAX_WORDS} words in total. Use ONLY facts written in the ticket. "
          "If no actions are recorded write 'None recorded'. Do not invent names, numbers, causes or dates.")


def get_ticket(ticket_id, db_path=None):
    """One ticket with its category, team and resolution as a dict (or None)."""
    df = read_table(
        "SELECT t.ticket_id, t.priority, t.status, t.opened_at, t.resolved_at, t.resolution_hours, "
        "t.sla_breached, t.short_description, t.description, c.category, c.subcategory, "
        "c.affected_service, tm.team_name, r.resolution_notes, r.resolution_code "
        "FROM tickets t LEFT JOIN categories c ON c.category_id = t.category_id "
        "LEFT JOIN teams tm ON tm.team_id = t.team_id "
        "LEFT JOIN resolutions r ON r.ticket_id = t.ticket_id WHERE t.ticket_id = ?",
        (ticket_id,), db_path)
    return None if df.empty else {k: (None if pd.isna(v) else v) for k, v in df.to_dict("records")[0].items()}


def _ticket_text(ticket):
    return "\n".join(f"{k}: {ticket.get(k)}" for k in (
        "ticket_id", "priority", "status", "opened_at", "resolved_at", "affected_service",
        "short_description", "description", "resolution_notes") if ticket.get(k))


def _cap_words(text, limit=MAX_WORDS):
    words = text.split(" ")
    return text if len(text.split()) <= limit else " ".join(words[:limit]).rstrip(",;") + " ..."


def fallback_summary(ticket):
    """No-LLM summary built directly from the ticket fields."""
    status = ticket.get("status") or "Unknown"
    if ticket.get("resolved_at"):
        status += f" (resolved {ticket['resolved_at']})"
    return _cap_words(f"Problem: {ticket.get('short_description') or ticket.get('description')}\n"
                      f"Actions taken: {ticket.get('resolution_notes') or 'None recorded'}\n"
                      f"Current status: {status}")


def summarize_ticket(ticket):
    """Return {"summary", "provider", "word_count"} for a ticket dict."""
    try:
        summary = _cap_words(llm_client.ask_llm(SYSTEM, _ticket_text(ticket), max_tokens=250))
        provider = llm_client.LAST_PROVIDER
    except llm_client.LLMUnavailableError:
        summary, provider = fallback_summary(ticket), "offline extractive fallback (no LLM)"
    return {"summary": summary, "provider": provider, "word_count": len(summary.split())}


if __name__ == "__main__":
    import sys
    tid = sys.argv[1] if len(sys.argv) > 1 else "INC0100005"
    t = get_ticket(tid, config.DB_PATH)
    print(summarize_ticket(t) if t else f"{tid} not found")

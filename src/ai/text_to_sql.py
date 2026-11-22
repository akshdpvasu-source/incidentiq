"""Phase 6c - natural-language question -> safe SQL -> grounded answer.

    answer_question("Which team has the highest SLA breach rate?")
      -> {"answer", "sql", "dataframe", "status", "reason", "provider", "ungrounded_numbers"}

Steps: build schema prompt -> LLM writes SQL -> sql_guard validates and runs it
on a read-only connection (one retry on execution errors) -> second LLM call
explains ONLY the returned rows -> every attempt is written to ai_query_log.
"""
import re
from datetime import datetime, timedelta

from src import config
from src.ai import llm_client
from src.ai.sql_guard import run_safe_query
from src.analytics import read_only_connection
from src.load_db import connect, init_db

EXAMPLES = """Q: How many tickets are currently open?
SQL: SELECT COUNT(*) AS open_tickets FROM tickets WHERE status IN ('Open', 'In Progress')

Q: What is the SLA breach rate by team?
SQL: SELECT tm.team_name, COUNT(*) AS tickets, ROUND(100.0 * SUM(t.sla_breached) / COUNT(*), 2) AS breach_rate_pct FROM tickets t JOIN teams tm ON tm.team_id = t.team_id GROUP BY tm.team_name ORDER BY breach_rate_pct DESC

Q: Average resolution time of P1 tickets per month
SQL: SELECT opened_month, ROUND(AVG(resolution_hours), 2) AS mttr_hours FROM tickets WHERE priority = 'P1' AND resolution_hours IS NOT NULL GROUP BY opened_month ORDER BY opened_month

Q: Top 5 affected services in 2026-09
SQL: SELECT c.affected_service, COUNT(*) AS tickets FROM tickets t JOIN categories c ON c.category_id = t.category_id WHERE t.opened_month = '2026-09' GROUP BY c.affected_service ORDER BY tickets DESC LIMIT 5

Q: Why did SLA breaches change in 2026-09 compared with 2026-08?
SQL: SELECT c.affected_service, SUM(CASE WHEN t.opened_month = '2026-09' THEN t.sla_breached ELSE 0 END) AS breaches_2026_09, SUM(CASE WHEN t.opened_month = '2026-08' THEN t.sla_breached ELSE 0 END) AS breaches_2026_08, SUM(CASE WHEN t.opened_month = '2026-09' THEN t.sla_breached ELSE 0 END) - SUM(CASE WHEN t.opened_month = '2026-08' THEN t.sla_breached ELSE 0 END) AS change FROM tickets t JOIN categories c ON c.category_id = t.category_id WHERE t.opened_month IN ('2026-08', '2026-09') GROUP BY c.affected_service ORDER BY change DESC LIMIT 10"""

EXPLAIN_SYSTEM = ("You explain database query results to IT managers. Explain the result in 2-4 "
                  "plain-English sentences using ONLY the rows provided. Do not compute new numbers. "
                  "If the rows are empty or insufficient, say 'insufficient data'. Label any reasons "
                  "as 'possible factors', never as confirmed causes.")
WRITE_INTENT = re.compile(r"^\s*(please\s+)?(delete|drop|truncate|update|insert|remove|alter|wipe|"
                          r"erase|modify|overwrite)\b", re.I)


def describe_schema(db_path=None):
    """Tables, columns and types of the approved tables, read from the database."""
    conn = read_only_connection(db_path)
    try:
        lines = []
        for table in sorted(config.APPROVED_TABLES):
            cols = conn.execute(f"SELECT name, type FROM pragma_table_info('{table}')").fetchall()
            lines.append(f"{table}({', '.join(f'{n} {t}' for n, t in cols)})")
        months = conn.execute("SELECT MIN(opened_month), MAX(opened_month) FROM tickets").fetchone()
        teams = [r[0] for r in conn.execute("SELECT team_name FROM teams ORDER BY team_name")]
    finally:
        conn.close()
    return "\n".join(lines), months, teams


def build_system_prompt(db_path=None):
    schema, (first, last), teams = describe_schema(db_path)
    prev = (datetime.strptime(last, "%Y-%m") - timedelta(days=1)).strftime("%Y-%m") if last else "unknown"
    return f"""You are IncidentIQ, an expert SQLite analyst. Convert the user's question into ONE SQLite SELECT query.

SCHEMA
{schema}
Foreign keys: tickets.team_id -> teams.team_id, tickets.agent_id -> agents.agent_id, tickets.category_id -> categories.category_id, agents.team_id -> teams.team_id, resolutions.ticket_id -> tickets.ticket_id, tickets.batch_id -> batches.batch_id.
Category, subcategory and affected_service live in the categories table; team_name in teams; agent_name in agents.

VALUE HINTS
- tickets.priority: 'P1' (Critical), 'P2' (High), 'P3' (Medium), 'P4' (Low)
- tickets.status: 'Open', 'In Progress', 'Resolved', 'Closed'. Open tickets = status IN ('Open', 'In Progress')
- categories.category: {', '.join(repr(c) for c in config.APPROVED_CATEGORIES)}
- teams.team_name: {', '.join(repr(t) for t in teams)}
- Dates are TEXT 'YYYY-MM-DD HH:MM:SS'. tickets.opened_month is 'YYYY-MM'. tickets.opened_weekday is 'Monday'..'Sunday'. tickets.opened_hour is 0-23.
- tickets.sla_breached is 1 or 0. Breach rate % = ROUND(100.0 * SUM(sla_breached) / COUNT(*), 2).
- tickets.resolution_hours is NULL for open tickets. MTTR = AVG(resolution_hours).
- Data covers {first} to {last}. "last month" or "this month" means '{last}'; "the month before" means '{prev}'.

SQLITE RULES
- Use strftime() for date parts; there is no YEAR(), MONTH() or DATEDIFF().
- Give computed columns short aliases and ORDER BY so the most important row comes first.
- Only SELECT (or WITH ... SELECT) is allowed.

EXAMPLES
{EXAMPLES}

Return ONLY the SQL, no markdown, no explanation. If the question cannot be answered from the schema, return exactly: CANNOT_ANSWER"""


def extract_sql(text):
    """Strip markdown fences and any leading chatter around the SQL."""
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    text = (fenced.group(1) if fenced else text).strip()
    start = re.search(r"^\s*(SELECT|WITH)\b", text, re.I | re.M)
    if start and not re.match(r"\s*(SELECT|WITH)\b", text, re.I):
        text = text[start.start():]
    return re.sub(r"^(sql|query)\s*:\s*", "", text.strip(), flags=re.I)


def generate_sql(question, error=None, previous_sql=None, db_path=None):
    """Ask the LLM for SQL (optionally including the previous error for a retry)."""
    user = f"Question: {question}"
    if error:
        user += f"\n\nYour previous SQL:\n{previous_sql}\nfailed with this error:\n{error}\nReturn a corrected query."
    return extract_sql(llm_client.ask_llm(build_system_prompt(db_path), user))


def ungrounded_numbers(answer, df, context=""):
    """Numbers in the answer that appear neither in the result rows nor in the question/SQL."""
    number = re.compile(r"-?\d+(?:\.\d+)?")
    cells = list(df.astype(str).to_numpy().ravel()) + [context]
    known = {float(x) for cell in cells for x in number.findall(cell)}
    known |= {round(v, d) for v in list(known) for d in (0, 1, 2)}
    return [x for x in number.findall(answer.replace(",", ""))
            if float(x) not in known and float(x) not in range(0, 11)]  # allow small counting words


def explain(question, sql, df):
    """Second LLM call: plain-English explanation built only from the rows."""
    if df.empty:
        return "Insufficient data: the query returned no rows."
    rows = df.head(30).to_csv(index=False)
    user = f"Question: {question}\nSQL used: {sql}\nRows ({len(df)} total, first {min(len(df), 30)} shown):\n{rows}"
    return llm_client.ask_llm(EXPLAIN_SYSTEM, user, max_tokens=300)


def log_query(question, sql, status, row_count, summary, db_path=None):
    init_db(db_path)
    conn = connect(db_path)
    with conn:
        conn.execute("INSERT INTO ai_query_log (asked_at, question, generated_sql, validation_status, "
                     "row_count, answer_summary) VALUES (?, ?, ?, ?, ?, ?)",
                     (datetime.now().isoformat(timespec="seconds"), question, sql, status, row_count,
                      (summary or "")[:500]))
    conn.close()


def answer_question(question, db_path=None, explain_answer=True, generate_fn=None):
    """Full text-to-SQL flow. generate_fn(question, error, previous_sql) can replace the LLM (tests)."""
    generate_fn = generate_fn or (lambda q, e=None, p=None: generate_sql(q, e, p, db_path))
    result = {"answer": "", "sql": None, "dataframe": None, "status": "Failed", "reason": "",
              "provider": None, "ungrounded_numbers": []}
    try:
        if WRITE_INTENT.match(question):
            result.update(status="Blocked", reason="Write request refused: IncidentIQ is read-only",
                          answer="Blocked: I can only read data, never change or delete it.")
            return result
        sql = generate_fn(question)
        result["provider"] = llm_client.LAST_PROVIDER
        if sql.strip().upper().startswith("CANNOT_ANSWER"):
            result.update(sql="CANNOT_ANSWER", reason="CANNOT_ANSWER",
                          answer="I can't answer that from the incident database.")
            return result
        run = run_safe_query(sql, db_path)
        if run["status"] == "Failed":  # one retry, giving the LLM the error message
            sql = generate_fn(question, run["reason"], run["sql"])
            run = run_safe_query(sql, db_path)
        result.update(sql=run["sql"], status=run["status"], reason=run["reason"], dataframe=run["df"])
        if run["status"] == "Blocked":
            result["answer"] = f"Blocked by the SQL guard: {run['reason']}"
        elif run["status"] == "Failed":
            result["answer"] = f"The query failed: {run['reason']}"
        elif explain_answer:
            result["answer"] = explain(question, run["sql"], run["df"])
            result["ungrounded_numbers"] = ungrounded_numbers(result["answer"], run["df"],
                                                              f"{question} {run['sql']}")
        else:
            result["answer"] = f"{len(run['df'])} row(s) returned."
        return result
    except llm_client.LLMUnavailableError as exc:
        result.update(status="Failed", reason="LLM unavailable", answer=str(exc))
        return result
    finally:
        rows = None if result["dataframe"] is None else len(result["dataframe"])
        log_query(question, result["sql"], result["status"], rows, result["answer"], db_path)

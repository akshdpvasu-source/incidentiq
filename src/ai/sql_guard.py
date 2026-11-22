"""Phase 6b - the SQL guard. Every AI-generated query must pass through here.

    validate_sql(sql)            -> (ok, reason, safe_sql)
    run_safe_query(sql, db_path) -> {"status", "reason", "sql", "df"}

Defence in depth:
 1. static checks: exactly one SELECT/WITH statement, no write keywords,
    no comments, no stacked ';', only approved tables, LIMIT added/capped
 2. read-only SQLite connection (mode=ro) + PRAGMA query_only
 3. SQLite authorizer: the engine itself refuses reads of other tables
 4. queries running longer than QUERY_TIMEOUT_SECONDS are aborted
Text inside 'string literals' is ignored by the keyword checks, so
WHERE description LIKE '%delete%' is allowed.
"""
import re
import sqlite3
import time

import pandas as pd

from src import config
from src.analytics import read_only_connection

FORBIDDEN = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE", "ATTACH", "DETACH",
             "PRAGMA", "VACUUM", "TRUNCATE", "REINDEX", "ANALYZE"]
FORBIDDEN_FUNCTIONS = ["load_extension", "readfile", "writefile", "fts3_tokenizer"]
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(FORBIDDEN) + r")\b", re.I)
_BLOCKED_TABLE_RE = re.compile(r"\b(ai_query_log|rejected_records|sqlite_\w+)\b", re.I)
_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*|[(),]")
_CLAUSE_END = {"where", "group", "order", "limit", "having", "union", "intersect", "except", "join",
               "inner", "left", "right", "full", "cross", "natural", "on", "using", "window"}
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)(\s+OFFSET\s+\d+)?\s*$", re.I)


def referenced_tables(scan_sql):
    """Names that appear after FROM / JOIN (including comma-separated FROM lists)."""
    tokens, tables, i = _TOKEN_RE.findall(scan_sql), set(), 0
    while i < len(tokens):
        if tokens[i].lower() not in ("from", "join"):
            i += 1
            continue
        i, expect_table = i + 1, True
        while i < len(tokens):
            tok = tokens[i]
            if expect_table:
                if tok == "(":        # sub-query: its own FROM is checked later
                    break
                tables.add(tok.lower())
                expect_table = False
            elif tok == ",":
                expect_table = True
            elif tok.lower() in _CLAUSE_END or tok in "()":
                break
            i += 1
    return tables


def validate_sql(sql):
    """Static safety checks. Returns (ok, reason, safe_sql_or_None)."""
    if not sql or not sql.strip():
        return False, "Empty query", None
    text = re.sub(r";\s*$", "", sql.strip())          # one trailing ';' is fine
    if len(text) > 4000:
        return False, "Query is too long", None
    scan = _STRING_RE.sub("''", text)                  # hide string literal contents
    if "'" in scan.replace("''", ""):
        return False, "Unbalanced quotes", None
    scan = re.sub(r'["`\[\]]', " ", scan)             # unwrap "quoted" identifiers
    if "--" in scan or "/*" in scan or "*/" in scan:
        return False, "SQL comments are not allowed", None
    if ";" in scan:
        return False, "Multiple statements are not allowed", None
    first = re.match(r"\s*([A-Za-z]+)", scan)
    if not first or first.group(1).upper() not in ("SELECT", "WITH"):
        return False, "Only SELECT queries are allowed", None
    if (bad := _FORBIDDEN_RE.search(scan)):
        return False, f"Forbidden keyword: {bad.group(1).upper()}", None
    for func in FORBIDDEN_FUNCTIONS:
        if re.search(rf"\b{func}\s*\(", scan, re.I):
            return False, f"Forbidden function: {func}", None
    if (bad := _BLOCKED_TABLE_RE.search(scan)):
        return False, f"Table not allowed: {bad.group(1)}", None
    cte_names = {n.lower() for n in re.findall(r"\b([A-Za-z_]\w*)\s*(?:\([^()]*\))?\s+AS\s*\(", scan, re.I)}
    unknown = referenced_tables(scan) - cte_names - config.APPROVED_TABLES
    if unknown:
        return False, f"Table not allowed: {', '.join(sorted(unknown))}", None
    limit = _LIMIT_RE.search(text)
    if limit is None:
        text = f"{text}\nLIMIT {config.MAX_ROWS}"
    elif int(limit.group(1)) > config.MAX_ROWS:
        text = text[:limit.start(1)] + str(config.MAX_ROWS) + text[limit.end(1):]
    return True, "OK", text


def _authorizer(action, arg1, arg2, db_name, trigger):
    """Called by SQLite for every table read / function call while preparing a query."""
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if arg1 in config.APPROVED_TABLES else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if (arg2 or "").lower() in FORBIDDEN_FUNCTIONS else sqlite3.SQLITE_OK
    allowed = (sqlite3.SQLITE_SELECT, getattr(sqlite3, "SQLITE_RECURSIVE", 33))
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def execute_readonly(sql, db_path=None):
    """Run SQL on a read-only, authorizer-protected connection (no static checks)."""
    conn = read_only_connection(db_path)
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.set_authorizer(_authorizer)
        deadline = time.monotonic() + config.QUERY_TIMEOUT_SECONDS
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        return pd.read_sql_query(sql, conn)
    finally:
        conn.close()


def run_safe_query(sql, db_path=None):
    """Validate then execute. status is 'Valid', 'Blocked' or 'Failed'."""
    ok, reason, safe_sql = validate_sql(sql)
    if not ok:
        return {"status": "Blocked", "reason": reason, "sql": sql, "df": None}
    try:
        df = execute_readonly(safe_sql, db_path)
    except Exception as exc:  # sqlite errors, timeouts, authorizer denials
        message = str(exc)
        status = "Blocked" if ("prohibited" in message or "not authorized" in message) else "Failed"
        if "interrupted" in message:
            message = f"Query took longer than {config.QUERY_TIMEOUT_SECONDS}s and was stopped"
        return {"status": status, "reason": message, "sql": safe_sql, "df": None}
    return {"status": "Valid", "reason": "OK", "sql": safe_sql, "df": df}

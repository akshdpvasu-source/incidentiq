"""Phase 3 - clean, standardize, validate and derive fields.

    clean(df, batch_id) -> (cleaned_df, rejected_df, report)

Rules (every rule is counted in the report):
  1 snake_case column names, trim whitespace in all strings
  2 priority -> P1..P4                  unmappable -> INVALID_PRIORITY
  3 category -> approved list (fuzzy)   unmappable -> INVALID_CATEGORY
  4 status   -> Open / In Progress / Resolved / Closed
  5 dates    -> timezone-naive UTC      unparsable -> BAD_DATE
  6 resolved_at < opened_at                        -> RESOLVED_BEFORE_OPENED
  7 duplicate ticket_id: keep the first            -> DUPLICATE_ID
  8 negative / empty reopen_count -> 0 (fixed)
  9 derived fields (resolution_hours, sla_breached, ticket_age_days, ...)
 10 rejected rows keep the original payload + reason_code
A row is rejected for the first rule it fails, so raw = cleaned + rejected.
Slash dates (05/31/2026) are read as US month/day.
"""
import difflib
import json
import re
from collections import Counter

import numpy as np
import pandas as pd

from src import config

EXPECTED_COLUMNS = ["ticket_id", "opened_at", "resolved_at", "closed_at", "priority", "status",
                    "category", "subcategory", "affected_service", "assignment_team", "assigned_agent",
                    "short_description", "description", "resolution_notes", "resolution_code",
                    "sla_target_hours", "reopen_count", "source_system"]
DERIVED_COLUMNS = ["resolution_hours", "response_hours", "sla_breached", "ticket_age_days",
                   "opened_hour", "opened_weekday", "opened_month"]
PRIORITY_WORDS = [("critical", "P1"), ("urgent", "P1"), ("high", "P2"), ("medium", "P3"),
                  ("moderate", "P3"), ("normal", "P3"), ("low", "P4")]
STATUS_MAP = {"open": "Open", "new": "Open", "in progress": "In Progress", "in-progress": "In Progress",
              "inprogress": "In Progress", "wip": "In Progress", "assigned": "In Progress",
              "on hold": "In Progress", "pending": "In Progress", "resolved": "Resolved",
              "fixed": "Resolved", "closed": "Closed", "done": "Closed", "completed": "Closed"}
CATEGORY_ALIASES = {"e-mail": "Email", "mail": "Email", "db": "Database", "app error": "Application Error",
                    "application": "Application Error", "password": "Access/Password",
                    "access": "Access/Password", "cloud": "Cloud Infrastructure", "hw": "Hardware",
                    "infosec": "Security", "net": "Network"}
DATE_FORMATS = ["%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
                "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"]
_APPROVED = {c.lower(): c for c in config.APPROVED_CATEGORIES}


def snake_case(name):
    """'Ticket ID' / 'ticketId' -> 'ticket_id'."""
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name).strip())
    return re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()


def standardize_priority(value):
    """'1 - Critical', 'P1', 'critical' -> 'P1'; unknown -> None."""
    if value is None:
        return None
    text = str(value).strip().lower()
    match = re.match(r"^p?\s*([1-4])\b", text)
    if match:
        return f"P{match.group(1)}"
    return next((code for word, code in PRIORITY_WORDS if re.search(rf"\b{word}\b", text)), None)


def standardize_category(value):
    """Exact (case-insensitive) match, then aliases, then difflib fuzzy match."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip().lower())
    if text in _APPROVED:
        return _APPROVED[text]
    if text in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[text]
    match = difflib.get_close_matches(text, list(_APPROVED), n=1, cutoff=0.75)
    return _APPROVED[match[0]] if match else None


def standardize_status(value):
    if value is None:
        return None
    return STATUS_MAP.get(re.sub(r"[_\s]+", " ", str(value).strip().lower()))


def parse_dates(series):
    """Parse mixed formats. Returns (timestamps, mask of values that were not ISO)."""
    text = series.astype(object)
    parsed = pd.to_datetime(text, format="ISO8601", utc=True, errors="coerce")
    iso_ok = parsed.notna()
    for fmt in DATE_FORMATS:
        todo = parsed.isna() & text.notna()
        if not todo.any():
            break
        parsed.loc[todo] = pd.to_datetime(text[todo], format=fmt, utc=True, errors="coerce")
    return parsed.dt.tz_convert(None), parsed.notna() & ~iso_ok


def _map_unique(series, func):
    """Apply func once per distinct value (fast on 20k rows)."""
    return series.map({v: func(v) for v in series.dropna().unique()})


def _tidy(value):
    """Trim strings and turn '' / NaN into None."""
    if isinstance(value, str):
        return value.strip() or None
    return None if pd.isna(value) else value


def _json_safe(record):
    return json.dumps({str(k): (None if not isinstance(v, str) and pd.isna(v) else v)
                       for k, v in record.items()}, default=str)


def clean(df, batch_id, as_of=None):
    """Clean one ingested batch. The input DataFrame is never modified."""
    as_of = pd.Timestamp(as_of or config.AS_OF)
    fixed = Counter()
    payloads = [_json_safe(r) for r in df.to_dict(orient="records")]

    work = df.copy()                                                     # rule 1
    work.columns = [snake_case(c) for c in work.columns]
    for col in EXPECTED_COLUMNS:
        if col not in work.columns:
            work[col] = None
    work = work.astype(object)
    for col in work.columns:
        fixed["whitespace_trimmed"] += sum(isinstance(v, str) and v != v.strip() and v.strip() != ""
                                           for v in work[col])
        work[col] = work[col].map(_tidy).astype(object)

    reason = pd.Series(None, index=work.index, dtype=object)

    def reject(mask, code):
        reason.loc[mask & reason.isna()] = code

    reject(work["ticket_id"].isna(), "MISSING_ID")                       # rule 7
    reject(work["ticket_id"].notna() & work.duplicated("ticket_id", keep="first"), "DUPLICATE_ID")

    for col, func, code in (("priority", standardize_priority, "INVALID_PRIORITY"),   # rules 2, 3
                            ("category", standardize_category, "INVALID_CATEGORY")):
        new = _map_unique(work[col], func)
        reject(new.isna(), code)
        fixed[f"{col}_standardized"] += int((new.notna() & (new != work[col])).sum())
        work[col] = new

    for col in ("opened_at", "resolved_at", "closed_at"):                # rule 5
        parsed, reformatted = parse_dates(work[col])
        bad = (work[col].notna() & parsed.isna()) | (work[col].isna() if col == "opened_at" else False)
        reject(bad, "BAD_DATE")
        fixed["dates_reformatted"] += int((reformatted & ~bad).sum())
        work[col] = parsed

    reject(work["resolved_at"] < work["opened_at"], "RESOLVED_BEFORE_OPENED")   # rule 6

    status = _map_unique(work["status"], standardize_status)             # rule 4
    inferred = status.isna()
    fixed["status_standardized"] += int((~inferred & (status != work["status"])).sum())
    fixed["status_inferred"] += int(inferred.sum())
    work["status"] = status.where(~inferred, np.where(work["resolved_at"].notna(), "Resolved", "Open"))

    reopen = pd.to_numeric(work["reopen_count"], errors="coerce")        # rule 8
    bad_reopen = reopen.isna() | (reopen < 0)
    fixed["reopen_count_set_to_zero"] += int(bad_reopen.sum())
    work["reopen_count"] = reopen.where(~bad_reopen, 0)

    target = work["priority"].map(config.SLA_HOURS)
    raw_target = pd.to_numeric(work["sla_target_hours"], errors="coerce")
    fixed["sla_target_set_from_priority"] += int((target.notna() & (raw_target != target)).sum())
    work["sla_target_hours"] = target

    good = work[reason.isna()].copy()                                    # rule 9
    good["reopen_count"] = good["reopen_count"].astype(int)
    elapsed = (good["resolved_at"].fillna(as_of) - good["opened_at"]).dt.total_seconds() / 3600
    good["resolution_hours"] = ((good["resolved_at"] - good["opened_at"]).dt.total_seconds() / 3600).round(2)
    good["response_hours"] = None  # the source systems have no first-response timestamp
    good["sla_breached"] = (elapsed > good["sla_target_hours"]).astype(int)
    good["ticket_age_days"] = (elapsed / 24).round(2)
    good["opened_hour"] = good["opened_at"].dt.hour
    good["opened_weekday"] = good["opened_at"].dt.day_name()
    good["opened_month"] = good["opened_at"].dt.strftime("%Y-%m")
    cleaned = good[EXPECTED_COLUMNS + DERIVED_COLUMNS].reset_index(drop=True)

    bad_rows = np.flatnonzero(reason.notna().to_numpy())                 # rule 10
    rejected = pd.DataFrame({"raw_ticket_id": work["ticket_id"].iloc[bad_rows].tolist(),
                             "reason_code": reason.iloc[bad_rows].tolist(),
                             "raw_payload": [payloads[i] for i in bad_rows]})
    report = {"batch_id": batch_id, "as_of": str(as_of), "raw_rows": len(df),
              "cleaned_rows": len(cleaned), "rejected_rows": len(rejected),
              "reconciled": len(df) == len(cleaned) + len(rejected),
              "rejected_by_reason": dict(Counter(rejected["reason_code"])),
              "fixed_by_rule": {k: int(v) for k, v in fixed.items()}}
    return cleaned, rejected, report


def print_report(report):
    """Readable table of the cleaning report."""
    print(f"\nCleaning report for {report['batch_id']}")
    print(f"  raw rows      {report['raw_rows']:>8,}")
    print(f"  cleaned rows  {report['cleaned_rows']:>8,}")
    print(f"  rejected rows {report['rejected_rows']:>8,}")
    print(f"  raw == cleaned + rejected: {report['reconciled']}")
    for title, key in (("Rejected by reason", "rejected_by_reason"), ("Fixed by rule", "fixed_by_rule")):
        print(f"  {title}:")
        for name, count in sorted(report[key].items()):
            print(f"    {name:<30}{count:>8,}")


def save_outputs(cleaned, rejected, report):
    """Write cleaned CSV, rejected CSV and the JSON report for this batch."""
    batch = report["batch_id"]
    paths = {"cleaned": config.CLEANED_DIR / f"{batch}_cleaned.csv",
             "rejected": config.REJECTED_DIR / f"{batch}_rejected.csv",
             "report": config.CLEANED_DIR / f"{batch}_report.json"}
    cleaned.to_csv(paths["cleaned"], index=False)
    rejected.to_csv(paths["rejected"], index=False)
    paths["report"].write_text(json.dumps(report, indent=2))
    return paths

"""Dirty test file + the acceptance summary printed by generate_data.py.

make_dirty() puts each defect type in its own block of rows, so the tests
can assert exact reject counts:

  rows   0-9    priority empty                     -> INVALID_PRIORITY (10)
  rows  10-14   priority "ASAP", "N/A", "9" ...    -> INVALID_PRIORITY (5)
  rows  15-34   priority "High", "1 - Critical"... -> fixed (mapped to P1-P4)
  rows  35-39   unparsable opened_at               -> BAD_DATE (5)
  rows  40-47   resolved_at 3h before opened_at    -> RESOLVED_BEFORE_OPENED (8)
  rows  58-61   category "Coffee Machine" ...      -> INVALID_CATEGORY (4)
  rows  62-69   reopen_count negative or empty     -> fixed (set to 0)
  rows  70-99   category typos / CAPS / spaces     -> fixed (fuzzy match)
  3 Network rows in 100-189: "netwrok", "NETWORK", "Network "  -> fixed
  rows 100-129  dates as 05/31/2026 14:30 or 31-May-2026 14:30 -> fixed
  rows 130-139  status "RESOLVED", "closed ", "in-progress"    -> fixed
  rows 190-199  exact copies of rows 140-149       -> DUPLICATE_ID (10)
Expected: 200 raw = 158 cleaned + 42 rejected.
"""
import pandas as pd

from src import config
from src.generate_data import DATE_FMT, generate_tickets

EXPECTED_REJECTS = {"INVALID_PRIORITY": 15, "BAD_DATE": 5, "RESOLVED_BEFORE_OPENED": 8,
                    "INVALID_CATEGORY": 4, "DUPLICATE_ID": 10}
US_FMT, TEXT_FMT = "%m/%d/%Y %H:%M", "%d-%b-%Y %H:%M"


def _misspell(category, i):
    """Three kinds of bad spelling: CAPS, padded lower case, two swapped letters."""
    if i % 3 == 0:
        return category.upper()
    if i % 3 == 1 or len(category) <= 4:
        return f"  {category.lower()} "
    return category[:2] + category[3] + category[2] + category[4:]


def make_dirty(n=config.N_DIRTY_TICKETS, seed=config.SEED + 2):
    """Return a DataFrame of n tickets (ids INC09...) with the defects listed above."""
    df = generate_tickets(n, seed, "Legacy-CSV", 900000).astype(object)
    df["reopen_count"] = df["reopen_count"].astype(str)
    df.loc[0:9, "priority"] = ""
    df.loc[10:14, "priority"] = ["ASAP", "unknown", "N/A", "Priority?", "9"]
    df.loc[15:34, "priority"] = ["P1", "High", "1 - Critical", "2", "low",
                                 "Medium", "3 - Medium", "critical", "p4", "4"] * 2
    df.loc[35:39, "opened_at"] = ["not a date", "2026-13-45 10:00:00", "yesterday", "31/31/2026", "??"]
    for i in range(40, 48):
        df.at[i, "resolved_at"] = (pd.Timestamp(df.at[i, "opened_at"])
                                   - pd.Timedelta(hours=3)).strftime(DATE_FMT)
        df.at[i, "status"] = "Resolved"
    df.loc[58:61, "category"] = ["Coffee Machine", "Catering", "Parking", "Furniture"]
    df.loc[62:65, "reopen_count"] = ["-1", "-2", "-5", "-1"]
    df.loc[66:69, "reopen_count"] = ""
    for i in range(70, 100):
        df.at[i, "category"] = _misspell(df.at[i, "category"], i)
    network_rows = [i for i in range(100, 190) if df.at[i, "category"] == "Network"][:3]
    for i, variant in zip(network_rows, ["netwrok", "NETWORK", "Network "]):
        df.at[i, "category"] = variant
    for col in ("opened_at", "resolved_at", "closed_at"):
        for i in range(100, 130):
            if df.at[i, col]:
                df.at[i, col] = pd.Timestamp(df.at[i, col]).strftime(US_FMT if i < 115 else TEXT_FMT)
    status_variants = {"Closed": "closed ", "Resolved": "RESOLVED", "Open": "open",
                       "In Progress": "in-progress"}
    for i in range(130, 140):
        df.at[i, "status"] = status_variants[df.at[i, "status"]]
    df.loc[190:199] = df.loc[140:149].to_numpy()
    return df


def print_summary(df):
    """Acceptance printout: counts, priority mix, breach rate and planted patterns."""
    as_of = pd.Timestamp(config.AS_OF)
    opened = pd.to_datetime(df["opened_at"])
    resolved = pd.to_datetime(df["resolved_at"].replace("", None))
    hours = ((resolved.fillna(as_of)) - opened).dt.total_seconds() / 3600
    breached = hours > df["sla_target_hours"]
    is_open = resolved.isna()
    print(f"\nTotal rows: {len(df):,}   open/in-progress: {is_open.mean():.1%}")
    print("Priority distribution:",
          (df["priority"].value_counts(normalize=True).sort_index() * 100).round(1).to_dict())
    print(f"SLA breach rate: overall {breached.mean():.1%}  "
          f"(resolved tickets only {breached[~is_open].mean():.1%})")
    team = breached.groupby(df["assignment_team"]).mean()
    others = breached[df["assignment_team"] != config.WEAK_TEAM].mean()
    print(f"[B] {config.WEAK_TEAM} breach {team[config.WEAK_TEAM]:.1%} vs others {others:.1%} "
          f"(x{team[config.WEAK_TEAM] / others:.2f})")
    vpn = df[df["affected_service"] == "VPN Gateway"].groupby(opened.dt.strftime("%Y-%m")).size()
    print(f"[A] VPN Gateway tickets {config.VPN_SPIKE_MONTH}: {vpn.get(config.VPN_SPIKE_MONTH, 0)} "
          f"vs average of other months {vpn.drop(config.VPN_SPIKE_MONTH, errors='ignore').mean():.0f}")
    peak = df.groupby([opened.dt.day_name(), opened.dt.hour]).size().idxmax()
    print(f"[C] Busiest weekday/hour: {peak[0]} {peak[1]:02d}:00")
    subs = pd.DataFrame({"sub": df["subcategory"], "h": hours.where(~is_open)}).groupby("sub")["h"]
    top = subs.size().idxmax()
    print(f"[D] Most frequent subcategory: {top} ({subs.size()[top]:,} tickets), median "
          f"{subs.median()[top]:.1f}h - fastest is {subs.median().idxmin()}")

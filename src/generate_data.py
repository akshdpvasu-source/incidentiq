"""Phase 1 - synthetic incident ticket generator (all data is fake).

Creates:
  data/raw/incidents_YYYYMMDD.csv     N_TICKETS tickets over 12 months
  data/raw/incidents_dirty_test.csv   200 rows with deliberate defects
  data/api_source/api_tickets.csv     ~2,000 extra tickets served by the mock API
  data/reference/teams.csv, agents.csv  roster used to fill the teams/agents tables

The planted patterns A-D are documented in docs/planted_patterns.md.
Run:  python src/generate_data.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # lets "python src/x.py" import src.*

import numpy as np
import pandas as pd
from faker import Faker

from src import config
from src.catalog import CATEGORIES, CATEGORY_TEAMS, CHANNELS, GENERIC, IMPACTS, SITES, TEAMS

COLUMNS = ["ticket_id", "opened_at", "resolved_at", "closed_at", "priority", "status", "category",
           "subcategory", "affected_service", "assignment_team", "assigned_agent", "short_description",
           "description", "resolution_notes", "resolution_code", "sla_target_hours", "reopen_count",
           "source_system"]
DATE_FMT = "%Y-%m-%d %H:%M:%S"
WEEKDAY_W = [1.35, 1.1, 1.05, 1.0, 0.95, 0.3, 0.25]                      # Mon..Sun
HOUR_W = [0.15] * 7 + [0.5, 1.2, 1.8, 1.8, 1.5, 1.0, 1.2, 1.3, 1.2, 1.0, 0.7, 0.4] + [0.25] * 5
RELEASE_BOOST = {"Application Error": 4.0, "Cloud Infrastructure": 2.0}  # release-evening mix
MEDIAN_HOURS = {"P1": 1.6, "P2": 3.6, "P3": 8.5, "P4": 24.0}           # log-normal medians
SIGMA = 0.75
EXPERIENCE_SPEED = {"Junior": 1.15, "Mid": 1.0, "Senior": 0.9}
WEAK_TEAM_SLOWDOWN = 1.35  # pattern B
SPIKE_SLOWDOWN = 1.5       # VPN Gateway is overloaded during the spike month


class Chooser:
    """Fast weighted random choice (cumulative weights + one random number)."""

    def __init__(self, options, weights=None):
        self.options = list(options)
        w = np.asarray(weights if weights is not None else [1] * len(self.options), dtype=float)
        self.cum = np.cumsum(w / w.sum())

    def pick(self, rng):
        return self.options[min(int(np.searchsorted(self.cum, rng.random())), len(self.options) - 1)]


def build_roster(seed=config.SEED):
    """Five synthetic agents per team. Infra-Team-B is deliberately junior-heavy."""
    fake, rng = Faker(), np.random.default_rng(seed)
    fake.seed_instance(seed)
    rows, used = [], set()
    for team in TEAMS:
        levels = (["Junior"] * 4 + ["Mid"]) if team == config.WEAK_TEAM else \
            rng.choice(["Junior", "Mid", "Senior"], size=5, p=[0.3, 0.4, 0.3]).tolist()
        for level in levels:
            name = f"{fake.first_name()} {fake.last_name()}"
            while name in used:
                name = f"{fake.first_name()} {fake.last_name()}"
            used.add(name)
            rows.append({"agent_name": name, "team_name": team, "experience_level": level})
    return pd.DataFrame(rows)


def _choosers(roster, rng):
    """Pre-build every weighted choice so the per-ticket loop stays fast."""
    ch = {"category": Chooser(CATEGORIES, [c["weight"] for c in CATEGORIES.values()]),
          "release_category": Chooser(CATEGORIES, [c["weight"] * RELEASE_BOOST.get(n, 1)
                                                   for n, c in CATEGORIES.items()]),
          "priority": Chooser(config.PRIORITY_SHARES, list(config.PRIORITY_SHARES.values())),
          "site": Chooser(SITES), "channel": Chooser(CHANNELS), "impact": Chooser(IMPACTS)}
    for name, cat in CATEGORIES.items():
        ch[name + "/sub"] = Chooser(cat["subs"], [s["w"] for s in cat["subs"].values()])
        ch[name + "/service"] = Chooser(cat["services"], cat.get("service_w"))
        ch[name + "/team"] = Chooser(CATEGORY_TEAMS[name])
    for team, grp in roster.groupby("team_name"):  # uneven workload inside each team
        ch[team + "/agent"] = Chooser(list(zip(grp.agent_name, grp.experience_level)),
                                      rng.dirichlet([3] * len(grp)))
    return ch


def _make_ticket(rng, fake, ch, opened_at, release=False, force_vpn_gateway=False):
    """Build one ticket as a dict; resolution time is kept in the helper field _hours."""
    category = "VPN" if force_vpn_gateway else ch["release_category" if release else "category"].pick(rng)
    sub_name = ch[category + "/sub"].pick(rng)
    sub = CATEGORIES[category]["subs"][sub_name]
    service = "VPN Gateway" if force_vpn_gateway else ch[category + "/service"].pick(rng)
    priority, team = ch["priority"].pick(rng), ch[category + "/team"].pick(rng)
    agent, level = ch[team + "/agent"].pick(rng)

    n = int(rng.integers(5, 60))
    fill = {"service": service, "user": fake.first_name(), "site": ch["site"].pick(rng),
            "code": f"0x{int(rng.integers(4096, 65535)):04X}", "n": n,
            "channel": ch["channel"].pick(rng), "impact": ch["impact"].pick(rng).format(n=n)}
    k = int(rng.integers(1, min(3, len(sub["detail"])) + 1))
    sentences = [sub["detail"][i] for i in rng.choice(len(sub["detail"]), size=k, replace=False)]
    sentences += [GENERIC[i] for i in rng.choice(len(GENERIC), size=int(rng.integers(1, 3)), replace=False)]
    code, fix = sub["fix"][int(rng.integers(len(sub["fix"])))]

    median = MEDIAN_HOURS[priority] * sub["speed"] * EXPERIENCE_SPEED[level]
    if team == config.WEAK_TEAM:
        median *= WEAK_TEAM_SLOWDOWN
    if service == "VPN Gateway" and opened_at.strftime("%Y-%m") == config.VPN_SPIKE_MONTH:
        median *= SPIKE_SLOWDOWN
    return {"opened_at": opened_at, "priority": priority, "category": category,
            "subcategory": sub_name, "affected_service": service, "assignment_team": team,
            "assigned_agent": agent, "short_description": sub["short"][int(rng.integers(2))].format(**fill),
            "description": " ".join(sentences).format(**fill), "resolution_notes": fix.format(**fill),
            "resolution_code": code, "sla_target_hours": config.SLA_HOURS[priority],
            "_hours": max(0.05, float(rng.lognormal(np.log(median), SIGMA)))}


def _hour_slots():
    """Every hour in the 12-month window, weighted to plant pattern C (peaks)."""
    slots = pd.date_range(config.DATA_START, config.AS_OF, freq="h", inclusive="left")
    weekday, hour = slots.day_of_week.to_numpy(), slots.hour.to_numpy()
    w = np.take(WEEKDAY_W, weekday) * np.take(HOUR_W, hour)
    monday_peak = (weekday == 0) & np.isin(hour, [9, 10])
    release = (weekday == 4) & (slots.day.to_numpy() <= 7) & (hour >= 18) & (hour <= 22)
    w = w * np.where(monday_peak, 1.6, 1.0) * np.where(release, 12.0, 1.0)
    return slots, w / w.sum(), release


def _plant_vpn_spike(rows, rng, fake, ch, slots, p):
    """Pattern A: add two extra VPN Gateway tickets for every normal one in the spike month."""
    month = config.VPN_SPIKE_MONTH
    in_month = [r["opened_at"].strftime("%Y-%m") == month for r in rows]
    base = sum(1 for r, m in zip(rows, in_month) if m and r["affected_service"] == "VPN Gateway")
    mask = np.asarray(slots.strftime("%Y-%m") == month)
    extra = [_make_ticket(rng, fake, ch, slots[i] + pd.Timedelta(seconds=int(rng.integers(3600))),
                          force_vpn_gateway=True)
             for i in rng.choice(len(slots), size=2 * base, p=p * mask / (p * mask).sum())]
    others = [i for i, m in enumerate(in_month) if not m]  # keep total = n
    drop = set(rng.choice(others, size=min(len(extra), len(others)), replace=False).tolist())
    return [r for i, r in enumerate(rows) if i not in drop] + extra


def _finalize(df, rng):
    """Turn resolution hours into timestamps, statuses and reopen counts (~8% stay open)."""
    as_of = pd.Timestamp(config.AS_OF)
    resolved = df["opened_at"] + pd.to_timedelta(df["_hours"], unit="h")
    is_open = (resolved > as_of).to_numpy().copy()
    extra = int(round(config.OPEN_SHARE * len(df))) - int(is_open.sum())
    if extra > 0:  # older tickets are less likely to still be open
        age_days = ((as_of - df["opened_at"]).dt.total_seconds() / 86400).to_numpy()
        w = np.where(is_open, 0.0, np.exp(-age_days / 60))
        is_open[rng.choice(len(df), size=extra, replace=False, p=w / w.sum())] = True
    closed = resolved + pd.to_timedelta(rng.uniform(0.5, 5, len(df)), unit="D")
    high = df["priority"].isin(["P1", "P2"]).to_numpy()
    open_status = np.where(high | (rng.random(len(df)) < 0.5), "In Progress", "Open")
    df["status"] = np.where(is_open, open_status, np.where(closed <= as_of, "Closed", "Resolved"))
    df["resolved_at"] = resolved.where(~is_open).dt.strftime(DATE_FMT).fillna("")
    df["closed_at"] = closed.where(df["status"] == "Closed").dt.strftime(DATE_FMT).fillna("")
    df.loc[is_open, ["resolution_notes", "resolution_code"]] = ""
    late = (df["_hours"] > df["sla_target_hours"]).to_numpy()
    df["reopen_count"] = np.where(late, rng.choice([0, 1, 2], len(df), p=[0.85, 0.12, 0.03]),
                                  rng.choice([0, 1, 2], len(df), p=[0.95, 0.045, 0.005]))
    df["opened_at"] = df["opened_at"].dt.strftime(DATE_FMT)
    return df


def generate_tickets(n, seed, source_system, id_start):
    """Generate n tickets with ids INC<id_start>... Same seed -> same data."""
    rng, fake = np.random.default_rng(seed), Faker()
    fake.seed_instance(seed)
    ch = _choosers(build_roster(), rng)
    slots, p, release = _hour_slots()
    idx = rng.choice(len(slots), size=n, p=p)
    times = slots[idx] + pd.to_timedelta(rng.integers(0, 3600, n), unit="s")
    rows = [_make_ticket(rng, fake, ch, times[i], bool(release[idx[i]])) for i in range(n)]
    rows = _plant_vpn_spike(rows, rng, fake, ch, slots, p)
    df = pd.DataFrame(rows).sort_values("opened_at", kind="stable").reset_index(drop=True)
    df["ticket_id"] = [f"INC{id_start + i:07d}" for i in range(len(df))]
    df["source_system"] = source_system
    return _finalize(df, rng)[COLUMNS]


def generate_all():
    """Write every generated file. Returns (main DataFrame, list of written paths)."""
    from src.dirty_data import make_dirty

    roster = build_roster()
    roster.to_csv(config.REFERENCE_DIR / "agents.csv", index=False)
    pd.DataFrame({"team_name": list(TEAMS), "region": list(TEAMS.values())}).to_csv(
        config.REFERENCE_DIR / "teams.csv", index=False)
    main_df = generate_tickets(config.N_TICKETS, config.SEED, "ServiceNow-Export", 100000)
    main_path = config.RAW_DIR / f"incidents_{datetime.now():%Y%m%d}.csv"
    main_df.to_csv(main_path, index=False)
    dirty_path = config.RAW_DIR / "incidents_dirty_test.csv"
    make_dirty().to_csv(dirty_path, index=False)
    api_path = config.API_SOURCE_DIR / "api_tickets.csv"
    generate_tickets(config.N_API_TICKETS, config.SEED + 1, "ServiceDesk-API", 500000).to_csv(
        api_path, index=False)
    return main_df, [main_path, dirty_path, api_path]


if __name__ == "__main__":
    from src.dirty_data import print_summary

    data, written = generate_all()
    for path in written:
        print(f"Wrote {path.relative_to(config.ROOT)} ({sum(1 for _ in open(path)) - 1:,} rows)")
    print_summary(data)

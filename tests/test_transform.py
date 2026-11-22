"""Phase 3 acceptance: every planted defect is caught and raw = cleaned + rejected."""
import json

import pandas as pd
import pytest

from src.dirty_data import EXPECTED_REJECTS, make_dirty
from src.transform import (clean, parse_dates, snake_case, standardize_category,
                           standardize_priority, standardize_status)


@pytest.fixture(scope="module")
def dirty_result():
    raw = make_dirty()
    cleaned, rejected, report = clean(raw.copy(), "B-TEST-001")
    return raw, cleaned, rejected, report


def test_dirty_file_reconciles(dirty_result):
    raw, cleaned, rejected, report = dirty_result
    assert len(raw) == 200
    assert len(raw) == len(cleaned) + len(rejected)
    assert report["reconciled"] is True
    assert (report["raw_rows"], report["cleaned_rows"], report["rejected_rows"]) == (200, 158, 42)


def test_each_defect_type_is_caught_exactly(dirty_result):
    *_, report = dirty_result
    assert report["rejected_by_reason"] == EXPECTED_REJECTS


def test_fixes_are_counted(dirty_result):
    *_, report = dirty_result
    fixed = report["fixed_by_rule"]
    assert fixed["priority_standardized"] == 18     # 20 variants, "P1" x2 already valid
    assert fixed["reopen_count_set_to_zero"] == 8
    assert fixed["status_standardized"] == 10
    assert fixed["dates_reformatted"] > 0
    assert fixed["category_standardized"] > 0


def test_cleaned_values_are_standard(dirty_result):
    _, cleaned, _, _ = dirty_result
    assert set(cleaned["priority"]) <= {"P1", "P2", "P3", "P4"}
    assert set(cleaned["status"]) <= {"Open", "In Progress", "Resolved", "Closed"}
    assert cleaned["ticket_id"].is_unique
    assert (cleaned["reopen_count"] >= 0).all()
    assert (cleaned["resolution_hours"].dropna() >= 0).all()


def test_rejected_rows_keep_original_payload(dirty_result):
    raw, _, rejected, _ = dirty_result
    row = rejected[rejected["reason_code"] == "BAD_DATE"].iloc[0]
    payload = json.loads(row["raw_payload"])
    assert payload["opened_at"] == "not a date"
    assert set(payload) == set(raw.columns)


def test_input_dataframe_is_not_modified():
    raw = make_dirty()
    before = raw.copy()
    clean(raw, "B-TEST-002")
    pd.testing.assert_frame_equal(raw, before)


@pytest.mark.parametrize("value,expected", [
    ("P1", "P1"), ("p2", "P2"), ("1 - Critical", "P1"), ("High", "P2"), ("2", "P2"),
    ("medium", "P3"), ("Low", "P4"), (" 4 ", "P4"), ("critical", "P1"),
    (None, None), ("ASAP", None), ("9", None), ("unknown", None)])
def test_priority_mapping(value, expected):
    assert standardize_priority(value) == expected


@pytest.mark.parametrize("value,expected", [
    ("netwrok", "Network"), ("NETWORK", "Network"), ("Network ", "Network"),
    ("  vpn ", "VPN"), ("e-mail", "Email"), ("Hadrware", "Hardware"),
    ("access/password", "Access/Password"), ("Coffee Machine", None), ("Parking", None)])
def test_category_mapping(value, expected):
    assert standardize_category(value) == expected


@pytest.mark.parametrize("value,expected", [
    ("RESOLVED", "Resolved"), ("closed ", "Closed"), ("in-progress", "In Progress"),
    ("In_Progress", "In Progress"), ("open", "Open"), ("banana", None)])
def test_status_mapping(value, expected):
    assert standardize_status(value) == expected


def test_three_date_formats_parse_to_the_same_timestamp():
    parsed, reformatted = parse_dates(pd.Series(
        ["2026-05-31 14:30:00", "05/31/2026 14:30", "31-May-2026 14:30", "2026-05-31T19:00:00+04:30",
         "not a date", None]))
    assert parsed.iloc[:4].nunique() == 1
    assert parsed.iloc[0] == pd.Timestamp("2026-05-31 14:30:00")
    assert parsed.iloc[4:].isna().all()
    assert reformatted.tolist() == [False, True, True, False, False, False]


def test_snake_case():
    assert snake_case("Ticket ID") == "ticket_id"
    assert snake_case("openedAt") == "opened_at"
    assert snake_case(" Resolution-Notes ") == "resolution_notes"


def test_derived_fields_and_sla():
    df = pd.DataFrame([
        {"Ticket ID": "T1", "opened_at": "2026-03-02 09:00:00", "resolved_at": "2026-03-02 15:00:00",
         "priority": "P1", "status": "Closed", "category": "VPN", "reopen_count": "-2"},
        {"Ticket ID": "T2", "opened_at": "2026-03-02 09:00:00", "resolved_at": "2026-03-02 12:00:00",
         "priority": "P1", "status": "Closed", "category": "VPN", "reopen_count": "1"},
        {"Ticket ID": "T3", "opened_at": "2026-03-01 00:00:00", "resolved_at": "",
         "priority": "P4", "status": "", "category": "VPN", "reopen_count": ""},
    ])
    cleaned, rejected, report = clean(df, "B-TEST-003", as_of="2026-03-05 00:00:00")
    assert rejected.empty
    t1, t2, t3 = (cleaned.set_index("ticket_id").loc[t] for t in ("T1", "T2", "T3"))
    assert t1["resolution_hours"] == 6.0 and t1["sla_breached"] == 1 and t1["reopen_count"] == 0
    assert t2["resolution_hours"] == 3.0 and t2["sla_breached"] == 0
    assert t1["opened_weekday"] == "Monday" and t1["opened_hour"] == 9 and t1["opened_month"] == "2026-03"
    assert t3["status"] == "Open" and t3["ticket_age_days"] == 4.0 and t3["sla_breached"] == 1
    assert report["fixed_by_rule"]["status_inferred"] == 1
    assert report["fixed_by_rule"]["reopen_count_set_to_zero"] == 2


def test_duplicates_keep_first_occurrence():
    row = {"ticket_id": "T9", "opened_at": "2026-03-02 09:00:00", "priority": "P3", "category": "Email"}
    df = pd.DataFrame([row, {**row, "priority": "P4"}])
    cleaned, rejected, _ = clean(df, "B-TEST-004")
    assert cleaned["priority"].tolist() == ["P3"]
    assert rejected["reason_code"].tolist() == ["DUPLICATE_ID"]

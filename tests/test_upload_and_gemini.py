"""CSV upload helper (safe names, validation, encodings) and the Gemini request hardening."""
import pytest

from src import config
from src.ai import llm_client
from src import ingest_files
from src.ingest_files import ingest_file, save_uploaded_csv

GOOD = b"Ticket ID,Opened At,Priority,Category\nINC1,2026-01-01 10:00:00,P1,Network\nINC2,2026-01-02,P3,VPN\n"


def test_upload_is_stored_under_a_safe_name_and_ingestible(tmp_path):
    info = save_uploaded_csv("../../My Tickets (final).csv", GOOD, tmp_path)
    assert info["path"] == tmp_path / "My_Tickets_final.csv"          # no path tricks, no odd characters
    assert (info["rows"], info["missing_required"]) == (2, [])
    assert "description" in info["missing_optional"]
    df, batch = ingest_file(info["path"], tmp_path / "t.db")
    assert len(df) == 2 and batch.startswith("B-")


def test_upload_never_overwrites_an_existing_raw_file(tmp_path):
    first = save_uploaded_csv("t.csv", GOOD, tmp_path)["path"]
    second = save_uploaded_csv("t.csv", GOOD + b"INC3,2026-01-03,P2,Email\n", tmp_path)["path"]
    assert (first.name, second.name) == ("t.csv", "t_2.csv")
    assert first.read_bytes() == GOOD


def test_missing_required_columns_are_reported(tmp_path):
    info = save_uploaded_csv("x.csv", b"id,when\n1,2\n", tmp_path)
    assert info["missing_required"] == ["ticket_id", "opened_at", "priority", "category"]


@pytest.mark.parametrize("name, data, message", [
    ("notes.txt", GOOD, "upload a .csv"),
    ("empty.csv", b"", "valid CSV"),
    ("headeronly.csv", b"ticket_id,opened_at\n", "no rows"),
    ("binary.csv", b"\x81\x8d\x8f\x90\x9d", "Could not read"),
])
def test_unusable_uploads_are_rejected_and_nothing_is_written(tmp_path, name, data, message):
    with pytest.raises(ValueError, match=message):
        save_uploaded_csv(name, data, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_excel_style_cp1252_and_bom_files_are_normalised_to_utf8(tmp_path):
    cp = "ticket_id,opened_at,priority,category,short_description\nINC1,2026-01-01,P1,Network,Café Wi-Fi down\n"
    info = save_uploaded_csv("excel.csv", cp.encode("cp1252"), tmp_path)
    assert "Café" in info["path"].read_text(encoding="utf-8")
    bom = save_uploaded_csv("bom.csv", b"\xef\xbb\xbf" + GOOD, tmp_path)
    assert bom["missing_required"] == []                             # BOM did not corrupt the first header


def test_oversized_upload_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_files, "MAX_UPLOAD_MB", 0)
    with pytest.raises(ValueError, match="larger than"):
        save_uploaded_csv("big.csv", GOOD, tmp_path)


# ------------------------------------------------------------------- Gemini
class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def _gemini_with(monkeypatch, model, bodies):
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json["generationConfig"])
        return FakeResponse(bodies[min(len(sent), len(bodies)) - 1])

    monkeypatch.setattr(llm_client.requests, "post", post)
    monkeypatch.setattr(config, "GEMINI_MODEL", model)
    return sent


def _ok(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_gemini_25_flash_runs_without_thinking_so_short_answers_are_not_cut_off(monkeypatch):
    sent = _gemini_with(monkeypatch, "gemini-2.5-flash", [_ok("hello")])
    assert llm_client._gemini("sys", "user", 0, 250) == "hello"
    assert sent[0]["thinkingConfig"] == {"thinkingBudget": 0} and sent[0]["maxOutputTokens"] == 250


def test_other_gemini_models_do_not_get_the_thinking_switch(monkeypatch):
    sent = _gemini_with(monkeypatch, "gemini-some-future-model", [_ok("hi")])
    llm_client._gemini("sys", "user", 0, 250)
    assert "thinkingConfig" not in sent[0]


def test_gemini_empty_reply_is_retried_once_with_a_bigger_budget(monkeypatch):
    empty = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"role": "model"}}]}  # no "parts"
    sent = _gemini_with(monkeypatch, "gemini-some-future-model", [empty, _ok("second try works")])
    assert llm_client._gemini("sys", "user", 0, 250) == "second try works"
    assert [s["maxOutputTokens"] for s in sent] == [250, 1000]


def test_gemini_still_empty_after_retry_raises_so_the_ollama_fallback_can_take_over(monkeypatch):
    empty = {"candidates": [{"content": {"role": "model"}}]}
    _gemini_with(monkeypatch, "gemini-some-future-model", [empty, empty])
    with pytest.raises(ValueError, match="empty reply"):
        llm_client._gemini("sys", "user", 0, 250)

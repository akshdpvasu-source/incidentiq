"""Phase 2 + 4 acceptance: ingestion creates one batch per run, the raw file is never
modified, the API paginates, and tickets + rejected_records reconcile with input_rows."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from src import api_server, ingest_api
from src.dirty_data import make_dirty
from src.generate_data import generate_tickets
from src.ingest_files import ingest_file
from src.load_db import connect, load_batch
from src.transform import clean


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """Mock API backed by a small temp CSV; requests.get is routed to FastAPI's TestClient."""
    source = tmp_path / "api_tickets.csv"
    generate_tickets(120, 7, "ServiceDesk-API", 500000).to_csv(source, index=False)
    monkeypatch.setattr(api_server, "SOURCE", source)
    api_server._tickets.cache_clear()
    client = TestClient(api_server.app)

    def fake_get(url, params=None, timeout=None):
        return client.get(url.replace(ingest_api.config.API_URL, ""), params=params)

    monkeypatch.setattr(ingest_api.requests, "get", fake_get)
    yield client
    api_server._tickets.cache_clear()


def test_api_health_and_pagination(api):
    assert api.get("/health").json() == {"status": "ok", "tickets_available": 120}
    body = api.get("/tickets", params={"page": 3, "page_size": 50}).json()
    assert (body["total"], body["total_pages"], len(body["tickets"])) == (120, 3, 20)
    since = api.get("/tickets", params={"since": "2026-07-01", "page_size": 1000}).json()
    assert all(t["opened_at"] >= "2026-07-01" for t in since["tickets"]) and since["total"] < 120
    assert api.get("/tickets", params={"since": "not-a-date"}).status_code == 400


def test_api_ingestion_creates_a_batch_per_run(api, tmp_path):
    db = tmp_path / "t.db"
    df1, b1 = ingest_api.ingest_api(page_size=25, db_path=db)
    df2, b2 = ingest_api.ingest_api(page_size=25, db_path=db)
    assert len(df1) == len(df2) == 120 and b1 != b2
    conn = connect(db)
    rows = conn.execute("SELECT source_type, input_rows, status FROM batches ORDER BY batch_id").fetchall()
    conn.close()
    assert rows == [("api", 120, "ingested"), ("api", 120, "ingested")]


def test_file_ingestion_never_modifies_raw_and_reconciles(tmp_path):
    raw_path, db = tmp_path / "incidents_dirty_test.csv", tmp_path / "t.db"
    make_dirty().to_csv(raw_path, index=False)
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    for _ in range(2):  # idempotent: re-running just creates a new batch
        df, batch = ingest_file(raw_path, db)
        cleaned, rejected, _ = clean(df, batch)
        load_batch(cleaned, rejected, batch, db)
    assert hashlib.sha256(raw_path.read_bytes()).hexdigest() == digest
    conn = connect(db)
    batches = conn.execute("SELECT batch_id, input_rows, loaded_rows, rejected_rows, status "
                           "FROM batches ORDER BY batch_id").fetchall()
    assert [b[1:] for b in batches] == [(200, 158, 42, "loaded")] * 2
    latest = batches[-1][0]
    in_db = conn.execute("SELECT (SELECT COUNT(*) FROM tickets WHERE batch_id = ?) + "
                         "(SELECT COUNT(*) FROM rejected_records WHERE batch_id = ?)",
                         (latest, latest)).fetchone()[0]
    assert in_db == 200                                   # tickets + rejected == input_rows
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 158   # upsert, no duplicates
    conn.close()
    with pytest.raises(ValueError, match="already loaded"):
        load_batch(cleaned, rejected, latest, db)


def test_failed_load_rolls_back_and_marks_batch(tmp_path):
    db = tmp_path / "t.db"
    raw = make_dirty()
    raw_path = tmp_path / "x.csv"
    raw.to_csv(raw_path, index=False)
    df, batch = ingest_file(raw_path, db)
    cleaned, rejected, _ = clean(df, batch)
    broken = cleaned.drop(columns=["opened_month"])        # makes the insert fail half-way
    with pytest.raises(KeyError):
        load_batch(broken, rejected, batch, db)
    conn = connect(db)
    assert conn.execute("SELECT status FROM batches WHERE batch_id = ?", (batch,)).fetchone()[0] == "failed"
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0     # dimensions rolled back too
    conn.close()

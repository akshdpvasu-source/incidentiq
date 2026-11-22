"""Phase 2 - mock ticket REST API (FastAPI).

Serves a *different* batch of ~2,000 synthetic tickets from
data/api_source/api_tickets.csv (created by generate_data.py, or on first
start if missing).

Run:   uvicorn src.api_server:app --port 8000
Try:   http://127.0.0.1:8000/tickets?since=2026-10-01&page=1&page_size=100
Docs:  http://127.0.0.1:8000/docs
"""
import math
from functools import lru_cache

import pandas as pd
from fastapi import FastAPI, HTTPException, Query

from src import config

app = FastAPI(title="IncidentIQ mock ticket API", version="1.0")
SOURCE = config.API_SOURCE_DIR / "api_tickets.csv"


@lru_cache(maxsize=1)
def _tickets():
    """Load (or create) the API's ticket store once."""
    if not SOURCE.exists():
        from src.generate_data import generate_tickets
        generate_tickets(config.N_API_TICKETS, config.SEED + 1, "ServiceDesk-API", 500000).to_csv(
            SOURCE, index=False)
    return pd.read_csv(SOURCE, dtype=str, keep_default_na=False)


@app.get("/health")
def health():
    return {"status": "ok", "tickets_available": len(_tickets())}


@app.get("/tickets")
def tickets(since: str | None = Query(None, description="Only tickets opened on/after YYYY-MM-DD"),
            page: int = Query(1, ge=1), page_size: int = Query(500, ge=1, le=1000)):
    df = _tickets()
    if since:
        try:
            since_ts = pd.Timestamp(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="since must look like YYYY-MM-DD")
        df = df[pd.to_datetime(df["opened_at"]) >= since_ts]
    total = len(df)
    total_pages = max(1, math.ceil(total / page_size))
    chunk = df.iloc[(page - 1) * page_size: page * page_size]
    records = [{k: (v if v != "" else None) for k, v in row.items()}
               for row in chunk.to_dict(orient="records")]
    return {"page": page, "page_size": page_size, "total": total,
            "total_pages": total_pages, "tickets": records}

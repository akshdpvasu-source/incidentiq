"""Phase 2 - ingest tickets from the mock REST API.

Pages through GET /tickets with a timeout and 3 retries (exponential
backoff: 1s, 2s). Records the batch with source_type='api'.

Start the API first:  uvicorn src.api_server:app --port 8000
Run:                  python src/ingest_api.py [--since 2026-01-01]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import requests

from src import config
from src.load_db import register_batch


class ApiUnavailableError(RuntimeError):
    """Raised when the API cannot be reached after all retries."""


def _get_with_retry(url, params):
    """GET with timeout + retries. Returns the requests.Response."""
    last_error = None
    for attempt in range(1, config.API_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=config.API_TIMEOUT)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < config.API_RETRIES:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s
    raise ApiUnavailableError(
        f"Could not reach {url} after {config.API_RETRIES} attempts ({last_error}). "
        "Start it with: uvicorn src.api_server:app --port 8000")


def api_is_up():
    """Quick health check (no retries) used by the dashboard."""
    try:
        return requests.get(f"{config.API_URL}/health", timeout=2).ok
    except requests.RequestException:
        return False


def ingest_api(since=None, page_size=config.API_PAGE_SIZE, db_path=None):
    """Fetch every page and register a batch. Returns (DataFrame, batch_id)."""
    url = f"{config.API_URL}/tickets"
    records, page, total_pages, size_bytes = [], 1, 1, 0
    while page <= total_pages:
        params = {"page": page, "page_size": page_size}
        if since:
            params["since"] = since
        response = _get_with_retry(url, params)
        body = response.json()
        records.extend(body["tickets"])
        total_pages, size_bytes = body["total_pages"], size_bytes + len(response.content)
        page += 1
    df = pd.DataFrame.from_records(records).astype(object)
    df = df.where(df.notna(), "")  # same convention as CSV ingestion: missing -> ""
    source = f"{url}?since={since}" if since else url
    batch_id = register_batch(source, "api", len(df), size_bytes, db_path)
    return df, batch_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pull tickets from the mock API")
    parser.add_argument("--since", help="only tickets opened on/after YYYY-MM-DD")
    args = parser.parse_args()
    frame, bid = ingest_api(args.since)
    print(f"Ingested {len(frame):,} rows from the API as batch {bid}")

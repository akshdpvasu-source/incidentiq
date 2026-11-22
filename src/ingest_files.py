"""Phase 2 - ingest a CSV file from data/raw/.

The raw file is only ever *read* (never modified). Every call creates a new
row in the `batches` table, so running it twice gives two batches.

Run:  python src/ingest_files.py [path/to/file.csv]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src import config
from src.load_db import register_batch


def list_raw_files():
    """All CSV files in data/raw, newest name first."""
    return sorted(config.RAW_DIR.glob("*.csv"), reverse=True)


def latest_main_file():
    """The newest generated incidents_YYYYMMDD.csv (skips the dirty test file)."""
    files = sorted(config.RAW_DIR.glob("incidents_[0-9]*.csv"))
    if not files:
        raise FileNotFoundError("No incidents_YYYYMMDD.csv in data/raw. Run: python src/generate_data.py")
    return files[-1]


def ingest_file(path=None, db_path=None):
    """Read a raw CSV as text and register a batch. Returns (DataFrame, batch_id)."""
    path = Path(path) if path else latest_main_file()
    # dtype=str + keep_default_na=False: keep every value exactly as written in the file
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    batch_id = register_batch(path.name, "file", len(df), path.stat().st_size, db_path)
    return df, batch_id


if __name__ == "__main__":
    frame, bid = ingest_file(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"Ingested {len(frame):,} rows as batch {bid}")

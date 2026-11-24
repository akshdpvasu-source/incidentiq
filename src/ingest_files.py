"""Phase 2 - ingest a CSV file from data/raw/.

The raw file is only ever *read* (never modified). Every call creates a new
row in the `batches` table, so running it twice gives two batches.

Run:  python src/ingest_files.py [path/to/file.csv]
"""
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src import config
from src.load_db import register_batch
from src.transform import EXPECTED_COLUMNS, snake_case

MAX_UPLOAD_MB = 100
# Columns without which every row would be rejected; others are optional (filled with blanks).
REQUIRED_UPLOAD_COLUMNS = ["ticket_id", "opened_at", "priority", "category"]


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


def save_uploaded_csv(filename, data, raw_dir=None):
    """Validate an uploaded CSV and store it in data/raw/ under a safe, unique name.

    Returns {"path", "rows", "missing_required", "missing_optional"}. Raises ValueError for a
    file that is not a usable CSV. The file is only stored, not ingested; the normal
    ingest -> clean -> load steps then run on it like on any generated file.
    """
    raw_dir = Path(raw_dir or config.RAW_DIR)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"File is larger than {MAX_UPLOAD_MB} MB.")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(str(filename)).stem).strip("._") or "uploaded"
    if not str(filename).lower().endswith(".csv"):
        raise ValueError("Please upload a .csv file.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:  # typical for CSVs saved from Excel
        try:
            text = data.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise ValueError("Could not read the file as text (expected UTF-8 or Windows-1252).") from exc
    try:
        frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ValueError(f"This does not look like a valid CSV file: {exc}") from exc
    if frame.empty:
        raise ValueError("The CSV has a header but no rows.")
    present = {snake_case(c) for c in frame.columns}
    missing_required = [c for c in REQUIRED_UPLOAD_COLUMNS if c not in present]
    missing_optional = [c for c in EXPECTED_COLUMNS if c not in present and c not in missing_required]
    path, n = raw_dir / f"{stem}.csv", 1
    while path.exists():  # never overwrite: raw files are never modified
        n += 1
        path = raw_dir / f"{stem}_{n}.csv"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return {"path": path, "rows": len(frame), "missing_required": missing_required,
            "missing_optional": missing_optional}


if __name__ == "__main__":
    frame, bid = ingest_file(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"Ingested {len(frame):,} rows as batch {bid}")

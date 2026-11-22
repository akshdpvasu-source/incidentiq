"""Run the whole pipeline from the command line: ingest -> clean -> load (-> S3).

Examples:
  python src/pipeline.py                 # latest main CSV + dirty test file + API (if running)
  python src/pipeline.py --file data/raw/incidents_dirty_test.csv
  python src/pipeline.py --api --since 2026-06-01
  python src/pipeline.py --upload        # also copy outputs to S3 (if configured)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config
from src.ingest_api import ApiUnavailableError, api_is_up, ingest_api
from src.ingest_files import ingest_file, latest_main_file
from src.load_db import load_batch
from src.transform import clean, print_report, save_outputs


def process(df, batch_id, db_path=None, upload=False, raw_path=None):
    """Clean, save and load one ingested batch. Returns the cleaning report."""
    cleaned, rejected, report = clean(df, batch_id)
    paths = save_outputs(cleaned, rejected, report)
    load_batch(cleaned, rejected, batch_id, db_path)
    if upload:
        from src.cloud_s3 import upload_file
        if raw_path:
            upload_file(raw_path, "raw/")
        upload_file(paths["cleaned"], "processed/")
        upload_file(paths["rejected"], "rejected/")
        upload_file(paths["report"], "reports/")
    return report


def run_file(path=None, db_path=None, upload=False):
    path = Path(path) if path else latest_main_file()
    df, batch_id = ingest_file(path, db_path)
    return process(df, batch_id, db_path, upload, raw_path=path)


def run_api(since=None, db_path=None, upload=False):
    df, batch_id = ingest_api(since, db_path=db_path)
    return process(df, batch_id, db_path, upload)


def main():
    parser = argparse.ArgumentParser(description="IncidentIQ pipeline")
    parser.add_argument("--file", help="CSV file to ingest")
    parser.add_argument("--api", action="store_true", help="ingest from the mock API")
    parser.add_argument("--since", help="API filter: opened on/after YYYY-MM-DD")
    parser.add_argument("--upload", action="store_true", help="upload outputs to S3 if configured")
    args = parser.parse_args()

    reports = []
    if args.file:
        reports.append(run_file(args.file, upload=args.upload))
    if args.api:
        reports.append(run_api(args.since, upload=args.upload))
    if not args.file and not args.api:  # default: everything available
        reports.append(run_file(upload=args.upload))
        dirty = config.RAW_DIR / "incidents_dirty_test.csv"
        if dirty.exists():
            reports.append(run_file(dirty, upload=args.upload))
        if api_is_up():
            reports.append(run_api(upload=args.upload))
        else:
            print("Mock API not running - skipped. Start it with: uvicorn src.api_server:app --port 8000")
    for report in reports:
        print_report(report)
    print(f"\nDatabase: {config.DB_PATH}")


if __name__ == "__main__":
    try:
        main()
    except (ApiUnavailableError, FileNotFoundError) as exc:
        sys.exit(f"ERROR: {exc}")

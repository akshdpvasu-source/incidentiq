"""Phase 8 (optional) - tiny AWS Lambda triggered by an S3 upload.

For each uploaded object it logs the file name, size, CSV header and row
count to CloudWatch. It streams the file and only counts line breaks (no
pandas, no pipeline). Deploy steps: docs/aws_setup.md.

Local test:  python lambda/handler.py data/raw/incidents_dirty_test.csv
"""
import json
import logging
import urllib.parse

logger = logging.getLogger()
logger.setLevel(logging.INFO)
MAX_BYTES = 50 * 1024 * 1024  # skip counting very large files


def summarize_csv_stream(chunks):
    """Return (header_columns, data_row_count) from an iterable of byte chunks."""
    header, newlines, first, last_byte = None, 0, b"", b"\n"
    for chunk in chunks:
        if not chunk:
            continue
        if header is None:
            first += chunk
            if b"\n" in first:
                header = first.split(b"\n", 1)[0].decode("utf-8-sig").strip().split(",")
        newlines += chunk.count(b"\n")
        last_byte = chunk[-1:]
    if header is None and first:
        header = first.decode("utf-8-sig").strip().split(",")
    lines = newlines + (0 if last_byte == b"\n" else 1)  # last line may lack "\n"
    return header or [], max(lines - 1, 0)               # minus the header line


def lambda_handler(event, context, s3_client=None):
    """Entry point configured in Lambda as handler.lambda_handler."""
    if s3_client is None:
        import boto3  # available in the Lambda Python runtime
        s3_client = boto3.client("s3")
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        size = int(record["s3"]["object"].get("size", 0))
        info = {"bucket": bucket, "key": key, "size_bytes": size}
        if key.lower().endswith(".csv") and size <= MAX_BYTES:
            body = s3_client.get_object(Bucket=bucket, Key=key)["Body"]
            header, rows = summarize_csv_stream(body.iter_chunks(chunk_size=1024 * 1024))
            info.update(columns=len(header), header=header[:20], row_count=rows)
        logger.info(json.dumps(info))
        results.append(info)
    return {"statusCode": 200, "processed": results}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    with open(sys.argv[1], "rb") as f:
        cols, n = summarize_csv_stream(iter(lambda: f.read(65536), b""))
    print(json.dumps({"file": sys.argv[1], "columns": len(cols), "row_count": n}))

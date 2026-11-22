"""Optional cloud parts: the Lambda handler (with a fake S3 client) and S3-disabled behaviour."""
import importlib.util
import io
from pathlib import Path

from src import cloud_s3, config

_spec = importlib.util.spec_from_file_location("handler", Path(__file__).parents[1] / "lambda" / "handler.py")
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)

CSV = b"ticket_id,priority\nINC1,P1\nINC2,P3\nINC3,P4"


class FakeBody(io.BytesIO):
    def iter_chunks(self, chunk_size):
        return iter(lambda: self.read(4), b"")  # tiny chunks to test line counting across chunks


class FakeS3:
    def get_object(self, Bucket, Key):
        return {"Body": FakeBody(CSV)}


def test_summarize_csv_stream_counts_rows():
    assert handler.summarize_csv_stream([CSV]) == (["ticket_id", "priority"], 3)
    assert handler.summarize_csv_stream([CSV + b"\n"]) == (["ticket_id", "priority"], 3)
    assert handler.summarize_csv_stream([]) == ([], 0)


def test_lambda_handler_logs_file_info():
    event = {"Records": [{"s3": {"bucket": {"name": "demo-bucket"},
                                 "object": {"key": "raw/incidents+2026.csv", "size": len(CSV)}}}]}
    out = handler.lambda_handler(event, None, s3_client=FakeS3())
    info = out["processed"][0]
    assert info["key"] == "raw/incidents 2026.csv"
    assert (info["row_count"], info["columns"], info["size_bytes"]) == (3, 2, len(CSV))


def test_s3_disabled_without_bucket(monkeypatch):
    monkeypatch.setattr(config, "S3_BUCKET", "")
    assert cloud_s3.s3_status()["enabled"] is False
    result = cloud_s3.upload_file(config.RAW_DIR / "x.csv", "raw/")
    assert result["ok"] is False and "S3 disabled" in result["message"]
    assert cloud_s3.list_objects() == []

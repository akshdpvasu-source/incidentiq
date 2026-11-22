"""Phase 8 (optional) - upload pipeline files to Amazon S3.

    upload_file("data/cleaned/B-20260101-001_cleaned.csv", "processed/")

Credentials come from .env (AWS_PROFILE / standard AWS_* variables) or the
AWS CLI profile. If boto3, credentials or S3_BUCKET are missing, every
function returns a clear "S3 disabled" message and the app keeps working.
Only files inside the project's data/ folder (synthetic data) can be uploaded.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config

PREFIXES = ("raw/", "processed/", "rejected/", "reports/")
_warned = False


def _session():
    import boto3  # imported lazily so the app runs without boto3 installed
    return boto3.Session(profile_name=config.AWS_PROFILE or None, region_name=config.AWS_REGION)


def s3_status():
    """{"enabled": bool, "reason": str, "bucket": str, "region": str}"""
    status = {"enabled": False, "bucket": config.S3_BUCKET, "region": config.AWS_REGION}
    if not config.S3_BUCKET:
        return {**status, "reason": "S3 disabled: set S3_BUCKET in .env"}
    try:
        if _session().get_credentials() is None:
            return {**status, "reason": "S3 disabled: no AWS credentials found (.env or `aws configure`)"}
    except ImportError:
        return {**status, "reason": "S3 disabled: boto3 is not installed"}
    except Exception as exc:  # e.g. unknown profile name
        return {**status, "reason": f"S3 disabled: {exc}"}
    return {**status, "enabled": True, "reason": "S3 enabled"}


def console_url(key):
    return (f"https://s3.console.aws.amazon.com/s3/object/{config.S3_BUCKET}"
            f"?region={config.AWS_REGION}&prefix={key}")


def upload_file(local_path, prefix):
    """Upload one file under raw/, processed/, rejected/ or reports/."""
    global _warned
    status = s3_status()
    if not status["enabled"]:
        if not _warned:
            print(status["reason"])
            _warned = True
        return {"ok": False, "message": status["reason"]}
    if prefix not in PREFIXES:
        return {"ok": False, "message": f"Prefix must be one of {PREFIXES}"}
    path = Path(local_path).resolve()
    if not path.is_file() or not path.is_relative_to(config.DATA_DIR.resolve()):
        return {"ok": False, "message": "Only synthetic files inside data/ can be uploaded"}
    key = prefix + path.name
    try:
        _session().client("s3").upload_file(str(path), config.S3_BUCKET, key)
    except Exception as exc:
        return {"ok": False, "message": f"Upload failed: {exc}"}
    return {"ok": True, "message": f"Uploaded s3://{config.S3_BUCKET}/{key}",
            "s3_uri": f"s3://{config.S3_BUCKET}/{key}", "console_url": console_url(key)}


def list_objects(prefix="", max_keys=50):
    """Recent objects in the bucket (empty list when S3 is disabled)."""
    if not s3_status()["enabled"]:
        return []
    response = _session().client("s3").list_objects_v2(Bucket=config.S3_BUCKET, Prefix=prefix,
                                                      MaxKeys=max_keys)
    return [{"key": o["Key"], "size_bytes": o["Size"], "last_modified": str(o["LastModified"])[:19],
             "console_url": console_url(o["Key"])} for o in response.get("Contents", [])]


if __name__ == "__main__":
    print(s3_status()["reason"])
    if len(sys.argv) == 3:
        print(upload_file(sys.argv[1], sys.argv[2])["message"])

"""Lambda entry point for the scheduled monthly rollup (Req 1).

Fires on the 3rd of each month at 03:00 UTC and rolls up the immediately
preceding calendar month (Req 1.2). Any failure raises, so the Lambda
Errors metric (and its alarm) reports it and EventBridge retries up to
twice — safe, because re-runs are idempotent replaces.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3

from src.analytics_rollup import aggregate, topics
from src.analytics_rollup.rollup import (
    SCAN_WARN_BYTES,
    AthenaRunner,
    ensure_tables,
    run_month,
)

CATALOG_KEY = "database/announcements.csv"


def previous_month(now: datetime) -> str:
    """Calendar month immediately preceding the invocation instant, UTC."""
    year, month = now.year, now.month
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def handler(event, context):
    logs_bucket = os.environ.get("LOGS_BUCKET_NAME", "")
    data_bucket = os.environ.get("DATA_BUCKET_NAME", "")
    if not logs_bucket or not data_bucket:
        # Req 3.7: terminate before writing anything.
        raise RuntimeError("LOGS_BUCKET_NAME / DATA_BUCKET_NAME not configured")

    now = datetime.now(timezone.utc)
    target_month = previous_month(now)

    s3 = boto3.client("s3")
    athena = boto3.client("athena")
    runner = AthenaRunner(athena, f"s3://{logs_bucket}/athena-results/")

    # Announcement_Catalog: read once per invocation (Req 9.9, 13.12).
    try:
        catalog_text = s3.get_object(Bucket=data_bucket, Key=CATALOG_KEY)["Body"].read().decode("utf-8")
        entries = topics.load_catalog(catalog_text)
    except Exception as exc:
        print(json.dumps({"component": "analytics_rollup", "outcome": "failure",
                          "error": f"cannot read catalog {CATALOG_KEY}: {exc}"}))
        raise
    slug_index = topics.build_slug_index(entries)

    ensure_tables(runner, logs_bucket)
    result = run_month(target_month, runner=runner, s3=s3, logs_bucket=logs_bucket,
                       slug_index=slug_index, now=now)

    if runner.bytes_scanned > SCAN_WARN_BYTES:
        print(json.dumps({"component": "analytics_rollup", "event": "scan_ceiling_exceeded",
                          "bytes_scanned": runner.bytes_scanned,
                          "queries_issued": runner.queries_issued}))

    if not result.ok:
        print(json.dumps({"component": "analytics_rollup", "target_month": target_month,
                          "outcome": "failure", "error": result.error}))
        raise RuntimeError(f"rollup failed for {target_month}: {result.error}")

    all_time_key, _ = aggregate.run_aggregate(s3, logs_bucket)
    return {
        "target_month": target_month,
        "coverage_status": result.coverage_status,
        "keys_written": result.keys_written + [all_time_key],
    }

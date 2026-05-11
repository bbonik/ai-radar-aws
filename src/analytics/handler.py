"""Analytics event collector Lambda.

Receives client-side events via API Gateway POST and writes them
to S3 as JSONL files (one file per invocation to avoid write conflicts).
"""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3


def handler(event, context):
    """Handle analytics event from API Gateway."""
    # Parse the request body
    try:
        if isinstance(event.get("body"), str):
            body = json.loads(event["body"])
        else:
            body = event.get("body", {})
    except (json.JSONDecodeError, TypeError):
        return {"statusCode": 400, "body": "Invalid JSON"}

    # Extract events (can be a single event or batch)
    events = body.get("events", [body]) if "events" in body else [body]

    # Enrich each event with server-side metadata
    now = datetime.now(timezone.utc)
    date_prefix = now.strftime("%Y-%m-%d")
    source_ip = (
        event.get("requestContext", {}).get("identity", {}).get("sourceIp", "unknown")
    )
    user_agent = event.get("headers", {}).get(
        "User-Agent", event.get("headers", {}).get("user-agent", "unknown")
    )

    enriched_events = []
    for evt in events:
        enriched = {
            "event_type": evt.get("event_type", "unknown"),
            "path": evt.get("path", ""),
            "report_slug": evt.get("report_slug", ""),
            "tag": evt.get("tag", ""),
            "dimension": evt.get("dimension", ""),
            "session_id": evt.get("session_id", ""),
            "timestamp": evt.get("timestamp", now.isoformat()),
            "server_timestamp": now.isoformat(),
            "source_ip": source_ip,
            "user_agent": user_agent,
        }
        enriched_events.append(enriched)

    # Write to S3 as JSONL (one file per invocation)
    bucket = os.environ.get("DATA_BUCKET_NAME", "")
    if not bucket:
        return {"statusCode": 500, "body": "DATA_BUCKET_NAME not configured"}

    file_id = uuid.uuid4().hex[:12]
    key = f"analytics/events/{date_prefix}/{file_id}.jsonl"

    jsonl_content = "\n".join(json.dumps(e) for e in enriched_events)

    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=jsonl_content.encode("utf-8"),
        ContentType="application/jsonl",
    )

    return {
        "statusCode": 200,
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps({"status": "ok", "events_recorded": len(enriched_events)}),
    }

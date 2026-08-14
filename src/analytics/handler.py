"""Analytics event collector Lambda.

Receives client-side events via API Gateway POST and writes them
to S3 as JSONL files (one file per invocation to avoid write conflicts).

Security hardening:
- Validates event_type against an allowlist
- Rejects payloads exceeding MAX_BODY_SIZE (1KB)
- Filters out invalid events before writing to S3
- Truncates client IPs at ingest (IPv4 /24, IPv6 /48) so 90-day raw
  retention is not 90 days of device identifiers
  (docs/audit-remediation-plan.md item 12, decision D6)
"""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

# Security: maximum request body size (1KB)
MAX_BODY_SIZE = 1024


def truncate_ip(ip: str) -> str:
    """De-identify a client IP at ingest.

    IPv4 keeps the /24 (drops the host octet); IPv6 keeps the /48 (first
    three hextets). Preserves geographic and coarse-uniqueness signal while
    ceasing to store a per-device identifier. Anything unparseable is
    replaced with "unknown" rather than stored raw.
    """
    if not ip or ip == "unknown":
        return "unknown"
    if ":" in ip:  # IPv6
        parts = ip.split(":")
        if len(parts) >= 3:
            return ":".join(parts[:3]) + "::/48"
        return "unknown"
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + ".0/24"
    return "unknown"

# Security: allowlist of valid event types
VALID_EVENT_TYPES = frozenset({
    "pageview",
    "report_click",
    "filter_tag",
    "filter_time",
    "pdf_export",
    "about_open",
    "sort_change",
})


def _cors_headers() -> dict:
    """CORS response headers, honouring the deployment's configured origin.

    ALLOWED_ORIGIN is set by the CDK stack from the custom_domain context;
    absent configuration falls back to "*" (the endpoint is publicly
    writable by design — throttling and validation are the controls).
    Previously hardcoded "*", contradicting the API-level CORS config.
    """
    return {
        "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def handler(event, context):
    """Handle analytics event from API Gateway."""
    # Security: reject oversized payloads
    raw_body = event.get("body", "")
    if isinstance(raw_body, str) and len(raw_body) > MAX_BODY_SIZE:
        return {"statusCode": 413, "body": "Payload too large"}

    # Parse the request body
    try:
        if isinstance(raw_body, str):
            body = json.loads(raw_body)
        else:
            body = raw_body if raw_body else {}
    except (json.JSONDecodeError, TypeError):
        return {"statusCode": 400, "body": "Invalid JSON"}

    # Extract events (can be a single event or batch)
    events = body.get("events", [body]) if "events" in body else [body]

    # Enrich each event with server-side metadata
    now = datetime.now(timezone.utc)
    date_prefix = now.strftime("%Y-%m-%d")
    source_ip = truncate_ip(
        event.get("requestContext", {}).get("identity", {}).get("sourceIp", "unknown")
    )
    user_agent = event.get("headers", {}).get(
        "User-Agent", event.get("headers", {}).get("user-agent", "unknown")
    )

    enriched_events = []
    for evt in events:
        # Security: validate event_type against allowlist
        event_type = evt.get("event_type", "")
        if event_type not in VALID_EVENT_TYPES:
            continue  # Skip invalid event types silently

        enriched = {
            "event_type": event_type,
            "path": evt.get("path", "")[:500],  # Limit field lengths
            "report_slug": evt.get("report_slug", "")[:200],
            "tag": evt.get("tag", "")[:100],
            "dimension": evt.get("dimension", "")[:50],
            "session_id": evt.get("session_id", "")[:64],
            "timestamp": evt.get("timestamp", now.isoformat())[:50],
            "server_timestamp": now.isoformat(),
            "source_ip": source_ip,
            "user_agent": user_agent[:500],  # Limit UA length
        }
        enriched_events.append(enriched)

    # If all events were filtered out (invalid types), return success but record nothing
    if not enriched_events:
        return {
            "statusCode": 200,
            "headers": _cors_headers(),
            "body": json.dumps({"status": "ok", "events_recorded": 0}),
        }

    # Write to S3 as JSONL (one file per invocation)
    bucket = os.environ.get("LOGS_BUCKET_NAME", "")
    if not bucket:
        return {"statusCode": 500, "body": "LOGS_BUCKET_NAME not configured"}

    file_id = uuid.uuid4().hex[:12]
    key = f"events/{date_prefix}/{file_id}.jsonl"

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
        "headers": _cors_headers(),
        "body": json.dumps({"status": "ok", "events_recorded": len(enriched_events)}),
    }

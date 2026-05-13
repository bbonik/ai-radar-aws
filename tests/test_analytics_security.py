"""Tests for analytics handler security hardening (P1)."""
import json
import os
import sys

import pytest

sys.path.insert(0, ".")
from src.analytics.handler import handler, VALID_EVENT_TYPES, MAX_BODY_SIZE


@pytest.fixture(autouse=True)
def clear_env():
    """Ensure LOGS_BUCKET_NAME is unset for validation-only tests."""
    os.environ.pop("LOGS_BUCKET_NAME", None)
    yield
    os.environ.pop("LOGS_BUCKET_NAME", None)


def _make_event(body_dict):
    return {
        "body": json.dumps(body_dict),
        "requestContext": {"identity": {"sourceIp": "1.2.3.4"}},
        "headers": {"User-Agent": "test-agent"},
    }


def test_valid_event_passes_validation():
    """Valid event_type should pass validation (fails on missing bucket, not validation)."""
    event = _make_event({"event_type": "pageview", "path": "/index.html"})
    result = handler(event, None)
    # No bucket configured -> 500, but it got past validation
    assert result["statusCode"] == 500


def test_invalid_event_type_filtered():
    """Invalid event_type should be silently filtered, returning 0 events."""
    os.environ["LOGS_BUCKET_NAME"] = ""
    event = _make_event({"event_type": "hacker_event", "path": "/evil"})
    result = handler(event, None)
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["events_recorded"] == 0


def test_oversized_payload_rejected():
    """Payloads exceeding MAX_BODY_SIZE should be rejected with 413."""
    big_payload = "x" * (MAX_BODY_SIZE + 1)
    event = {
        "body": big_payload,
        "requestContext": {"identity": {"sourceIp": "1.2.3.4"}},
        "headers": {"User-Agent": "test-agent"},
    }
    result = handler(event, None)
    assert result["statusCode"] == 413


def test_invalid_json_rejected():
    """Malformed JSON should return 400."""
    event = {
        "body": "not valid json{{{",
        "requestContext": {"identity": {"sourceIp": "1.2.3.4"}},
        "headers": {"User-Agent": "test-agent"},
    }
    result = handler(event, None)
    assert result["statusCode"] == 400


def test_all_valid_event_types_accepted():
    """All event types in the allowlist should pass validation."""
    for event_type in VALID_EVENT_TYPES:
        event = _make_event({"event_type": event_type})
        result = handler(event, None)
        # Should fail on missing bucket (500), not on validation
        assert result["statusCode"] == 500, f"Failed for event_type={event_type}"


def test_field_length_truncation():
    """Fields should be truncated to prevent storage abuse."""
    os.environ["LOGS_BUCKET_NAME"] = ""
    # Use a path that's long but keeps total payload under 1KB
    long_path = "/" + "a" * 400
    event = _make_event({"event_type": "pageview", "path": long_path})
    result = handler(event, None)
    # Should fail on empty bucket, not crash on long fields
    assert result["statusCode"] == 500

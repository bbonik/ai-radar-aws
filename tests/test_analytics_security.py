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


# --- Item 12: de-identification and origin echo (docs/audit-remediation-plan.md) ---

from src.analytics.handler import truncate_ip


class TestIpTruncation:
    """Client IPs are de-identified at ingest (D6): IPv4 /24, IPv6 /48."""

    def test_ipv4_drops_host_octet(self):
        assert truncate_ip("203.0.113.77") == "203.0.113.0/24"

    def test_ipv6_keeps_first_three_hextets(self):
        assert truncate_ip("2001:db8:85a3:8d3:1319:8a2e:370:7348") == "2001:db8:85a3::/48"

    def test_unknown_and_garbage_never_stored_raw(self):
        assert truncate_ip("unknown") == "unknown"
        assert truncate_ip("") == "unknown"
        assert truncate_ip("not-an-ip") == "unknown"
        assert truncate_ip("1.2.3") == "unknown"

    def test_stored_event_contains_truncated_ip_only(self, monkeypatch):
        """End to end: the JSONL written to S3 must not hold the full IP."""
        import src.analytics.handler as h

        written = {}

        class FakeS3:
            def put_object(self, **kwargs):
                written.update(kwargs)

        monkeypatch.setenv("LOGS_BUCKET_NAME", "test-bucket")
        monkeypatch.setattr(h.boto3, "client", lambda *_a, **_k: FakeS3())

        event = _make_event({"event_type": "pageview", "path": "/index.html"})
        result = handler(event, None)

        assert result["statusCode"] == 200
        record = json.loads(written["Body"].decode())
        assert record["source_ip"] == "1.2.3.0/24"
        assert "1.2.3.4" not in written["Body"].decode()


class TestCorsOriginEcho:
    """ACAO honours ALLOWED_ORIGIN instead of hardcoding '*' (item 12)."""

    def test_configured_origin_is_echoed(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ORIGIN", "https://news.example.com")
        monkeypatch.setenv("LOGS_BUCKET_NAME", "")
        event = _make_event({"event_type": "invalid_type"})
        result = handler(event, None)
        assert result["headers"]["Access-Control-Allow-Origin"] == "https://news.example.com"

    def test_absent_config_falls_back_to_wildcard(self, monkeypatch):
        monkeypatch.delenv("ALLOWED_ORIGIN", raising=False)
        monkeypatch.setenv("LOGS_BUCKET_NAME", "")
        event = _make_event({"event_type": "invalid_type"})
        result = handler(event, None)
        assert result["headers"]["Access-Control-Allow-Origin"] == "*"

"""Unit tests for StructuredLogger."""

import json
from unittest.mock import patch
from datetime import datetime, timezone

from src.shared.logger import StructuredLogger


class TestStructuredLogger:
    """Tests for StructuredLogger JSON output."""

    def setup_method(self):
        self.run_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        self.lambda_name = "report-pipeline"
        self.logger = StructuredLogger(lambda_name=self.lambda_name, run_id=self.run_id)

    def _capture_log(self, method, message, **kwargs):
        """Call a log method and return the parsed JSON output."""
        with patch("builtins.print") as mock_print:
            method(message, **kwargs)
            output = mock_print.call_args[0][0]
            return json.loads(output)

    def test_info_contains_required_fields(self):
        entry = self._capture_log(self.logger.info, "Pipeline started")

        assert entry["run_id"] == self.run_id
        assert entry["lambda_name"] == self.lambda_name
        assert entry["level"] == "INFO"
        assert entry["message"] == "Pipeline started"
        assert "timestamp" in entry

    def test_warning_level(self):
        entry = self._capture_log(self.logger.warning, "Slow response")

        assert entry["level"] == "WARNING"
        assert entry["message"] == "Slow response"

    def test_error_level(self):
        entry = self._capture_log(self.logger.error, "Request failed")

        assert entry["level"] == "ERROR"
        assert entry["message"] == "Request failed"

    def test_kwargs_included_in_output(self):
        entry = self._capture_log(
            self.logger.error,
            "Report generation failed",
            announcement_link="https://aws.amazon.com/whats-new/example",
            stage="report_generation",
            error_type="ThrottlingException",
        )

        assert entry["announcement_link"] == "https://aws.amazon.com/whats-new/example"
        assert entry["stage"] == "report_generation"
        assert entry["error_type"] == "ThrottlingException"

    def test_timestamp_is_iso8601_utc(self):
        entry = self._capture_log(self.logger.info, "test")

        # Should end with Z and be parseable as ISO 8601
        assert entry["timestamp"].endswith("Z")
        # Verify it parses correctly
        ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
        assert ts.tzinfo is not None

    def test_correlation_id_consistent_across_calls(self):
        entries = []
        with patch("builtins.print") as mock_print:
            self.logger.info("first")
            self.logger.warning("second")
            self.logger.error("third")

            for call in mock_print.call_args_list:
                entries.append(json.loads(call[0][0]))

        # All entries share the same run_id
        assert all(e["run_id"] == self.run_id for e in entries)
        assert all(e["lambda_name"] == self.lambda_name for e in entries)

    def test_output_is_valid_json(self):
        with patch("builtins.print") as mock_print:
            self.logger.info("test", nested={"key": "value"}, count=42)
            output = mock_print.call_args[0][0]

        # Should not raise
        parsed = json.loads(output)
        assert parsed["nested"] == {"key": "value"}
        assert parsed["count"] == 42

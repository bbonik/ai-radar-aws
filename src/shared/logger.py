"""Structured JSON logging for CloudWatch ingestion."""

import json
import sys
from datetime import datetime, timezone


class StructuredLogger:
    """Outputs JSON-formatted log entries to stdout for CloudWatch.

    Every log entry automatically includes:
    - run_id: Correlation ID for the entire pipeline run (UUID)
    - lambda_name: Identifies which Lambda produced the log
    - timestamp: ISO 8601 UTC timestamp
    - level: INFO, WARNING, or ERROR
    """

    def __init__(self, lambda_name: str, run_id: str) -> None:
        self.lambda_name = lambda_name
        self.run_id = run_id

    def _log(self, level: str, message: str, **kwargs) -> None:
        """Build and emit a structured log entry."""
        entry = {
            "run_id": self.run_id,
            "lambda_name": self.lambda_name,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": level,
            "message": message,
        }
        entry.update(kwargs)
        print(json.dumps(entry), flush=True)

    def info(self, message: str, **kwargs) -> None:
        """Log an INFO-level message."""
        self._log("INFO", message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        """Log a WARNING-level message."""
        self._log("WARNING", message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        """Log an ERROR-level message."""
        self._log("ERROR", message, **kwargs)

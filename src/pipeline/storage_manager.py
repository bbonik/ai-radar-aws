"""Storage Manager module for the AI Radar AWS pipeline.

Handles persistence of announcement data and error records to S3 as CSV files.
Provides deduplication by loading existing announcement links from storage.
All S3 writes use server-side encryption (AES-256) and retry with exponential backoff.
"""

import csv
import io
import time

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import AnnouncementError, ProcessedAnnouncement

# CSV column headers for the announcements file
ANNOUNCEMENT_CSV_COLUMNS = [
    "title",
    "description",
    "pub_date",
    "link",
    "aws_service",
    "importance_level",
    "importance_score",
    "whats_new",
    "how_it_works",
    "why_important",
    "how_different",
    "when_to_prefer",
    "availability",
    "mermaid_graph",
    "blogpost_links",
    "first_detected",
    "tags",
]

# CSV column headers for the error records file
ERROR_CSV_COLUMNS = [
    "link",
    "title",
    "stage",
    "error_type",
    "error_message",
    "timestamp",
    "run_id",
]

# S3 key paths
ANNOUNCEMENTS_KEY = "database/announcements.csv"
ERRORS_KEY = "errors/failed_announcements.csv"

# Retry configuration
MAX_RETRIES = 3


class StorageManager:
    """Manages persistence of announcement data and error records to S3.

    CSV stored at s3://{data_bucket}/database/announcements.csv
    Error records at s3://{data_bucket}/errors/failed_announcements.csv

    Uses announcement link as unique key for deduplication.
    All S3 uploads use ServerSideEncryption='AES256'.
    Retries S3 writes up to 3 times with exponential backoff.
    """

    def __init__(self, config: Config, s3_client, logger: StructuredLogger, data_bucket: str) -> None:
        self._config = config
        self._s3 = s3_client
        self._logger = logger
        self._data_bucket = data_bucket

    def load_existing_links(self) -> set[str]:
        """Load all previously stored announcement links from S3 for deduplication.

        Returns a set of announcement link URLs. If the CSV file does not exist
        yet (first run), returns an empty set.
        """
        try:
            response = self._s3.get_object(
                Bucket=self._data_bucket,
                Key=ANNOUNCEMENTS_KEY,
            )
            csv_content = response["Body"].read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(csv_content))
            links = {row["link"] for row in reader if row.get("link")}
            self._logger.info(
                "Loaded existing announcement links for deduplication",
                links_count=len(links),
            )
            return links
        except self._s3.exceptions.NoSuchKey:
            self._logger.info(
                "No existing announcements CSV found, starting fresh",
            )
            return set()
        except Exception as exc:
            # Handle the case where the bucket/key doesn't exist yet
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if error_code in ("NoSuchKey", "404"):
                self._logger.info(
                    "No existing announcements CSV found, starting fresh",
                )
                return set()
            self._logger.error(
                "Failed to load existing links from S3",
                error_type=type(exc).__name__,
                error_message=str(exc),
                bucket=self._data_bucket,
                key=ANNOUNCEMENTS_KEY,
            )
            raise

    def save_announcement(self, announcement: ProcessedAnnouncement) -> bool:
        """Append a new announcement row to the CSV in S3.

        Downloads the existing CSV (if any), appends the new row, and uploads
        the updated file. Never overwrites existing data — only appends.

        Returns True on success, False on failure after all retries.
        """
        csv_row = announcement.to_csv_row()

        for attempt in range(MAX_RETRIES + 1):
            try:
                # Load existing CSV content (or start with headers only)
                existing_content = self._load_csv_content(ANNOUNCEMENTS_KEY)

                # Append the new row
                updated_content = self._append_row_to_csv(
                    existing_content, csv_row, ANNOUNCEMENT_CSV_COLUMNS
                )

                # Upload the updated CSV
                self._upload_csv(ANNOUNCEMENTS_KEY, updated_content)

                self._logger.info(
                    "Announcement saved to S3",
                    announcement_link=announcement.link,
                    announcement_title=announcement.title,
                )
                return True

            except Exception as exc:
                self._logger.warning(
                    "S3 write attempt failed for announcement",
                    attempt=attempt + 1,
                    max_retries=MAX_RETRIES,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    announcement_link=announcement.link,
                )
                if attempt < MAX_RETRIES:
                    backoff = 2**attempt  # 1s, 2s, 4s
                    time.sleep(backoff)

        self._logger.error(
            "Failed to save announcement after all retries",
            max_retries=MAX_RETRIES,
            announcement_link=announcement.link,
            announcement_title=announcement.title,
        )
        return False

    def save_error_record(self, error: AnnouncementError) -> bool:
        """Append an error record to the error CSV in S3.

        Downloads the existing error CSV (if any), appends the new error row,
        and uploads the updated file.

        Returns True on success, False on failure after all retries.
        """
        error_row = {
            "link": error.link,
            "title": error.title,
            "stage": error.stage,
            "error_type": error.error_type,
            "error_message": error.error_message,
            "timestamp": error.timestamp,
            "run_id": error.run_id,
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                # Load existing error CSV content (or start with headers only)
                existing_content = self._load_csv_content(ERRORS_KEY)

                # Append the new error row
                updated_content = self._append_row_to_csv(
                    existing_content, error_row, ERROR_CSV_COLUMNS
                )

                # Upload the updated CSV
                self._upload_csv(ERRORS_KEY, updated_content)

                self._logger.info(
                    "Error record saved to S3",
                    announcement_link=error.link,
                    stage=error.stage,
                    error_type=error.error_type,
                )
                return True

            except Exception as exc:
                self._logger.warning(
                    "S3 write attempt failed for error record",
                    attempt=attempt + 1,
                    max_retries=MAX_RETRIES,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    announcement_link=error.link,
                )
                if attempt < MAX_RETRIES:
                    backoff = 2**attempt  # 1s, 2s, 4s
                    time.sleep(backoff)

        self._logger.error(
            "Failed to save error record after all retries",
            max_retries=MAX_RETRIES,
            announcement_link=error.link,
            stage=error.stage,
        )
        return False

    def _load_csv_content(self, key: str) -> str:
        """Load existing CSV content from S3, or return empty string if not found."""
        try:
            response = self._s3.get_object(
                Bucket=self._data_bucket,
                Key=key,
            )
            return response["Body"].read().decode("utf-8")
        except self._s3.exceptions.NoSuchKey:
            return ""
        except Exception as exc:
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if error_code in ("NoSuchKey", "404"):
                return ""
            raise

    def _append_row_to_csv(
        self, existing_content: str, row: dict, columns: list[str]
    ) -> str:
        """Append a row to CSV content, adding headers if the file is new."""
        output = io.StringIO()

        if existing_content:
            # Write existing content (ensure it ends with newline)
            output.write(existing_content)
            if not existing_content.endswith("\n"):
                output.write("\n")
        else:
            # New file — write header row first
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()

        # Append the new row
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writerow(row)

        return output.getvalue()

    def _upload_csv(self, key: str, content: str) -> None:
        """Upload CSV content to S3 with server-side encryption."""
        self._s3.put_object(
            Bucket=self._data_bucket,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="text/csv",
            ServerSideEncryption="AES256",
        )

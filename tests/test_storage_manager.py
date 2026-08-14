"""Unit tests for the Storage Manager module.

Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4
"""

import csv
import io
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from src.config import Config
from src.pipeline.storage_manager import (
    CsvSchemaMismatchError,
    ANNOUNCEMENT_CSV_COLUMNS,
    ANNOUNCEMENTS_KEY,
    ERROR_CSV_COLUMNS,
    ERRORS_KEY,
    MAX_RETRIES,
    StorageManager,
)
from src.shared.logger import StructuredLogger
from src.shared.models import AnnouncementError, ProcessedAnnouncement, Report


# --- Fixtures ---

TEST_BUCKET = "test-data-bucket"


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def logger():
    return StructuredLogger(lambda_name="test", run_id="test-run-id")


@pytest.fixture
def sample_announcement():
    return ProcessedAnnouncement(
        title="Amazon Bedrock now supports Claude 4",
        description="Amazon Bedrock adds support for Anthropic Claude 4 model.",
        pub_date="Mon, 15 Jan 2025 22:00:00 GMT",
        link="https://aws.amazon.com/about-aws/whats-new/2025/01/bedrock-claude-4",
        importance_level=3,
        importance_score=6.5,
        report=Report(
            whats_new="Bedrock now supports Claude 4.",
            how_it_works="Available via invoke_model API.",
            why_important="State-of-the-art AI capabilities.",
            how_different="Better reasoning than Claude 3.",
            when_to_prefer="Use for complex multi-step tasks.",
            availability="GA in us-east-1, us-west-2.",
        ),
        mermaid_graph="graph TD\n    A[Bedrock] --> B[Claude 4]",
        blogpost_links=["https://aws.amazon.com/blogs/aws/bedrock-claude-4"],
        first_detected="2025-01-15T22:05:00Z",
    )


@pytest.fixture
def sample_error():
    return AnnouncementError(
        link="https://aws.amazon.com/about-aws/whats-new/2025/01/sagemaker-update",
        title="Amazon SageMaker update",
        stage="report_generation",
        error_type="ThrottlingException",
        error_message="Rate exceeded",
        timestamp="2025-01-15T22:10:00Z",
        run_id="run-abc-123",
    )


@pytest.fixture
def s3_client():
    """Create a mocked S3 client with a test bucket."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=TEST_BUCKET)
        yield client


@pytest.fixture
def storage_manager(config, s3_client, logger):
    """Create a StorageManager with mocked S3."""
    return StorageManager(config, s3_client, logger, TEST_BUCKET)


# --- Test: load_existing_links ---


class TestLoadExistingLinks:
    """Test loading existing announcement links for deduplication.

    **Validates: Requirements 8.1, 8.4**
    """

    def test_returns_empty_set_when_no_csv_exists(self, storage_manager):
        """Returns empty set when the announcements CSV does not exist yet."""
        links = storage_manager.load_existing_links()
        assert links == set()

    def test_returns_links_from_existing_csv(self, s3_client, storage_manager):
        """Returns all links from an existing CSV file."""
        # Seed the CSV with some data
        csv_content = (
            "title,description,pub_date,link,aws_service,importance_level,"
            "importance_score,whats_new,how_it_works,why_important,"
            "how_different,when_to_prefer,availability,mermaid_graph,"
            "blogpost_links,first_detected\n"
            "Title1,Desc1,Date1,https://link1.com,Service1,2,4.0,"
            "wn,hiw,wi,hd,wtp,avail,,links,2025-01-01T00:00:00Z\n"
            "Title2,Desc2,Date2,https://link2.com,Service2,1,2.0,"
            "wn,hiw,wi,hd,wtp,avail,,links,2025-01-02T00:00:00Z\n"
        )
        s3_client.put_object(
            Bucket=TEST_BUCKET,
            Key=ANNOUNCEMENTS_KEY,
            Body=csv_content.encode("utf-8"),
        )

        links = storage_manager.load_existing_links()
        assert links == {"https://link1.com", "https://link2.com"}

    def test_returns_empty_set_for_header_only_csv(self, s3_client, storage_manager):
        """Returns empty set when CSV has only headers and no data rows."""
        csv_content = ",".join(ANNOUNCEMENT_CSV_COLUMNS) + "\n"
        s3_client.put_object(
            Bucket=TEST_BUCKET,
            Key=ANNOUNCEMENTS_KEY,
            Body=csv_content.encode("utf-8"),
        )

        links = storage_manager.load_existing_links()
        assert links == set()


# --- Test: save_announcement ---


class TestSaveAnnouncement:
    """Test saving announcements to S3 CSV.

    **Validates: Requirements 7.1, 7.2, 7.4, 7.5**
    """

    def test_creates_csv_with_headers_on_first_save(
        self, s3_client, storage_manager, sample_announcement
    ):
        """First save creates a new CSV with headers and the announcement row."""
        result = storage_manager.save_announcement(sample_announcement)

        assert result is True

        # Verify the file was created in S3
        response = s3_client.get_object(Bucket=TEST_BUCKET, Key=ANNOUNCEMENTS_KEY)
        content = response["Body"].read().decode("utf-8")

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["link"] == sample_announcement.link
        assert rows[0]["title"] == sample_announcement.title
        assert rows[0]["importance_level"] == "3"

    def test_appends_to_existing_csv(
        self, s3_client, storage_manager, sample_announcement
    ):
        """Subsequent saves append rows without overwriting existing data."""
        # Save first announcement
        storage_manager.save_announcement(sample_announcement)

        # Save a second announcement
        second = ProcessedAnnouncement(
            title="SageMaker update",
            description="New SageMaker feature.",
            pub_date="Tue, 16 Jan 2025 10:00:00 GMT",
            link="https://aws.amazon.com/about-aws/whats-new/2025/01/sagemaker",
            importance_level=2,
            importance_score=4.0,
            report=Report(
                whats_new="SageMaker update.",
                how_it_works="Works via API.",
                why_important="Improves ML workflows.",
                how_different="New feature not available before.",
                when_to_prefer="Use for training jobs.",
                availability="GA in all regions.",
            ),
            mermaid_graph=None,
            blogpost_links=[],
            first_detected="2025-01-16T10:00:00Z",
        )
        result = storage_manager.save_announcement(second)

        assert result is True

        # Verify both rows exist
        response = s3_client.get_object(Bucket=TEST_BUCKET, Key=ANNOUNCEMENTS_KEY)
        content = response["Body"].read().decode("utf-8")

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["link"] == sample_announcement.link
        assert rows[1]["link"] == second.link

    def test_s3_upload_uses_aes256_encryption(
        self, s3_client, storage_manager, sample_announcement
    ):
        """S3 uploads use ServerSideEncryption='AES256'."""
        storage_manager.save_announcement(sample_announcement)

        # Check the object metadata for encryption
        response = s3_client.head_object(Bucket=TEST_BUCKET, Key=ANNOUNCEMENTS_KEY)
        assert response.get("ServerSideEncryption") == "AES256"

    @patch("src.pipeline.storage_manager.time.sleep")
    def test_retries_on_s3_write_failure(self, mock_sleep, config, logger):
        """Retries S3 writes up to 3 times with exponential backoff."""
        mock_s3 = MagicMock()

        # Simulate NoSuchKey on get (new file) then put_object fails twice then succeeds
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey("No key")
        mock_s3.put_object.side_effect = [
            ClientError(
                {"Error": {"Code": "InternalError", "Message": "Server error"}},
                "PutObject",
            ),
            ClientError(
                {"Error": {"Code": "InternalError", "Message": "Server error"}},
                "PutObject",
            ),
            None,  # Success on third attempt (CSV write)
            None,  # Success for _append_link
        ]

        sm = StorageManager(config, mock_s3, logger, TEST_BUCKET)
        announcement = ProcessedAnnouncement(
            title="Test",
            description="Test desc",
            pub_date="Date",
            link="https://test.com",
            importance_level=1,
            importance_score=1.0,
            report=Report(
                whats_new="wn",
                how_it_works="hiw",
                why_important="wi",
                how_different="hd",
                when_to_prefer="wtp",
                availability="avail",
            ),
            mermaid_graph=None,
            blogpost_links=[],
            first_detected="2025-01-01T00:00:00Z",
        )

        result = sm.save_announcement(announcement)

        assert result is True
        assert mock_s3.put_object.call_count == 4  # 2 failures + 1 CSV success + 1 links append
        # Exponential backoff: 1s, 2s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    @patch("src.pipeline.storage_manager.time.sleep")
    def test_returns_false_after_all_retries_exhausted(self, mock_sleep, config, logger):
        """Returns False when all retry attempts are exhausted."""
        mock_s3 = MagicMock()
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey("No key")
        mock_s3.put_object.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "Server error"}},
            "PutObject",
        )

        sm = StorageManager(config, mock_s3, logger, TEST_BUCKET)
        announcement = ProcessedAnnouncement(
            title="Test",
            description="Test desc",
            pub_date="Date",
            link="https://test.com",
            importance_level=1,
            importance_score=1.0,
            report=Report(
                whats_new="wn",
                how_it_works="hiw",
                why_important="wi",
                how_different="hd",
                when_to_prefer="wtp",
                availability="avail",
            ),
            mermaid_graph=None,
            blogpost_links=[],
            first_detected="2025-01-01T00:00:00Z",
        )

        result = sm.save_announcement(announcement)

        assert result is False
        # 1 initial + 3 retries = 4 total attempts
        assert mock_s3.put_object.call_count == MAX_RETRIES + 1

    def test_csv_contains_all_columns(
        self, s3_client, storage_manager, sample_announcement
    ):
        """Saved CSV contains all expected columns from the schema."""
        storage_manager.save_announcement(sample_announcement)

        response = s3_client.get_object(Bucket=TEST_BUCKET, Key=ANNOUNCEMENTS_KEY)
        content = response["Body"].read().decode("utf-8")

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)

        assert reader.fieldnames == ANNOUNCEMENT_CSV_COLUMNS
        assert len(rows) == 1

    def test_blogpost_links_serialized_as_pipe_separated(
        self, s3_client, storage_manager, sample_announcement
    ):
        """Blogpost links are stored as pipe-separated values."""
        storage_manager.save_announcement(sample_announcement)

        response = s3_client.get_object(Bucket=TEST_BUCKET, Key=ANNOUNCEMENTS_KEY)
        content = response["Body"].read().decode("utf-8")

        reader = csv.DictReader(io.StringIO(content))
        row = next(reader)

        assert row["blogpost_links"] == "https://aws.amazon.com/blogs/aws/bedrock-claude-4"

    def test_mermaid_graph_stored_when_present(
        self, s3_client, storage_manager, sample_announcement
    ):
        """Mermaid graph is stored in the CSV when present."""
        storage_manager.save_announcement(sample_announcement)

        response = s3_client.get_object(Bucket=TEST_BUCKET, Key=ANNOUNCEMENTS_KEY)
        content = response["Body"].read().decode("utf-8")

        reader = csv.DictReader(io.StringIO(content))
        row = next(reader)

        assert row["mermaid_graph"] == "graph TD\n    A[Bedrock] --> B[Claude 4]"

    def test_mermaid_graph_empty_string_when_none(
        self, s3_client, storage_manager
    ):
        """Mermaid graph is stored as empty string when None (1-star)."""
        announcement = ProcessedAnnouncement(
            title="Minor update",
            description="Small change.",
            pub_date="Date",
            link="https://test.com/minor",
            importance_level=1,
            importance_score=1.5,
            report=Report(
                whats_new="wn",
                how_it_works="hiw",
                why_important="wi",
                how_different="hd",
                when_to_prefer="wtp",
                availability="avail",
            ),
            mermaid_graph=None,
            blogpost_links=[],
            first_detected="2025-01-01T00:00:00Z",
        )
        storage_manager.save_announcement(announcement)

        response = s3_client.get_object(Bucket=TEST_BUCKET, Key=ANNOUNCEMENTS_KEY)
        content = response["Body"].read().decode("utf-8")

        reader = csv.DictReader(io.StringIO(content))
        row = next(reader)

        assert row["mermaid_graph"] == ""


# --- Test: save_error_record ---


class TestSaveErrorRecord:
    """Test saving error records to S3 CSV.

    **Validates: Requirements 7.5**
    """

    def test_creates_error_csv_on_first_save(
        self, s3_client, storage_manager, sample_error
    ):
        """First error save creates a new error CSV with headers and the error row."""
        result = storage_manager.save_error_record(sample_error)

        assert result is True

        response = s3_client.get_object(Bucket=TEST_BUCKET, Key=ERRORS_KEY)
        content = response["Body"].read().decode("utf-8")

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["link"] == sample_error.link
        assert rows[0]["stage"] == "report_generation"
        assert rows[0]["error_type"] == "ThrottlingException"
        assert rows[0]["run_id"] == "run-abc-123"

    def test_appends_to_existing_error_csv(
        self, s3_client, storage_manager, sample_error
    ):
        """Subsequent error saves append rows without overwriting."""
        storage_manager.save_error_record(sample_error)

        second_error = AnnouncementError(
            link="https://aws.amazon.com/about-aws/whats-new/2025/01/lambda-update",
            title="Lambda update",
            stage="graph_generation",
            error_type="ModelTimeoutException",
            error_message="Model timed out",
            timestamp="2025-01-15T22:12:00Z",
            run_id="run-abc-123",
        )
        result = storage_manager.save_error_record(second_error)

        assert result is True

        response = s3_client.get_object(Bucket=TEST_BUCKET, Key=ERRORS_KEY)
        content = response["Body"].read().decode("utf-8")

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["link"] == sample_error.link
        assert rows[1]["link"] == second_error.link

    def test_error_csv_has_correct_columns(
        self, s3_client, storage_manager, sample_error
    ):
        """Error CSV contains all expected columns."""
        storage_manager.save_error_record(sample_error)

        response = s3_client.get_object(Bucket=TEST_BUCKET, Key=ERRORS_KEY)
        content = response["Body"].read().decode("utf-8")

        reader = csv.DictReader(io.StringIO(content))
        list(reader)  # consume rows

        assert reader.fieldnames == ERROR_CSV_COLUMNS

    def test_error_csv_uses_aes256_encryption(
        self, s3_client, storage_manager, sample_error
    ):
        """Error CSV uploads use ServerSideEncryption='AES256'."""
        storage_manager.save_error_record(sample_error)

        response = s3_client.head_object(Bucket=TEST_BUCKET, Key=ERRORS_KEY)
        assert response.get("ServerSideEncryption") == "AES256"

    @patch("src.pipeline.storage_manager.time.sleep")
    def test_error_record_retries_on_failure(self, mock_sleep, config, logger):
        """Error record saves retry on S3 failure."""
        mock_s3 = MagicMock()
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey("No key")
        mock_s3.put_object.side_effect = [
            ClientError(
                {"Error": {"Code": "InternalError", "Message": "Server error"}},
                "PutObject",
            ),
            None,  # Success on second attempt
        ]

        sm = StorageManager(config, mock_s3, logger, TEST_BUCKET)
        error = AnnouncementError(
            link="https://test.com",
            title="Test",
            stage="storage",
            error_type="TestError",
            error_message="Test message",
            timestamp="2025-01-01T00:00:00Z",
            run_id="run-123",
        )

        result = sm.save_error_record(error)

        assert result is True
        assert mock_s3.put_object.call_count == 2
        mock_sleep.assert_called_once_with(1)


# --- Item 3: links-index write path (docs/audit-remediation-plan.md) ---


def _throttling_error(operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
        operation,
    )


class TestAppendLinkErrorHandling:
    """A transient read error must never blank the dedup index."""

    def _manager_with_mock_s3(self, config, logger):
        mock_s3 = MagicMock()
        # Modeled exceptions must be real exception classes for except clauses
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        return StorageManager(config, mock_s3, logger, TEST_BUCKET), mock_s3

    def test_transient_read_error_propagates_and_never_writes(self, config, logger):
        """Throttling on get_object must raise — NOT be treated as an empty file."""
        manager, mock_s3 = self._manager_with_mock_s3(config, logger)
        mock_s3.get_object.side_effect = _throttling_error("GetObject")

        with pytest.raises(ClientError):
            manager._append_link("https://example.com/announcement")

        # The catastrophic historical behaviour: put_object replacing the
        # whole index with one link. Must not happen.
        mock_s3.put_object.assert_not_called()

    def test_missing_file_creates_index_with_one_link(self, config, logger):
        manager, mock_s3 = self._manager_with_mock_s3(config, logger)
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()

        manager._append_link("https://example.com/announcement")

        body = mock_s3.put_object.call_args.kwargs["Body"].decode("utf-8")
        assert body == "https://example.com/announcement\n"

    def test_client_error_404_treated_as_missing(self, config, logger):
        manager, mock_s3 = self._manager_with_mock_s3(config, logger)
        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject"
        )

        manager._append_link("https://example.com/announcement")
        mock_s3.put_object.assert_called_once()


class TestSaveAnnouncementWriteOrder:
    """Index first, then CSV; failures produce a skip, never a duplicate."""

    def _manager_with_mock_s3(self, config, logger):
        mock_s3 = MagicMock()
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        # Empty bucket state: all reads miss
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()
        mock_s3.head_object.side_effect = mock_s3.exceptions.NoSuchKey()
        return StorageManager(config, mock_s3, logger, TEST_BUCKET), mock_s3

    def test_link_index_written_before_csv(self, config, logger, sample_announcement):
        manager, mock_s3 = self._manager_with_mock_s3(config, logger)

        assert manager.save_announcement(sample_announcement) is True

        keys_in_order = [
            call.kwargs["Key"] for call in mock_s3.put_object.call_args_list
        ]
        assert keys_in_order == ["database/links.txt", ANNOUNCEMENTS_KEY]

    @patch("src.pipeline.storage_manager.time.sleep")
    def test_index_write_failure_means_no_csv_row(
        self, _sleep, config, logger, sample_announcement
    ):
        """If the index cannot be written, zero CSV writes occur — the item
        is retried next run instead of risking divergence."""
        manager, mock_s3 = self._manager_with_mock_s3(config, logger)
        mock_s3.put_object.side_effect = _throttling_error("PutObject")

        assert manager.save_announcement(sample_announcement) is False

        csv_writes = [
            call for call in mock_s3.put_object.call_args_list
            if call.kwargs["Key"] == ANNOUNCEMENTS_KEY
        ]
        assert csv_writes == []

    @patch("src.pipeline.storage_manager.time.sleep")
    def test_csv_failure_after_link_recorded_logs_remedy(
        self, _sleep, config, logger, sample_announcement, capsys
    ):
        """CSV failing after the index write must log the manual re-queue path."""
        manager, mock_s3 = self._manager_with_mock_s3(config, logger)

        def put_object(**kwargs):
            if kwargs["Key"] == ANNOUNCEMENTS_KEY:
                raise _throttling_error("PutObject")
            return {}

        mock_s3.put_object.side_effect = put_object

        assert manager.save_announcement(sample_announcement) is False

        log_output = capsys.readouterr().out
        assert "links.txt" in log_output
        assert sample_announcement.link in log_output

    @patch("src.pipeline.storage_manager.time.sleep")
    def test_csv_retry_never_reappends_link(
        self, _sleep, config, logger, sample_announcement
    ):
        """CSV retries must not touch the links index again (historical
        duplicate-row bug: the old retry loop wrapped both writes)."""
        manager, mock_s3 = self._manager_with_mock_s3(config, logger)

        csv_attempts = {"count": 0}

        def put_object(**kwargs):
            if kwargs["Key"] == ANNOUNCEMENTS_KEY:
                csv_attempts["count"] += 1
                if csv_attempts["count"] == 1:
                    raise _throttling_error("PutObject")
            return {}

        mock_s3.put_object.side_effect = put_object

        assert manager.save_announcement(sample_announcement) is True

        link_writes = [
            call for call in mock_s3.put_object.call_args_list
            if call.kwargs["Key"] == "database/links.txt"
        ]
        assert len(link_writes) == 1


class TestDamagedIndexSelfHeal:
    """A near-empty index next to a substantial CSV is rebuilt, not trusted."""

    def _big_csv(self, n_rows: int = 300) -> str:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=ANNOUNCEMENT_CSV_COLUMNS)
        writer.writeheader()
        for i in range(n_rows):
            row = {col: f"filler-{i}-" + "x" * 20 for col in ANNOUNCEMENT_CSV_COLUMNS}
            row["link"] = f"https://aws.amazon.com/whats-new/{i}"
            writer.writerow(row)
        return output.getvalue()

    def test_damaged_index_rebuilt_from_csv(self, config, s3_client, logger):
        csv_content = self._big_csv()
        assert len(csv_content) > 100_000  # must trip the size guard
        s3_client.put_object(
            Bucket=TEST_BUCKET, Key=ANNOUNCEMENTS_KEY, Body=csv_content.encode()
        )
        # Damaged index: 2 links where the CSV holds 300
        s3_client.put_object(
            Bucket=TEST_BUCKET,
            Key="database/links.txt",
            Body=b"https://aws.amazon.com/whats-new/0\nhttps://example.com/only-in-index\n",
        )
        manager = StorageManager(config, s3_client, logger, TEST_BUCKET)

        links = manager.load_existing_links()

        assert len(links) == 301  # 300 from CSV + 1 unique to the index
        assert "https://example.com/only-in-index" in links
        # And the rebuilt index was persisted
        stored = s3_client.get_object(Bucket=TEST_BUCKET, Key="database/links.txt")
        assert len(stored["Body"].read().decode().strip().split("\n")) == 301

    def test_small_index_with_small_csv_is_trusted(self, config, s3_client, logger):
        """Fresh deployment: few links, small CSV — no rebuild."""
        s3_client.put_object(
            Bucket=TEST_BUCKET, Key=ANNOUNCEMENTS_KEY, Body=b"link\nhttps://a.example\n"
        )
        s3_client.put_object(
            Bucket=TEST_BUCKET,
            Key="database/links.txt",
            Body=b"https://a.example\nhttps://b.example\n",
        )
        manager = StorageManager(config, s3_client, logger, TEST_BUCKET)

        links = manager.load_existing_links()
        assert links == {"https://a.example", "https://b.example"}


# --- Item 5: CSV schema drift fails loudly (docs/audit-remediation-plan.md) ---


class TestCsvSchemaMismatch:
    """Schema drift must raise an actionable error, never a bare ValueError
    (extra fields) or a silent empty cell (missing fields)."""

    @pytest.fixture
    def manager(self, config, s3_client, logger):
        return StorageManager(config, s3_client, logger, TEST_BUCKET)

    def _csv_with_columns(self, columns: list[str]) -> str:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerow({c: "value" for c in columns})
        return output.getvalue()

    def test_row_with_extra_field_raises_named_error(self, manager):
        """New model field, old stored header → error names the new column."""
        existing = self._csv_with_columns(["title", "link"])
        row = {"title": "t", "link": "l", "brand_new_field": "x"}

        with pytest.raises(CsvSchemaMismatchError) as exc_info:
            manager._append_row_to_csv(existing, row, ["title", "link", "brand_new_field"])

        message = str(exc_info.value)
        assert "brand_new_field" in message
        assert "migration" in message

    def test_header_with_extra_column_raises_named_error(self, manager):
        """Migrated header, old code's row → previously a silent empty cell."""
        existing = self._csv_with_columns(["title", "link", "migrated_column"])
        row = {"title": "t", "link": "l"}

        with pytest.raises(CsvSchemaMismatchError) as exc_info:
            manager._append_row_to_csv(existing, row, ["title", "link"])

        assert "migrated_column" in str(exc_info.value)

    def test_matching_schema_appends_normally(self, manager):
        existing = self._csv_with_columns(["title", "link"])
        row = {"title": "t2", "link": "l2"}

        result = manager._append_row_to_csv(existing, row, ["title", "link"])

        rows = list(csv.DictReader(io.StringIO(result)))
        assert len(rows) == 2
        assert rows[1] == row

    def test_new_file_uses_canonical_columns(self, manager, sample_announcement):
        result = manager._append_row_to_csv(
            "", sample_announcement.to_csv_row(), ANNOUNCEMENT_CSV_COLUMNS
        )
        header = result.split("\n", 1)[0]
        assert header.split(",")[0] == "title"

    def test_current_model_matches_live_schema(self, manager, sample_announcement):
        """Guard: ProcessedAnnouncement.to_csv_row() must line up with the
        canonical column list, or every production append would now raise."""
        assert set(sample_announcement.to_csv_row().keys()) == set(
            ANNOUNCEMENT_CSV_COLUMNS
        )

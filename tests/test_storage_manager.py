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
        aws_service="Amazon Bedrock",
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
            aws_service="Amazon SageMaker",
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
            aws_service="TestService",
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
            aws_service="TestService",
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
            aws_service="TestService",
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

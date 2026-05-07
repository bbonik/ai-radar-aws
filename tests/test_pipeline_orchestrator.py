"""Unit tests for the Pipeline Orchestrator module.

Validates: Requirements 4.6, 14.1
"""

from unittest.mock import MagicMock, patch, call

import pytest

from src.config import Config
from src.pipeline.orchestrator import PipelineOrchestrator
from src.shared.logger import StructuredLogger
from src.shared.models import (
    AnnouncementError,
    PipelineRunSummary,
    ProcessedAnnouncement,
    Report,
    ResearchContext,
    RSSItem,
    PageContent,
)


# --- Fixtures ---


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def logger():
    return StructuredLogger(lambda_name="test", run_id="test-run-id")


@pytest.fixture
def mock_context():
    """Mock Lambda context."""
    ctx = MagicMock()
    ctx.get_remaining_time_in_millis.return_value = 900_000  # 15 minutes
    return ctx


@pytest.fixture
def sample_items():
    """A list of sample RSS items for testing."""
    return [
        RSSItem(
            title="Amazon Bedrock now supports Claude 4",
            description="Amazon Bedrock adds support for Anthropic Claude 4 model.",
            pub_date="Mon, 15 Jan 2025 22:00:00 GMT",
            link="https://aws.amazon.com/about-aws/whats-new/2025/01/bedrock-claude-4",
        ),
        RSSItem(
            title="Amazon SageMaker adds new training feature",
            description="SageMaker introduces distributed training improvements.",
            pub_date="Tue, 16 Jan 2025 10:00:00 GMT",
            link="https://aws.amazon.com/about-aws/whats-new/2025/01/sagemaker-training",
        ),
        RSSItem(
            title="AWS Lambda adds Python 3.13 support",
            description="Lambda now supports Python 3.13 runtime.",
            pub_date="Wed, 17 Jan 2025 08:00:00 GMT",
            link="https://aws.amazon.com/about-aws/whats-new/2025/01/lambda-python313",
        ),
    ]


@pytest.fixture
def sample_research():
    return ResearchContext(gathered_content=[], skipped=False, error_links=[])


@pytest.fixture
def sample_report():
    return Report(
        whats_new="Summary of the announcement.",
        how_it_works="Technical explanation.",
        why_important="Significance for users.",
        how_different="Comparison to previous.",
        when_to_prefer="Guidance on usage.",
        availability="GA in us-east-1.",
    )


def _create_orchestrator_with_mocks(config, mock_context, logger):
    """Create a PipelineOrchestrator with all internal components mocked."""
    with patch("src.pipeline.orchestrator.boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        orchestrator = PipelineOrchestrator(config, mock_context, logger)

        # Mock all pipeline components
        orchestrator._rss_fetcher = MagicMock()
        orchestrator._relevance_filter = MagicMock()
        orchestrator._importance_classifier = MagicMock()
        orchestrator._research_agent = MagicMock()
        orchestrator._report_generator = MagicMock()
        orchestrator._graph_generator = MagicMock()
        orchestrator._storage_manager = MagicMock()

        return orchestrator


# --- Test: Individual Announcement Failure Does Not Halt Pipeline ---


class TestFailureIsolation:
    """Test that individual announcement failure does not halt pipeline.

    **Validates: Requirements 4.6**
    """

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_pipeline_continues_after_single_failure(
        self, mock_boto, config, mock_context, logger, sample_items, sample_research, sample_report
    ):
        """Pipeline processes remaining items even when one fails mid-pipeline."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        # Setup: 3 items fetched, all new, all relevant
        orchestrator._rss_fetcher.fetch.return_value = sample_items
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = sample_items

        # First item: classify succeeds, research raises exception
        # Second item: all stages succeed
        # Third item: all stages succeed
        orchestrator._importance_classifier.classify.return_value = (2, 4.5)
        orchestrator._importance_classifier._extract_service.return_value = "Amazon Bedrock"

        orchestrator._research_agent.research.side_effect = [
            RuntimeError("Network timeout during research"),  # First item fails
            sample_research,  # Second item succeeds
            sample_research,  # Third item succeeds
        ]

        orchestrator._report_generator.generate.return_value = sample_report
        orchestrator._graph_generator.generate.return_value = "graph TD\n    A --> B"
        orchestrator._storage_manager.save_announcement.return_value = True
        orchestrator._storage_manager.save_error_record.return_value = True

        # Mock Lambda 2 invocation
        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        summary = orchestrator.run()

        # Pipeline completed: 2 succeeded, 1 failed
        assert summary.total_processed_ok == 2
        assert summary.total_failed == 1
        # All 3 items were attempted (research was called 3 times)
        assert orchestrator._research_agent.research.call_count == 3

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_pipeline_continues_after_multiple_failures(
        self, mock_boto, config, mock_context, logger, sample_items, sample_research, sample_report
    ):
        """Pipeline processes all items even when multiple fail."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = sample_items
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = sample_items

        orchestrator._importance_classifier.classify.return_value = (2, 4.5)
        orchestrator._importance_classifier._extract_service.return_value = "Amazon Bedrock"

        # First two items fail, third succeeds
        orchestrator._research_agent.research.side_effect = [
            RuntimeError("Failure 1"),
            RuntimeError("Failure 2"),
            sample_research,
        ]

        orchestrator._report_generator.generate.return_value = sample_report
        orchestrator._graph_generator.generate.return_value = None
        orchestrator._storage_manager.save_announcement.return_value = True
        orchestrator._storage_manager.save_error_record.return_value = True

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        summary = orchestrator.run()

        assert summary.total_processed_ok == 1
        assert summary.total_failed == 2
        # All items were attempted
        assert orchestrator._research_agent.research.call_count == 3

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_all_items_fail_pipeline_still_completes(
        self, mock_boto, config, mock_context, logger, sample_items
    ):
        """Pipeline completes and returns summary even when all items fail."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = sample_items
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = sample_items

        # All items fail at classification stage
        orchestrator._importance_classifier.classify.side_effect = RuntimeError("LLM down")
        orchestrator._storage_manager.save_error_record.return_value = True

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        summary = orchestrator.run()

        assert isinstance(summary, PipelineRunSummary)
        assert summary.total_processed_ok == 0
        assert summary.total_failed == 3


# --- Test: Pipeline Run Summary Generation ---


class TestPipelineRunSummary:
    """Test pipeline run summary generation with correct counts.

    **Validates: Requirements 14.1**
    """

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_summary_counts_match_processing_results(
        self, mock_boto, config, mock_context, logger, sample_items, sample_research, sample_report
    ):
        """Summary counts accurately reflect fetched, deduplicated, relevant, processed, and failed."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        # 3 items fetched, 1 deduplicated, 2 relevant, 1 succeeds, 1 fails
        orchestrator._rss_fetcher.fetch.return_value = sample_items
        orchestrator._storage_manager.load_existing_links.return_value = {sample_items[0].link}
        # After dedup: items[1] and items[2] remain
        remaining = [sample_items[1], sample_items[2]]
        orchestrator._relevance_filter.filter.return_value = remaining

        orchestrator._importance_classifier.classify.return_value = (1, 2.0)
        orchestrator._importance_classifier._extract_service.return_value = "Amazon SageMaker"

        # First relevant item succeeds, second fails
        orchestrator._research_agent.research.side_effect = [
            sample_research,
            RuntimeError("Research failed"),
        ]
        orchestrator._report_generator.generate.return_value = sample_report
        orchestrator._graph_generator.generate.return_value = None
        orchestrator._storage_manager.save_announcement.return_value = True
        orchestrator._storage_manager.save_error_record.return_value = True

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        summary = orchestrator.run()

        assert summary.total_fetched == 3
        assert summary.total_deduplicated == 1
        assert summary.total_relevant == 2
        assert summary.total_processed_ok == 1
        assert summary.total_failed == 1

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_summary_has_valid_timestamps(
        self, mock_boto, config, mock_context, logger
    ):
        """Summary includes valid ISO start_time and end_time."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = []
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = []

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        summary = orchestrator.run()

        # Timestamps should be ISO format ending with Z
        assert summary.start_time.endswith("Z")
        assert summary.end_time.endswith("Z")
        assert summary.start_time <= summary.end_time

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_summary_includes_run_id(
        self, mock_boto, config, mock_context, logger
    ):
        """Summary includes the correlation run_id."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = []
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = []

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        summary = orchestrator.run()

        assert summary.run_id == orchestrator._run_id

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_summary_failed_items_contain_error_details(
        self, mock_boto, config, mock_context, logger, sample_items
    ):
        """Summary failed_items list contains link, title, stage, and error for each failure."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = [sample_items[0]]
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = [sample_items[0]]

        orchestrator._importance_classifier.classify.side_effect = ValueError("Bad input")
        orchestrator._storage_manager.save_error_record.return_value = True

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        summary = orchestrator.run()

        assert len(summary.failed_items) == 1
        failed = summary.failed_items[0]
        assert failed["link"] == sample_items[0].link
        assert failed["title"] == sample_items[0].title
        assert "stage" in failed
        assert "error" in failed

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_summary_tracks_research_skipped_count(
        self, mock_boto, config, mock_context, logger, sample_items, sample_report
    ):
        """Summary tracks how many announcements had research skipped."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = sample_items[:2]
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = sample_items[:2]

        orchestrator._importance_classifier.classify.return_value = (1, 2.0)
        orchestrator._importance_classifier._extract_service.return_value = "Service"

        # First item: research not skipped, second: research skipped
        orchestrator._research_agent.research.side_effect = [
            ResearchContext(gathered_content=[], skipped=False),
            ResearchContext(gathered_content=[], skipped=True),
        ]
        orchestrator._report_generator.generate.return_value = sample_report
        orchestrator._graph_generator.generate.return_value = None
        orchestrator._storage_manager.save_announcement.return_value = True

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        summary = orchestrator.run()

        assert summary.research_skipped == 1


# --- Test: Lambda 2 Async Invocation ---


class TestLambda2Invocation:
    """Test Lambda 2 async invocation is called after processing.

    **Validates: Requirements 14.1**
    """

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_lambda2_invoked_asynchronously_after_processing(
        self, mock_boto, config, mock_context, logger
    ):
        """Lambda 2 is invoked with InvocationType='Event' after pipeline processing."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = []
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = []

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        summary = orchestrator.run()

        # Lambda client was created and invoke was called
        mock_boto.assert_called_with("lambda", region_name=config.aws_region)
        mock_lambda.invoke.assert_called_once()

        # Verify async invocation type
        invoke_kwargs = mock_lambda.invoke.call_args[1]
        assert invoke_kwargs["InvocationType"] == "Event"
        assert summary.website_builder_invoked is True

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_lambda2_invocation_includes_run_id_in_payload(
        self, mock_boto, config, mock_context, logger
    ):
        """Lambda 2 invocation payload includes the run_id for correlated logging."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = []
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = []

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        orchestrator.run()

        invoke_kwargs = mock_lambda.invoke.call_args[1]
        import json
        payload = json.loads(invoke_kwargs["Payload"].decode("utf-8"))
        assert payload["run_id"] == orchestrator._run_id
        assert payload["source"] == "pipeline-orchestrator"

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_lambda2_failure_does_not_crash_pipeline(
        self, mock_boto, config, mock_context, logger
    ):
        """Pipeline completes even if Lambda 2 invocation fails."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = []
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = []

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.side_effect = RuntimeError("Lambda invocation failed")

        summary = orchestrator.run()

        # Pipeline still completes, but website_builder_invoked is False
        assert isinstance(summary, PipelineRunSummary)
        assert summary.website_builder_invoked is False

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_lambda2_uses_configured_function_name(
        self, mock_boto, config, mock_context, logger
    ):
        """Lambda 2 invocation uses the function name from config/env."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = []
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = []

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        orchestrator.run()

        invoke_kwargs = mock_lambda.invoke.call_args[1]
        assert invoke_kwargs["FunctionName"] == config.website_builder_function_name


# --- Test: Error Records Saved for Failed Announcements ---


class TestErrorRecordSaving:
    """Test error records are saved for failed announcements.

    **Validates: Requirements 4.6, 14.1**
    """

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_error_record_saved_on_announcement_failure(
        self, mock_boto, config, mock_context, logger, sample_items
    ):
        """When an announcement fails, an error record is saved via storage manager."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = [sample_items[0]]
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = [sample_items[0]]

        orchestrator._importance_classifier.classify.side_effect = ValueError("Classification error")
        orchestrator._storage_manager.save_error_record.return_value = True

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        orchestrator.run()

        # save_error_record should have been called
        orchestrator._storage_manager.save_error_record.assert_called_once()

        # Verify the error record has correct fields
        error_record = orchestrator._storage_manager.save_error_record.call_args[0][0]
        assert isinstance(error_record, AnnouncementError)
        assert error_record.link == sample_items[0].link
        assert error_record.title == sample_items[0].title
        assert error_record.error_type == "ValueError"
        assert "Classification error" in error_record.error_message
        assert error_record.run_id == orchestrator._run_id

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_error_record_includes_stage_information(
        self, mock_boto, config, mock_context, logger, sample_items, sample_research
    ):
        """Error record includes the stage where the failure occurred."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = [sample_items[0]]
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = [sample_items[0]]

        orchestrator._importance_classifier.classify.return_value = (2, 4.0)
        orchestrator._importance_classifier._extract_service.return_value = "Amazon Bedrock"
        orchestrator._research_agent.research.return_value = sample_research

        # Fail at report generation stage
        from src.pipeline.report_generator import ReportGenerationError
        orchestrator._report_generator.generate.side_effect = ReportGenerationError(
            "Bedrock API failed after 3 retries"
        )
        orchestrator._storage_manager.save_error_record.return_value = True

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        orchestrator.run()

        error_record = orchestrator._storage_manager.save_error_record.call_args[0][0]
        assert error_record.stage == "report_generation"

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_multiple_failures_save_multiple_error_records(
        self, mock_boto, config, mock_context, logger, sample_items
    ):
        """Each failed announcement gets its own error record saved."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = sample_items
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = sample_items

        # All items fail at classification
        orchestrator._importance_classifier.classify.side_effect = RuntimeError("LLM unavailable")
        orchestrator._storage_manager.save_error_record.return_value = True

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        orchestrator.run()

        # One error record per failed item
        assert orchestrator._storage_manager.save_error_record.call_count == 3

        # Verify each error record has the correct link
        saved_links = [
            call_args[0][0].link
            for call_args in orchestrator._storage_manager.save_error_record.call_args_list
        ]
        expected_links = [item.link for item in sample_items]
        assert saved_links == expected_links

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_error_record_has_valid_timestamp(
        self, mock_boto, config, mock_context, logger, sample_items
    ):
        """Error record includes a valid ISO timestamp."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = [sample_items[0]]
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = [sample_items[0]]

        orchestrator._importance_classifier.classify.side_effect = ValueError("Error")
        orchestrator._storage_manager.save_error_record.return_value = True

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        orchestrator.run()

        error_record = orchestrator._storage_manager.save_error_record.call_args[0][0]
        # Timestamp should be ISO format ending with Z
        assert error_record.timestamp.endswith("Z")

    @patch("src.pipeline.orchestrator.boto3.client")
    def test_storage_failure_on_save_does_not_halt_pipeline(
        self, mock_boto, config, mock_context, logger, sample_items, sample_research, sample_report
    ):
        """Pipeline continues even if storage save returns False (all retries exhausted)."""
        orchestrator = _create_orchestrator_with_mocks(config, mock_context, logger)

        orchestrator._rss_fetcher.fetch.return_value = sample_items[:2]
        orchestrator._storage_manager.load_existing_links.return_value = set()
        orchestrator._relevance_filter.filter.return_value = sample_items[:2]

        orchestrator._importance_classifier.classify.return_value = (1, 2.0)
        orchestrator._importance_classifier._extract_service.return_value = "Service"
        orchestrator._research_agent.research.return_value = sample_research
        orchestrator._report_generator.generate.return_value = sample_report
        orchestrator._graph_generator.generate.return_value = None

        # First item: storage fails, second item: storage succeeds
        orchestrator._storage_manager.save_announcement.side_effect = [False, True]
        orchestrator._storage_manager.save_error_record.return_value = True

        mock_lambda = MagicMock()
        mock_boto.return_value = mock_lambda
        mock_lambda.invoke.return_value = {}

        summary = orchestrator.run()

        # First item failed (storage), second succeeded
        assert summary.total_failed == 1
        assert summary.total_processed_ok == 1

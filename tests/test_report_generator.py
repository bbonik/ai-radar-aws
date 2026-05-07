"""Unit tests for the Report Generator module.

Validates: Requirements 5.1, 5.5
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from src.config import Config
from src.pipeline.report_generator import (
    ReportGenerationError,
    ReportGenerator,
    _MAX_RETRIES,
    _RETRY_DELAY_SECONDS,
    _SECTION_MARKERS,
)
from src.shared.logger import StructuredLogger
from src.shared.models import PageContent, Report, ResearchContext, RSSItem


# --- Fixtures ---


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def logger():
    return StructuredLogger(lambda_name="test", run_id="test-run-id")


@pytest.fixture
def sample_item():
    return RSSItem(
        title="Amazon Bedrock now supports Claude 4",
        description="Amazon Bedrock adds support for Anthropic Claude 4 model.",
        pub_date="Mon, 15 Jan 2025 22:00:00 GMT",
        link="https://aws.amazon.com/about-aws/whats-new/2025/01/bedrock-claude-4",
    )


@pytest.fixture
def sample_research():
    return ResearchContext(
        gathered_content=[
            PageContent(
                url="https://aws.amazon.com/blogs/aws/bedrock-claude-4",
                text="Claude 4 brings improved reasoning and coding capabilities.",
                title="Introducing Claude 4 on Amazon Bedrock",
            )
        ],
        skipped=False,
        error_links=[],
    )


@pytest.fixture
def empty_research():
    return ResearchContext(
        gathered_content=[],
        skipped=False,
        error_links=[],
    )


@pytest.fixture
def skipped_research():
    return ResearchContext(
        gathered_content=[],
        skipped=True,
        error_links=[],
    )


def _make_bedrock_response(text: str) -> dict:
    """Create a mock Bedrock response body matching Claude's format."""
    return {
        "content": [{"type": "text", "text": text}],
        "model": "claude-sonnet",
        "stop_reason": "end_turn",
    }


def _well_formed_llm_response() -> str:
    """Return a well-formed LLM response with all six section markers."""
    return (
        "[WHATS_NEW]\n"
        "Amazon Bedrock now supports Claude 4, bringing improved reasoning.\n\n"
        "[HOW_IT_WORKS]\n"
        "Claude 4 is available through the Bedrock API using invoke_model.\n\n"
        "[WHY_IMPORTANT]\n"
        "This gives developers access to state-of-the-art AI capabilities.\n\n"
        "[HOW_DIFFERENT]\n"
        "Compared to Claude 3, it offers better coding and reasoning.\n\n"
        "[WHEN_TO_PREFER]\n"
        "Use Claude 4 when you need complex multi-step reasoning.\n\n"
        "[AVAILABILITY]\n"
        "Generally available in us-east-1, us-west-2, and eu-west-1.\n"
    )


def _make_client_error(code: str = "ThrottlingException", message: str = "Rate exceeded"):
    """Create a botocore ClientError for testing."""
    return ClientError(
        error_response={"Error": {"Code": code, "Message": message}},
        operation_name="InvokeModel",
    )


# --- Test: Report Parsing Produces All Sections (Property 8) ---


class TestReportParsing:
    """Property 8: Report parsing produces all sections.

    **Validates: Requirements 5.1**

    For any well-formed LLM response containing the six required section markers,
    the Report parser SHALL produce a Report object with all six fields populated
    with non-empty strings.
    """

    @patch("src.pipeline.report_generator.boto3.client")
    def test_well_formed_response_produces_all_sections(
        self, mock_boto_client, config, logger, sample_item, sample_research
    ):
        """A well-formed response with all markers produces a Report with all 6 fields."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        response_text = _well_formed_llm_response()
        mock_response_body = _make_bedrock_response(response_text)

        mock_read = MagicMock()
        mock_read.read.return_value = json.dumps(mock_response_body).encode()
        mock_bedrock.invoke_model.return_value = {"body": mock_read}

        generator = ReportGenerator(config, logger)
        report = generator.generate(sample_item, sample_research)

        assert isinstance(report, Report)
        assert report.whats_new.strip() != ""
        assert report.how_it_works.strip() != ""
        assert report.why_important.strip() != ""
        assert report.how_different.strip() != ""
        assert report.when_to_prefer.strip() != ""
        assert report.availability.strip() != ""

    @patch("src.pipeline.report_generator.boto3.client")
    def test_parsed_sections_contain_expected_content(
        self, mock_boto_client, config, logger, sample_item, sample_research
    ):
        """Parsed sections contain the text between their markers."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        response_text = _well_formed_llm_response()
        mock_response_body = _make_bedrock_response(response_text)

        mock_read = MagicMock()
        mock_read.read.return_value = json.dumps(mock_response_body).encode()
        mock_bedrock.invoke_model.return_value = {"body": mock_read}

        generator = ReportGenerator(config, logger)
        report = generator.generate(sample_item, sample_research)

        assert "Claude 4" in report.whats_new
        assert "Bedrock API" in report.how_it_works
        assert "state-of-the-art" in report.why_important
        assert "Claude 3" in report.how_different
        assert "multi-step reasoning" in report.when_to_prefer
        assert "us-east-1" in report.availability

    @patch("src.pipeline.report_generator.boto3.client")
    def test_missing_section_raises_error(
        self, mock_boto_client, config, logger, sample_item, sample_research
    ):
        """A response missing one or more section markers raises ReportGenerationError."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        # Response missing [AVAILABILITY] section
        incomplete_response = (
            "[WHATS_NEW]\nSome content\n"
            "[HOW_IT_WORKS]\nSome content\n"
            "[WHY_IMPORTANT]\nSome content\n"
            "[HOW_DIFFERENT]\nSome content\n"
            "[WHEN_TO_PREFER]\nSome content\n"
            # Missing [AVAILABILITY]
        )
        mock_response_body = _make_bedrock_response(incomplete_response)

        mock_read = MagicMock()
        mock_read.read.return_value = json.dumps(mock_response_body).encode()
        mock_bedrock.invoke_model.return_value = {"body": mock_read}

        generator = ReportGenerator(config, logger)

        with pytest.raises(ReportGenerationError, match="missing sections"):
            generator.generate(sample_item, sample_research)

    @patch("src.pipeline.report_generator.boto3.client")
    def test_empty_section_content_raises_error(
        self, mock_boto_client, config, logger, sample_item, sample_research
    ):
        """A response with empty section content raises ReportGenerationError."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        # All markers present but one has empty content
        response_with_empty = (
            "[WHATS_NEW]\nSome content\n"
            "[HOW_IT_WORKS]\n\n"  # Empty content
            "[WHY_IMPORTANT]\nSome content\n"
            "[HOW_DIFFERENT]\nSome content\n"
            "[WHEN_TO_PREFER]\nSome content\n"
            "[AVAILABILITY]\nSome content\n"
        )
        mock_response_body = _make_bedrock_response(response_with_empty)

        mock_read = MagicMock()
        mock_read.read.return_value = json.dumps(mock_response_body).encode()
        mock_bedrock.invoke_model.return_value = {"body": mock_read}

        generator = ReportGenerator(config, logger)

        with pytest.raises(ReportGenerationError, match="missing sections"):
            generator.generate(sample_item, sample_research)


# --- Test: Retry Behavior ---


class TestRetryBehavior:
    """Test retry behavior with mocked API failures.

    **Validates: Requirements 5.5**
    """

    @patch("src.pipeline.report_generator.time.sleep")
    @patch("src.pipeline.report_generator.boto3.client")
    def test_retries_on_client_error_then_succeeds(
        self, mock_boto_client, mock_sleep, config, logger, sample_item, sample_research
    ):
        """Retries on ClientError and succeeds on subsequent attempt."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        response_text = _well_formed_llm_response()
        mock_response_body = _make_bedrock_response(response_text)

        mock_read = MagicMock()
        mock_read.read.return_value = json.dumps(mock_response_body).encode()

        # Fail once, then succeed
        mock_bedrock.invoke_model.side_effect = [
            _make_client_error(),
            {"body": mock_read},
        ]

        generator = ReportGenerator(config, logger)
        report = generator.generate(sample_item, sample_research)

        assert isinstance(report, Report)
        assert mock_bedrock.invoke_model.call_count == 2
        mock_sleep.assert_called_once_with(_RETRY_DELAY_SECONDS)

    @patch("src.pipeline.report_generator.time.sleep")
    @patch("src.pipeline.report_generator.boto3.client")
    def test_retries_twice_then_raises_on_persistent_failure(
        self, mock_boto_client, mock_sleep, config, logger, sample_item, sample_research
    ):
        """Raises ReportGenerationError after exhausting all retries (3 total attempts)."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        # All attempts fail
        mock_bedrock.invoke_model.side_effect = _make_client_error()

        generator = ReportGenerator(config, logger)

        with pytest.raises(ReportGenerationError, match="failed after"):
            generator.generate(sample_item, sample_research)

        # 1 initial + 2 retries = 3 total attempts
        assert mock_bedrock.invoke_model.call_count == _MAX_RETRIES + 1
        # Sleep called between retries (2 times)
        assert mock_sleep.call_count == _MAX_RETRIES

    @patch("src.pipeline.report_generator.time.sleep")
    @patch("src.pipeline.report_generator.boto3.client")
    def test_retry_delay_is_fixed_one_second(
        self, mock_boto_client, mock_sleep, config, logger, sample_item, sample_research
    ):
        """Each retry uses a fixed 1-second delay."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        mock_bedrock.invoke_model.side_effect = _make_client_error()

        generator = ReportGenerator(config, logger)

        with pytest.raises(ReportGenerationError):
            generator.generate(sample_item, sample_research)

        # All sleep calls should be 1 second
        for call in mock_sleep.call_args_list:
            assert call.args[0] == _RETRY_DELAY_SECONDS

    @patch("src.pipeline.report_generator.time.sleep")
    @patch("src.pipeline.report_generator.boto3.client")
    def test_retries_on_generic_exception(
        self, mock_boto_client, mock_sleep, config, logger, sample_item, sample_research
    ):
        """Retries on non-ClientError exceptions as well."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        response_text = _well_formed_llm_response()
        mock_response_body = _make_bedrock_response(response_text)

        mock_read = MagicMock()
        mock_read.read.return_value = json.dumps(mock_response_body).encode()

        # Fail with generic exception, then succeed
        mock_bedrock.invoke_model.side_effect = [
            RuntimeError("Unexpected error"),
            {"body": mock_read},
        ]

        generator = ReportGenerator(config, logger)
        report = generator.generate(sample_item, sample_research)

        assert isinstance(report, Report)
        assert mock_bedrock.invoke_model.call_count == 2


# --- Test: Prompt Construction ---


class TestPromptConstruction:
    """Test prompt construction includes announcement data and research context.

    **Validates: Requirements 5.1, 5.5**
    """

    @patch("src.pipeline.report_generator.boto3.client")
    def test_prompt_includes_announcement_title(
        self, mock_boto_client, config, logger, sample_item, sample_research
    ):
        """Prompt includes the announcement title."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        generator = ReportGenerator(config, logger)
        prompt = generator._build_prompt(sample_item, sample_research)

        assert sample_item.title in prompt

    @patch("src.pipeline.report_generator.boto3.client")
    def test_prompt_includes_announcement_description(
        self, mock_boto_client, config, logger, sample_item, sample_research
    ):
        """Prompt includes the announcement description."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        generator = ReportGenerator(config, logger)
        prompt = generator._build_prompt(sample_item, sample_research)

        assert sample_item.description in prompt

    @patch("src.pipeline.report_generator.boto3.client")
    def test_prompt_includes_announcement_link(
        self, mock_boto_client, config, logger, sample_item, sample_research
    ):
        """Prompt includes the announcement link."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        generator = ReportGenerator(config, logger)
        prompt = generator._build_prompt(sample_item, sample_research)

        assert sample_item.link in prompt

    @patch("src.pipeline.report_generator.boto3.client")
    def test_prompt_includes_announcement_pub_date(
        self, mock_boto_client, config, logger, sample_item, sample_research
    ):
        """Prompt includes the announcement publication date."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        generator = ReportGenerator(config, logger)
        prompt = generator._build_prompt(sample_item, sample_research)

        assert sample_item.pub_date in prompt

    @patch("src.pipeline.report_generator.boto3.client")
    def test_prompt_includes_research_content(
        self, mock_boto_client, config, logger, sample_item, sample_research
    ):
        """Prompt includes gathered research content."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        generator = ReportGenerator(config, logger)
        prompt = generator._build_prompt(sample_item, sample_research)

        # Research content text should appear in the prompt
        assert "Claude 4 brings improved reasoning" in prompt
        # Research source URL should appear
        assert "https://aws.amazon.com/blogs/aws/bedrock-claude-4" in prompt

    @patch("src.pipeline.report_generator.boto3.client")
    def test_prompt_with_skipped_research(
        self, mock_boto_client, config, logger, sample_item, skipped_research
    ):
        """Prompt indicates research was skipped when applicable."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        generator = ReportGenerator(config, logger)
        prompt = generator._build_prompt(sample_item, skipped_research)

        assert "skipped due to time constraints" in prompt

    @patch("src.pipeline.report_generator.boto3.client")
    def test_prompt_with_empty_research(
        self, mock_boto_client, config, logger, sample_item, empty_research
    ):
        """Prompt indicates no research content when none was gathered."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        generator = ReportGenerator(config, logger)
        prompt = generator._build_prompt(sample_item, empty_research)

        assert "No additional research content" in prompt

    @patch("src.pipeline.report_generator.boto3.client")
    def test_prompt_truncates_long_research_content(
        self, mock_boto_client, config, logger, sample_item
    ):
        """Research content longer than 3000 chars is truncated in the prompt."""
        mock_bedrock = MagicMock()
        mock_boto_client.return_value = mock_bedrock

        long_text = "A" * 5000
        research = ResearchContext(
            gathered_content=[
                PageContent(url="https://example.com", text=long_text, title="Long Page")
            ],
            skipped=False,
        )

        generator = ReportGenerator(config, logger)
        prompt = generator._build_prompt(sample_item, research)

        # The full 5000-char text should NOT appear; only first 3000
        assert "A" * 5000 not in prompt
        assert "A" * 3000 in prompt

"""Unit tests for the RelevanceFilter module."""

import pytest

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import RSSItem
from src.pipeline.relevance_filter import RelevanceFilter


@pytest.fixture
def relevance_filter():
    """Create a RelevanceFilter instance for testing."""
    config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run-id")
    return RelevanceFilter(config=config, logger=logger)


class TestIsRelevant:
    """Tests for the is_relevant method."""

    def test_matches_ai_in_title(self, relevance_filter):
        """Item with 'AI' in title is relevant."""
        item = RSSItem(
            title="New AI features announced",
            description="Some description here",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/1",
        )
        assert relevance_filter.is_relevant(item) is True

    def test_matches_bedrock_in_title(self, relevance_filter):
        """Item mentioning Amazon Bedrock is relevant."""
        item = RSSItem(
            title="Amazon Bedrock now supports new models",
            description="Details about the update",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/2",
        )
        assert relevance_filter.is_relevant(item) is True

    def test_matches_sagemaker_in_description(self, relevance_filter):
        """Item with SageMaker in first 200 chars of description is relevant."""
        item = RSSItem(
            title="AWS announces new feature",
            description="Amazon SageMaker now provides enhanced capabilities for training models.",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/3",
        )
        assert relevance_filter.is_relevant(item) is True

    def test_no_match_unrelated_content(self, relevance_filter):
        """Item with no AI keywords is not relevant."""
        item = RSSItem(
            title="Amazon S3 adds new storage class",
            description="A new storage tier for infrequently accessed data.",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/4",
        )
        assert relevance_filter.is_relevant(item) is False

    def test_word_boundary_prevents_false_positive_said(self, relevance_filter):
        """'AI' inside 'SAID' should not match."""
        item = RSSItem(
            title="AWS said new features are coming",
            description="The company said improvements are planned.",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/5",
        )
        assert relevance_filter.is_relevant(item) is False

    def test_word_boundary_prevents_false_positive_mail(self, relevance_filter):
        """'AI' inside 'MAIL' should not match."""
        item = RSSItem(
            title="Amazon WorkMail adds new features",
            description="Email service improvements for enterprise customers.",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/6",
        )
        assert relevance_filter.is_relevant(item) is False

    def test_exclusion_amazon_connect(self, relevance_filter):
        """Amazon Connect announcements are excluded even with agent keyword."""
        item = RSSItem(
            title="Amazon Connect adds new agent features",
            description="Contact center agents can now use improved tools.",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/7",
        )
        assert relevance_filter.is_relevant(item) is False

    def test_exclusion_overrides_inclusion(self, relevance_filter):
        """Exclusion patterns take priority over inclusion patterns."""
        item = RSSItem(
            title="Amazon Connect AI agent improvements",
            description="New AI-powered features for Connect agents.",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/8",
        )
        assert relevance_filter.is_relevant(item) is False

    def test_description_only_first_200_chars(self, relevance_filter):
        """Keywords after position 200 in description should not trigger match."""
        # Create a description where AI keyword appears only after 200 chars
        padding = "x" * 201
        item = RSSItem(
            title="AWS announces new feature",
            description=padding + " AI powered tools",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/9",
        )
        assert relevance_filter.is_relevant(item) is False

    def test_matches_generative_ai(self, relevance_filter):
        """Item mentioning generative AI is relevant."""
        item = RSSItem(
            title="New generative AI capabilities",
            description="Build with generative AI on AWS.",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/10",
        )
        assert relevance_filter.is_relevant(item) is True

    def test_matches_llm(self, relevance_filter):
        """Item mentioning LLM is relevant."""
        item = RSSItem(
            title="Deploy your LLM on AWS",
            description="New options for large language model deployment.",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/11",
        )
        assert relevance_filter.is_relevant(item) is True

    def test_matches_agentic(self, relevance_filter):
        """Item mentioning agentic AI is relevant."""
        item = RSSItem(
            title="Build agentic AI applications",
            description="New tools for building autonomous agents.",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/12",
        )
        assert relevance_filter.is_relevant(item) is True

    def test_matches_kiro(self, relevance_filter):
        """Item mentioning Kiro is relevant."""
        item = RSSItem(
            title="Introducing Kiro",
            description="A new AI-powered development environment.",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/13",
        )
        assert relevance_filter.is_relevant(item) is True

    def test_case_insensitive_matching(self, relevance_filter):
        """Matching is case-insensitive."""
        item = RSSItem(
            title="AMAZON BEDROCK NOW SUPPORTS NEW MODELS",
            description="DETAILS ABOUT THE UPDATE",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/14",
        )
        assert relevance_filter.is_relevant(item) is True


class TestFilter:
    """Tests for the filter method."""

    def test_returns_only_relevant_items(self, relevance_filter):
        """filter() returns only items that pass is_relevant."""
        items = [
            RSSItem(
                title="Amazon Bedrock update",
                description="New models available.",
                pub_date="2025-01-15",
                link="https://aws.amazon.com/whats-new/1",
            ),
            RSSItem(
                title="Amazon S3 storage class",
                description="New tier for cold data.",
                pub_date="2025-01-15",
                link="https://aws.amazon.com/whats-new/2",
            ),
            RSSItem(
                title="SageMaker improvements",
                description="Training enhancements.",
                pub_date="2025-01-15",
                link="https://aws.amazon.com/whats-new/3",
            ),
        ]
        result = relevance_filter.filter(items)
        assert len(result) == 2
        assert result[0].title == "Amazon Bedrock update"
        assert result[1].title == "SageMaker improvements"

    def test_empty_input_returns_empty(self, relevance_filter):
        """filter() with empty list returns empty list."""
        result = relevance_filter.filter([])
        assert result == []

    def test_no_relevant_items(self, relevance_filter):
        """filter() returns empty when no items are relevant."""
        items = [
            RSSItem(
                title="Amazon RDS update",
                description="Database improvements.",
                pub_date="2025-01-15",
                link="https://aws.amazon.com/whats-new/1",
            ),
            RSSItem(
                title="AWS CloudFormation feature",
                description="Infrastructure as code updates.",
                pub_date="2025-01-15",
                link="https://aws.amazon.com/whats-new/2",
            ),
        ]
        result = relevance_filter.filter(items)
        assert result == []

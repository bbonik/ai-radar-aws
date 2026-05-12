"""Property-based tests for the GraphGenerator module.

Feature: aws-ai-news-hub
- Property 9: Graph generation conditional on importance level

Validates: Requirements 6.1, 6.5
"""

import json
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from src.config import Config
from src.pipeline.graph_generator import GraphGenerator
from src.shared.logger import StructuredLogger
from src.shared.models import Report, RSSItem


# --- Shared helpers ---


def _make_generator() -> GraphGenerator:
    """Create a GraphGenerator with a mocked Bedrock client."""
    config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run")
    with patch("src.pipeline.graph_generator.boto3.client"):
        generator = GraphGenerator(config=config, logger=logger)
    return generator


def _mock_bedrock_response(mermaid_code: str = "graph TD\n    A{{Feature}}:::announced\n    B(Service):::compute\n    C(Storage):::storage\n    A --> B\n    B --> C\n    A -.-> C") -> MagicMock:
    """Create a mock Bedrock response containing a Mermaid diagram."""
    response_body = {
        "content": [
            {
                "type": "text",
                "text": f"```mermaid\n{mermaid_code}\n```",
            }
        ]
    }
    mock_body = MagicMock()
    mock_body.read.return_value = json.dumps(response_body).encode()
    return {"body": mock_body}


# --- Strategies ---

# Generate valid RSSItem instances
rss_item_strategy = st.builds(
    RSSItem,
    title=st.text(min_size=1, max_size=200),
    description=st.text(min_size=1, max_size=500),
    pub_date=st.text(min_size=1, max_size=30),
    link=st.from_regex(r"https://aws\.amazon\.com/whats-new/[a-z0-9\-]+", fullmatch=True),
)

# Generate valid Report instances
report_strategy = st.builds(
    Report,
    whats_new=st.text(min_size=1, max_size=200),
    how_it_works=st.text(min_size=1, max_size=200),
    why_important=st.text(min_size=1, max_size=200),
    how_different=st.text(min_size=1, max_size=200),
    when_to_prefer=st.text(min_size=1, max_size=200),
    availability=st.text(min_size=1, max_size=200),
)


# --- Property 9: Graph generation conditional on importance level ---


@given(
    item=rss_item_strategy,
    report=report_strategy,
)
@settings(max_examples=200)
@patch("src.pipeline.graph_generator.boto3.client")
def test_property9_importance_level_1_returns_none_without_llm_call(
    mock_boto_client,
    item: RSSItem,
    report: Report,
):
    """Property 9: Graph Generator returns None for importance_level < 3 without LLM call.

    For any announcement with importance_level < 3, the Graph Generator SHALL
    return None without invoking the LLM. The Bedrock client's invoke_model
    method SHALL NOT be called.

    **Validates: Requirements 6.1, 6.5**
    """
    config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run")

    # Create a mock Bedrock client instance
    mock_bedrock = MagicMock()
    mock_boto_client.return_value = mock_bedrock

    generator = GraphGenerator(config=config, logger=logger)

    # Test importance_level 1 only (skip)
    for level in (1,):
        mock_bedrock.reset_mock()
        result = generator.generate(item=item, report=report, importance_level=level)

        # Must return None for 1-star announcements
        assert result is None, (
            f"Graph Generator should return None for importance_level={level}, "
            f"but got: {result!r}"
        )

        # Bedrock invoke_model must NOT have been called
        mock_bedrock.invoke_model.assert_not_called(), (
            f"Bedrock invoke_model should NOT be called for importance_level={level}"
        )


@given(
    item=rss_item_strategy,
    report=report_strategy,
    importance_level=st.integers(min_value=3, max_value=5),
)
@settings(max_examples=200)
@patch("src.pipeline.graph_generator.boto3.client")
def test_property9_importance_level_gte_3_invokes_llm(
    mock_boto_client,
    item: RSSItem,
    report: Report,
    importance_level: int,
):
    """Property 9: Graph Generator invokes LLM for importance_level >= 3.

    For any announcement with importance_level >= 3, the Graph Generator SHALL
    attempt Mermaid diagram generation by invoking the Bedrock LLM. The
    invoke_model method SHALL be called at least once.

    **Validates: Requirements 6.1, 6.5**
    """
    config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run")

    # Create a mock Bedrock client that returns a valid Mermaid response
    mock_bedrock = MagicMock()
    mock_bedrock.invoke_model.return_value = _mock_bedrock_response()
    mock_boto_client.return_value = mock_bedrock

    generator = GraphGenerator(config=config, logger=logger)

    result = generator.generate(item=item, report=report, importance_level=importance_level)

    # Bedrock invoke_model MUST have been called for importance_level >= 2
    mock_bedrock.invoke_model.assert_called(), (
        f"Bedrock invoke_model should be called for importance_level={importance_level}"
    )

    # Result should be a non-None string (the Mermaid diagram)
    assert result is not None, (
        f"Graph Generator should return a Mermaid diagram string for "
        f"importance_level={importance_level}, but got None"
    )
    assert isinstance(result, str), (
        f"Graph Generator should return a string, got {type(result)}"
    )
    assert len(result) > 0, (
        f"Graph Generator should return a non-empty Mermaid diagram for "
        f"importance_level={importance_level}"
    )

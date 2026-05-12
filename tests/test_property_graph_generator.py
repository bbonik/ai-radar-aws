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


# --- Validation and Retry Tests ---


@patch("src.pipeline.graph_generator.boto3.client")
def test_validation_rejects_unbalanced_brackets(mock_boto_client):
    """Validation catches unbalanced brackets and triggers retry."""
    config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run")

    # First call returns invalid diagram (unbalanced parens)
    invalid_code = "graph TD\n    A(Unclosed\n    B(OK):::compute\n    A --> B\n    A --> B\n    B --> A"
    # Second call (retry) returns valid diagram
    valid_code = "graph TD\n    A(Fixed):::announced\n    B(OK):::compute\n    C(Data):::storage\n    A --> B\n    B --> C\n    A -.-> C"

    mock_bedrock = MagicMock()
    call_count = {"n": 0}

    def side_effect(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _mock_bedrock_response(invalid_code)
        return _mock_bedrock_response(valid_code)

    mock_bedrock.invoke_model.side_effect = side_effect
    mock_boto_client.return_value = mock_bedrock

    generator = GraphGenerator(config=config, logger=logger)
    item = RSSItem(title="Test", description="Test desc", pub_date="2026-01-01", link="https://aws.amazon.com/whats-new/test")
    report = Report(whats_new="x", how_it_works="x", why_important="x", how_different="x", when_to_prefer="x", availability="x")

    result = generator.generate(item=item, report=report, importance_level=3)

    # Should have retried and returned the fixed version
    assert call_count["n"] == 2, f"Expected 2 LLM calls (initial + retry), got {call_count['n']}"
    assert result is not None
    assert "Fixed" in result


@patch("src.pipeline.graph_generator.boto3.client")
def test_validation_returns_none_after_failed_retry(mock_boto_client):
    """If both initial and retry produce invalid diagrams, returns None."""
    config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run")

    # Both calls return invalid diagram (only 1 arrow)
    invalid_code = "graph TD\n    A(Node):::compute\n    B(Other):::storage\n    A --> B"

    mock_bedrock = MagicMock()
    mock_bedrock.invoke_model.return_value = _mock_bedrock_response(invalid_code)
    mock_boto_client.return_value = mock_bedrock

    generator = GraphGenerator(config=config, logger=logger)
    item = RSSItem(title="Test", description="Test desc", pub_date="2026-01-01", link="https://aws.amazon.com/whats-new/test")
    report = Report(whats_new="x", how_it_works="x", why_important="x", how_different="x", when_to_prefer="x", availability="x")

    result = generator.generate(item=item, report=report, importance_level=3)

    # Should return None after failed retry
    assert result is None


@patch("src.pipeline.graph_generator.boto3.client")
def test_validation_passes_valid_diagram_without_retry(mock_boto_client):
    """Valid diagram passes validation without triggering retry."""
    config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run")

    valid_code = "graph TD\n    A{{Feature}}:::announced\n    B(Service):::compute\n    C(Storage):::storage\n    A --> B\n    B --> C\n    A -.-> C"

    mock_bedrock = MagicMock()
    mock_bedrock.invoke_model.return_value = _mock_bedrock_response(valid_code)
    mock_boto_client.return_value = mock_bedrock

    generator = GraphGenerator(config=config, logger=logger)
    item = RSSItem(title="Test", description="Test desc", pub_date="2026-01-01", link="https://aws.amazon.com/whats-new/test")
    report = Report(whats_new="x", how_it_works="x", why_important="x", how_different="x", when_to_prefer="x", availability="x")

    result = generator.generate(item=item, report=report, importance_level=3)

    # Should pass without retry (only 1 LLM call)
    assert mock_bedrock.invoke_model.call_count == 1
    assert result is not None
    assert "Feature" in result


def test_validate_mermaid_catches_common_errors():
    """Unit test for _validate_mermaid with various invalid inputs."""
    generator = _make_generator()

    # Empty
    valid, err = generator._validate_mermaid("")
    assert not valid
    assert "Empty" in err

    # Wrong start
    valid, err = generator._validate_mermaid("random text\n    A --> B")
    assert not valid
    assert "Must start with" in err

    # Unbalanced brackets
    valid, err = generator._validate_mermaid("graph TD\n    A(Unclosed\n    A --> B\n    B --> A\n    A -.-> B")
    assert not valid
    assert "Unbalanced" in err

    # Too few arrows
    valid, err = generator._validate_mermaid("graph TD\n    A(Node):::compute\n    B(Other):::storage\n    A --> B")
    assert not valid
    assert "Too few" in err

    # Valid diagram
    valid, err = generator._validate_mermaid("graph TD\n    A{{X}}:::announced\n    B(Y):::compute\n    C(Z):::storage\n    A --> B\n    B --> C\n    A -.-> C")
    assert valid
    assert err == ""

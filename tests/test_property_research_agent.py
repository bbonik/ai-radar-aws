"""Property-based tests for the ResearchAgent module.

Feature: aws-ai-news-hub
- Property 7: Research agent respects remaining execution time

Validates: Requirements 4.7, 4.8
"""

from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from src.config import Config
from src.pipeline.research_agent import ResearchAgent, _SAFETY_MARGIN_MS
from src.shared.logger import StructuredLogger
from src.shared.models import RSSItem


# --- Shared helpers ---


def _make_agent(remaining_ms: int, config: Config | None = None) -> ResearchAgent:
    """Create a ResearchAgent with a mock Lambda context returning the given remaining time."""
    if config is None:
        config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run")
    context = MagicMock()
    context.get_remaining_time_in_millis.return_value = remaining_ms
    return ResearchAgent(config=config, context=context, logger=logger)


def _sample_item() -> RSSItem:
    """Create a minimal RSSItem for testing."""
    return RSSItem(
        title="Amazon Bedrock now supports new models",
        description="Check out the details.",
        pub_date="2025-01-15",
        link="https://aws.amazon.com/about-aws/whats-new/2025/01/bedrock-new-models",
    )


# --- Property 7: Research agent respects remaining execution time ---


@given(
    remaining_ms=st.integers(min_value=0, max_value=900_000),
    research_timeout=st.integers(min_value=1, max_value=600),
)
@settings(max_examples=200)
def test_property7_skip_decision_matches_threshold_formula(
    remaining_ms: int,
    research_timeout: int,
):
    """Property 7: Research agent respects remaining execution time.

    For any Lambda execution context with a given remaining time in milliseconds,
    and a configured per-announcement research timeout, the Research Agent SHALL
    skip research if remaining_time_ms < (research_timeout_per_announcement × 1000 + safety_margin).

    The skip decision is deterministic: if remaining time is below the threshold,
    research is skipped (returns ResearchContext with skipped=True and empty gathered_content).
    If remaining time is at or above the threshold, research proceeds.

    **Validates: Requirements 4.7, 4.8**
    """
    config = Config()
    config.research_timeout_per_announcement = research_timeout

    agent = _make_agent(remaining_ms=remaining_ms, config=config)

    # Compute the threshold
    threshold_ms = research_timeout * 1000 + _SAFETY_MARGIN_MS

    # Check the internal decision
    has_time = agent._has_sufficient_time()

    if remaining_ms >= threshold_ms:
        assert has_time is True, (
            f"Agent should proceed when remaining_ms={remaining_ms} >= "
            f"threshold={threshold_ms} (timeout={research_timeout}s × 1000 + "
            f"safety_margin={_SAFETY_MARGIN_MS}ms)"
        )
    else:
        assert has_time is False, (
            f"Agent should skip when remaining_ms={remaining_ms} < "
            f"threshold={threshold_ms} (timeout={research_timeout}s × 1000 + "
            f"safety_margin={_SAFETY_MARGIN_MS}ms)"
        )


@given(
    remaining_ms=st.integers(min_value=0, max_value=900_000),
    research_timeout=st.integers(min_value=1, max_value=600),
)
@settings(max_examples=200)
def test_property7_research_returns_skipped_when_time_insufficient(
    remaining_ms: int,
    research_timeout: int,
):
    """Property 7: Research method returns skipped=True when time is insufficient.

    When the remaining Lambda execution time is below the threshold, the research()
    method SHALL return a ResearchContext with skipped=True and gathered_content=[].

    **Validates: Requirements 4.7, 4.8**
    """
    config = Config()
    config.research_timeout_per_announcement = research_timeout

    threshold_ms = research_timeout * 1000 + _SAFETY_MARGIN_MS

    # Only test the insufficient-time case
    if remaining_ms >= threshold_ms:
        return  # Skip this case — tested in the proceed test below

    agent = _make_agent(remaining_ms=remaining_ms, config=config)
    item = _sample_item()

    result = agent.research(item)

    assert result.skipped is True, (
        f"research() should return skipped=True when remaining_ms={remaining_ms} < "
        f"threshold={threshold_ms}"
    )
    assert result.gathered_content == [], (
        f"research() should return empty gathered_content when skipped, "
        f"got {len(result.gathered_content)} items"
    )


@given(
    remaining_ms=st.integers(min_value=0, max_value=900_000),
    research_timeout=st.integers(min_value=1, max_value=600),
)
@settings(max_examples=200)
@patch("src.pipeline.research_agent.urlopen")
def test_property7_research_proceeds_when_time_sufficient(
    mock_urlopen,
    remaining_ms: int,
    research_timeout: int,
):
    """Property 7: Research method proceeds when time is sufficient.

    When the remaining Lambda execution time is at or above the threshold,
    the research() method SHALL NOT skip — it proceeds with URL fetching.
    The skipped flag SHALL be False.

    **Validates: Requirements 4.7, 4.8**
    """
    config = Config()
    config.research_timeout_per_announcement = research_timeout

    threshold_ms = research_timeout * 1000 + _SAFETY_MARGIN_MS

    # Only test the sufficient-time case
    if remaining_ms < threshold_ms:
        return  # Skip this case — tested in the skip test above

    # Mock urlopen to return a simple HTML response
    mock_response = MagicMock()
    mock_response.read.return_value = b"<html><head><title>Test</title></head><body><p>Content</p></body></html>"
    mock_response.headers.get.return_value = "text/html; charset=utf-8"
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_response

    agent = _make_agent(remaining_ms=remaining_ms, config=config)
    item = _sample_item()

    result = agent.research(item)

    assert result.skipped is False, (
        f"research() should NOT skip when remaining_ms={remaining_ms} >= "
        f"threshold={threshold_ms}"
    )


@given(
    remaining_ms=st.integers(min_value=0, max_value=900_000),
)
@settings(max_examples=100)
def test_property7_default_config_threshold_is_330_seconds(
    remaining_ms: int,
):
    """Property 7: With default config (300s timeout), threshold is 330,000ms.

    The default research_timeout_per_announcement is 300 seconds.
    Combined with the 30,000ms safety margin, the threshold is 330,000ms.
    This verifies the formula works correctly with the default configuration.

    **Validates: Requirements 4.7, 4.8**
    """
    config = Config()
    agent = _make_agent(remaining_ms=remaining_ms, config=config)

    expected_threshold = 300 * 1000 + 30_000  # 330,000 ms
    has_time = agent._has_sufficient_time()

    if remaining_ms >= expected_threshold:
        assert has_time is True, (
            f"With default config, agent should proceed when "
            f"remaining_ms={remaining_ms} >= 330,000ms"
        )
    else:
        assert has_time is False, (
            f"With default config, agent should skip when "
            f"remaining_ms={remaining_ms} < 330,000ms"
        )

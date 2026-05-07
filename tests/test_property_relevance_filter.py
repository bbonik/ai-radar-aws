"""Property-based tests for the RelevanceFilter module.

Feature: aws-ai-news-hub
- Property 2: Relevance filter correctly classifies items
- Property 3: Word-boundary matching prevents false positives
- Property 4: Exclusion patterns override inclusion

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5
"""

import string

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.config import Config
from src.pipeline.relevance_filter import RelevanceFilter
from src.shared.logger import StructuredLogger
from src.shared.models import RSSItem


# --- Shared fixtures ---

def _make_filter() -> RelevanceFilter:
    """Create a RelevanceFilter instance for testing."""
    config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run")
    return RelevanceFilter(config=config, logger=logger)


# Known AI/ML keywords that use simple word-boundary matching.
# These are keywords that, when surrounded by word boundaries, should trigger relevance.
_INCLUSION_KEYWORDS = [
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "neural network",
    "generative ai",
    "gen ai",
    "ai",
    "ml",
    "llm",
    "openai",
    "anthropic",
    "qwen",
    "nova",
    "amazon nova",
    "amazon bedrock",
    "bedrock",
    "amazon sagemaker",
    "sagemaker",
    "amazon comprehend",
    "comprehend",
    "amazon rekognition",
    "rekognition",
    "amazon textract",
    "textract",
    "amazon polly",
    "polly",
    "amazon lex",
    "lex",
    "amazon translate",
    "translate",
    "amazon transcribe",
    "transcribe",
    "amazon personalize",
    "personalize",
    "amazon forecast",
    "forecast",
    "amazon kendra",
    "kendra",
    "fraud detector",
    "amazon q",
    "q developer",
    "q business",
    "amazon quicksight",
    "quicksight q",
    "amazon quick suite",
    "quick suite",
    "agentcore",
    "agent core",
    "kiro",
    "computer vision",
    "natural language",
    "text analysis",
    "sentiment analysis",
    "image recognition",
    "speech recognition",
    "voice synthesis",
    "text-to-speech",
    "speech-to-text",
    "agents",
    "agentic ai",
    "agentic",
    "ai agent",
    "ai agents",
    "intelligent agent",
    "autonomous agent",
    "multi-agent",
]

# Exclusion patterns that should cause rejection
_EXCLUSION_PHRASES = [
    "amazon connect",
]

# Short keywords that are prone to substring false positives
_SHORT_KEYWORDS = ["ai", "ml"]

# Safe text characters that won't accidentally form AI keywords
_SAFE_CHARS = "bcdfghjkpqruvwxyz0123456789 "


# Strategy: generate text that does NOT contain any AI keyword as a standalone word
def _non_ai_text_strategy():
    """Generate text guaranteed to not contain any AI/ML keyword as a standalone word."""
    # Use words that cannot accidentally form AI keywords
    safe_words = [
        "amazon", "storage", "bucket", "cloud", "network", "database",
        "server", "compute", "deploy", "update", "feature", "service",
        "region", "global", "data", "file", "object", "table",
        "cluster", "node", "endpoint", "gateway", "route", "vpc",
    ]
    return st.lists(
        st.sampled_from(safe_words),
        min_size=3,
        max_size=15,
    ).map(lambda words: " ".join(words))


# Strategy: generate padding text that won't accidentally match keywords
def _safe_padding_strategy(min_size=1, max_size=50):
    """Generate padding text that won't accidentally match any inclusion keyword."""
    safe_words = [
        "the", "new", "for", "with", "from", "this", "that", "have",
        "been", "updated", "now", "supports", "added", "feature",
        "cloud", "storage", "bucket", "server", "deploy", "region",
    ]
    return st.lists(
        st.sampled_from(safe_words),
        min_size=max(1, min_size // 5),
        max_size=max(2, max_size // 5),
    ).map(lambda words: " ".join(words))


# --- Property 2: Relevance filter correctly classifies items ---


@given(
    keyword=st.sampled_from(_INCLUSION_KEYWORDS),
    prefix=_safe_padding_strategy(min_size=0, max_size=30),
    suffix=_safe_padding_strategy(min_size=0, max_size=30),
    place_in_title=st.booleans(),
)
@settings(max_examples=100)
def test_property2_relevant_items_with_keyword_are_classified_relevant(
    keyword: str, prefix: str, suffix: str, place_in_title: bool
):
    """Property 2: Relevance filter correctly classifies items.

    For any RSS item whose title or first 200 characters of description contain
    at least one AI/ML/GenAI keyword (matched with word boundaries) and zero
    exclusion pattern matches, the filter SHALL mark the item as relevant.

    **Validates: Requirements 2.1, 2.5**
    """
    rf = _make_filter()

    # Build text with the keyword placed at a random position
    text_with_keyword = f"{prefix} {keyword} {suffix}".strip()

    # Ensure the text with keyword fits within 200 chars for description placement
    if place_in_title:
        item = RSSItem(
            title=text_with_keyword,
            description="Some generic description about cloud services.",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/test",
        )
    else:
        # Ensure keyword is within first 200 chars of description
        desc = text_with_keyword[:199]
        item = RSSItem(
            title="AWS announces new feature",
            description=desc,
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/test",
        )

    # Ensure no exclusion pattern matches
    combined_text = item.title + " " + item.description[:200]
    for excl in _EXCLUSION_PHRASES:
        assume(excl not in combined_text.lower())
    # Also check the connect+agent patterns
    assume("connect" not in combined_text.lower() or "agent" not in combined_text.lower())

    assert rf.is_relevant(item) is True, (
        f"Item with keyword '{keyword}' should be relevant. "
        f"Title: '{item.title}', Description: '{item.description[:200]}'"
    )


@given(text=_non_ai_text_strategy())
@settings(max_examples=100)
def test_property2_items_without_keywords_are_not_relevant(text: str):
    """Property 2 (converse): Items with no keyword matches are NOT relevant.

    For any item with no keyword matches, the filter SHALL mark it as not relevant.

    **Validates: Requirements 2.1, 2.5**
    """
    rf = _make_filter()

    item = RSSItem(
        title=text,
        description=text,
        pub_date="2025-01-15",
        link="https://aws.amazon.com/whats-new/test",
    )

    assert rf.is_relevant(item) is False, (
        f"Item without AI keywords should NOT be relevant. "
        f"Title: '{item.title}'"
    )


# --- Property 3: Word-boundary matching prevents false positives ---


@given(
    keyword=st.sampled_from(_SHORT_KEYWORDS),
    prefix_chars=st.text(
        alphabet=string.ascii_lowercase,
        min_size=1,
        max_size=5,
    ),
    suffix_chars=st.text(
        alphabet=string.ascii_lowercase,
        min_size=1,
        max_size=5,
    ),
)
@settings(max_examples=100)
def test_property3_substring_keywords_do_not_match(
    keyword: str, prefix_chars: str, suffix_chars: str
):
    """Property 3: Word-boundary matching prevents false positives.

    For any string where an AI keyword appears only as a substring within a
    larger word (e.g., "SAID", "FAIR", "MAIL") and not as a standalone word,
    the filter SHALL NOT match.

    **Validates: Requirements 2.2, 2.3**
    """
    rf = _make_filter()

    # Create a word that contains the keyword as a substring but is not standalone
    embedded_word = prefix_chars + keyword + suffix_chars

    # Ensure the embedded word itself is not accidentally another keyword
    assume(embedded_word.lower() not in [k.lower() for k in _INCLUSION_KEYWORDS])

    # Build text using only the embedded word (no standalone keywords)
    title = f"AWS announces {embedded_word} feature update"
    description = f"The {embedded_word} service has been updated with new capabilities."

    # Verify no standalone keyword accidentally appears in our constructed text
    # by checking the full combined text
    combined = title + " " + description[:200]
    for kw in _INCLUSION_KEYWORDS:
        # Check if keyword appears as standalone (with word boundaries)
        import re
        if re.search(r'\b' + re.escape(kw) + r'\b', combined, re.IGNORECASE):
            # Skip this example - our safe text accidentally contains a keyword
            assume(False)

    item = RSSItem(
        title=title,
        description=description,
        pub_date="2025-01-15",
        link="https://aws.amazon.com/whats-new/test",
    )

    assert rf.is_relevant(item) is False, (
        f"Keyword '{keyword}' embedded in '{embedded_word}' should NOT trigger match. "
        f"Title: '{item.title}'"
    )


@given(
    keyword=st.sampled_from(_INCLUSION_KEYWORDS),
    padding_length=st.integers(min_value=201, max_value=300),
)
@settings(max_examples=100)
def test_property3_keywords_after_position_200_do_not_match(
    keyword: str, padding_length: int
):
    """Property 3: Keywords after position 200 in description do not trigger match.

    Keywords appearing after position 200 in the description SHALL NOT trigger
    a match.

    **Validates: Requirements 2.2, 2.3**
    """
    rf = _make_filter()

    # Create padding that fills the first 200+ chars with safe text
    padding = "x" * padding_length

    # Place keyword only after position 200 in description
    description = padding + " " + keyword + " more text here"

    # Title must also not contain any keywords
    title = "AWS announces new storage feature update"

    # Verify title doesn't accidentally match
    import re
    for kw in _INCLUSION_KEYWORDS:
        assume(not re.search(r'\b' + re.escape(kw) + r'\b', title, re.IGNORECASE))

    item = RSSItem(
        title=title,
        description=description,
        pub_date="2025-01-15",
        link="https://aws.amazon.com/whats-new/test",
    )

    assert rf.is_relevant(item) is False, (
        f"Keyword '{keyword}' at position {padding_length + 1} in description "
        f"should NOT trigger match."
    )


# --- Property 4: Exclusion patterns override inclusion ---


@given(
    keyword=st.sampled_from(_INCLUSION_KEYWORDS),
    place_in_title=st.booleans(),
)
@settings(max_examples=100)
def test_property4_exclusion_overrides_inclusion(
    keyword: str, place_in_title: bool
):
    """Property 4: Exclusion patterns override inclusion.

    For any RSS item that matches both an inclusion pattern and an exclusion
    pattern, the filter SHALL mark the item as NOT relevant, regardless of
    how many inclusion patterns it matches.

    **Validates: Requirements 2.4**
    """
    rf = _make_filter()

    # "Amazon Connect" is an exclusion pattern
    exclusion_text = "Amazon Connect"

    if place_in_title:
        # Put both the exclusion and inclusion keyword in the title
        title = f"{exclusion_text} now supports {keyword} capabilities"
        description = "Contact center improvements with new features."
    else:
        # Put exclusion in title, keyword in description (within 200 chars)
        title = f"{exclusion_text} announces new features"
        description = f"New {keyword} powered capabilities for contact centers."

    item = RSSItem(
        title=title,
        description=description,
        pub_date="2025-01-15",
        link="https://aws.amazon.com/whats-new/test",
    )

    assert rf.is_relevant(item) is False, (
        f"Item matching exclusion '{exclusion_text}' AND inclusion '{keyword}' "
        f"should NOT be relevant. Title: '{item.title}'"
    )

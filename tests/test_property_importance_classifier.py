"""Property-based tests for the ImportanceClassifier module.

Feature: aws-ai-news-hub
- Property 5: Importance score is additive sum of factors
- Property 6: Star level determined by threshold comparison

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.config import Config
from src.pipeline.importance_classifier import ImportanceClassifier
from src.shared.logger import StructuredLogger
from src.shared.models import RSSItem


# --- Shared fixtures ---


def _make_classifier(config: Config | None = None) -> ImportanceClassifier:
    """Create an ImportanceClassifier instance for testing."""
    if config is None:
        config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run")
    return ImportanceClassifier(config=config, logger=logger)


# Service names by tier (title-cased as they appear after extraction)
_HIGH_TIER_SERVICES = [
    "Amazon Bedrock",
    "Amazon Bedrock Agentcore",
    "Amazon Sagemaker Ai",
    "Amazon Quicksight",
]

_MEDIUM_TIER_SERVICES = [
    "Sagemaker",
    "Sagemaker Unified Studio",
    "Kiro",
]

_BASE_TIER_SERVICES = [
    "Other",
]


# Strategy: generate a word list of a given size (for controlled word count)
def _words_strategy(min_words: int = 1, max_words: int = 500):
    """Generate a description with a controlled number of words."""
    return st.lists(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz",
            min_size=3,
            max_size=8,
        ),
        min_size=min_words,
        max_size=max_words,
    ).map(lambda words: " ".join(words))


# Strategy: generate config with valid scoring parameters
def _config_strategy():
    """Generate a Config with randomized but valid scoring parameters."""
    return st.builds(
        Config,
        service_points_high=st.integers(min_value=1, max_value=10),
        service_points_medium=st.integers(min_value=1, max_value=10),
        service_points_base=st.integers(min_value=0, max_value=5),
        blogpost_points=st.integers(min_value=0, max_value=10),
        word_count_scale=st.floats(min_value=0.001, max_value=0.1, allow_nan=False, allow_infinity=False),
        threshold_2_star=st.floats(min_value=1.0, max_value=10.0, allow_nan=False, allow_infinity=False),
        threshold_3_star=st.floats(min_value=10.1, max_value=20.0, allow_nan=False, allow_infinity=False),
    )


# --- Property 5: Importance score is additive sum of factors ---


@given(
    service_keyword=st.sampled_from(
        [("amazon bedrock", "high"),
         ("amazon bedrock agentcore", "high"),
         ("amazon sagemaker ai", "high"),
         ("amazon quicksight", "high"),
         ("sagemaker unified studio", "medium"),
         ("kiro", "medium"),
         ]
    ),
    word_count=st.integers(min_value=1, max_value=500),
    has_blogpost=st.booleans(),
)
@settings(max_examples=100)
def test_property5_score_is_additive_sum_known_services(
    service_keyword: tuple[str, str],
    word_count: int,
    has_blogpost: bool,
):
    """Property 5: Importance score is additive sum of factors.

    For any announcement with a known service tier, blogpost link presence,
    and word count, the computed Importance_Score SHALL equal:
    service_tier_points + (blogpost_points if has_links else 0) + (word_count × word_count_scale).

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    """
    config = Config()
    classifier = _make_classifier(config)

    keyword, tier = service_keyword

    # Determine expected service points
    if tier == "high":
        expected_service_points = config.service_points_high
    elif tier == "medium":
        expected_service_points = config.service_points_medium
    else:
        expected_service_points = config.service_points_base

    # Build description with exact word count
    # Use safe words that won't accidentally match other service keywords
    words = ["word"] * word_count

    # Add blogpost link if needed (external URL that's not an AWS whats-new link)
    if has_blogpost:
        words[0] = "https://aws.amazon.com/blogs/machine-learning/example-post"

    description = " ".join(words)

    # Place the service keyword in the title to ensure correct tier detection
    title = f"{keyword} announces new feature"

    item = RSSItem(
        title=title,
        description=description,
        pub_date="2025-01-15",
        link="https://aws.amazon.com/about-aws/whats-new/2025/01/test",
    )

    # Compute actual score
    actual_score = classifier.compute_score(item)

    # Compute expected score
    expected_blogpost_points = config.blogpost_points if has_blogpost else 0
    actual_word_count = len(description.split())
    expected_word_contribution = actual_word_count * config.word_count_scale
    expected_score = expected_service_points + expected_blogpost_points + expected_word_contribution

    assert abs(actual_score - expected_score) < 1e-9, (
        f"Score mismatch for service '{keyword}' (tier={tier}). "
        f"Expected: {expected_score} = {expected_service_points} + {expected_blogpost_points} + "
        f"({actual_word_count} × {config.word_count_scale}), Got: {actual_score}"
    )


@given(
    word_count=st.integers(min_value=1, max_value=500),
    has_blogpost=st.booleans(),
)
@settings(max_examples=100)
def test_property5_score_is_additive_sum_base_tier(
    word_count: int,
    has_blogpost: bool,
):
    """Property 5: Importance score is additive for base-tier (Other) services.

    For announcements that don't match any known high/medium service,
    the base tier points are used in the additive formula.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    """
    config = Config()
    classifier = _make_classifier(config)

    # Use a title that won't match any known service
    title = "AWS announces new storage feature update"

    # Build description with exact word count
    words = ["word"] * word_count
    if has_blogpost:
        words[0] = "https://aws.amazon.com/blogs/storage/example-post"

    description = " ".join(words)

    item = RSSItem(
        title=title,
        description=description,
        pub_date="2025-01-15",
        link="https://aws.amazon.com/about-aws/whats-new/2025/01/test",
    )

    actual_score = classifier.compute_score(item)

    expected_blogpost_points = config.blogpost_points if has_blogpost else 0
    actual_word_count = len(description.split())
    expected_word_contribution = actual_word_count * config.word_count_scale
    expected_score = config.service_points_base + expected_blogpost_points + expected_word_contribution

    assert abs(actual_score - expected_score) < 1e-9, (
        f"Score mismatch for base tier. "
        f"Expected: {expected_score} = {config.service_points_base} + {expected_blogpost_points} + "
        f"({actual_word_count} × {config.word_count_scale}), Got: {actual_score}"
    )


@given(
    config=_config_strategy(),
    word_count=st.integers(min_value=10, max_value=300),
    has_blogpost=st.booleans(),
    tier=st.sampled_from(["high", "medium", "base"]),
)
@settings(max_examples=100)
def test_property5_score_additive_with_varied_config(
    config: Config,
    word_count: int,
    has_blogpost: bool,
    tier: str,
):
    """Property 5: Score additivity holds across different config values.

    The additive formula must hold regardless of the specific config values
    for service points, blogpost points, and word count scale.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    """
    # Ensure threshold_3_star > threshold_2_star
    assume(config.threshold_3_star > config.threshold_2_star)

    classifier = _make_classifier(config)

    # Select service keyword based on tier
    if tier == "high":
        title = "Amazon Bedrock announces new feature"
        expected_service_points = config.service_points_high
    elif tier == "medium":
        title = "Kiro announces new feature"
        expected_service_points = config.service_points_medium
    else:
        title = "AWS announces new storage feature update"
        expected_service_points = config.service_points_base

    # Build description with controlled word count
    words = ["word"] * word_count
    if has_blogpost:
        words[0] = "https://aws.amazon.com/blogs/machine-learning/example-post"

    description = " ".join(words)

    item = RSSItem(
        title=title,
        description=description,
        pub_date="2025-01-15",
        link="https://aws.amazon.com/about-aws/whats-new/2025/01/test",
    )

    actual_score = classifier.compute_score(item)

    expected_blogpost_points = config.blogpost_points if has_blogpost else 0
    actual_word_count = len(description.split())
    expected_word_contribution = actual_word_count * config.word_count_scale
    expected_score = expected_service_points + expected_blogpost_points + expected_word_contribution

    assert abs(actual_score - expected_score) < 1e-9, (
        f"Score mismatch with custom config (tier={tier}). "
        f"Expected: {expected_score}, Got: {actual_score}"
    )


# --- Property 6: Star level determined by threshold comparison ---


@given(
    score=st.floats(min_value=-10.0, max_value=100.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200)
def test_property6_star_level_by_threshold(score: float):
    """Property 6: Star level determined by threshold comparison.

    For any computed Importance_Score, the assigned star level SHALL be:
    - 1-star if score < threshold_2_star
    - 2-star if threshold_2_star <= score < threshold_3_star
    - 3-star if score >= threshold_3_star

    The result is always exactly one of {1, 2, 3}.

    **Validates: Requirements 3.5, 3.6**
    """
    config = Config()
    classifier = _make_classifier(config)

    # Directly test the _score_to_stars method
    star_level = classifier._score_to_stars(score)

    # Verify the result is always one of {1, 2, 3}
    assert star_level in {1, 2, 3}, (
        f"Star level must be 1, 2, or 3. Got: {star_level} for score {score}"
    )

    # Verify threshold logic
    if score < config.threshold_2_star:
        assert star_level == 1, (
            f"Score {score} < threshold_2_star ({config.threshold_2_star}) "
            f"should yield 1-star, got {star_level}"
        )
    elif score < config.threshold_3_star:
        assert star_level == 2, (
            f"Score {score} >= threshold_2_star ({config.threshold_2_star}) "
            f"and < threshold_3_star ({config.threshold_3_star}) "
            f"should yield 2-star, got {star_level}"
        )
    else:
        assert star_level == 3, (
            f"Score {score} >= threshold_3_star ({config.threshold_3_star}) "
            f"should yield 3-star, got {star_level}"
        )


@given(
    config=_config_strategy(),
    score=st.floats(min_value=-10.0, max_value=100.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200)
def test_property6_star_level_with_varied_thresholds(config: Config, score: float):
    """Property 6: Star level threshold logic holds for any valid thresholds.

    The threshold comparison must work correctly regardless of the specific
    threshold values, as long as threshold_3_star > threshold_2_star.

    **Validates: Requirements 3.5, 3.6**
    """
    # Ensure threshold_3_star > threshold_2_star (valid config)
    assume(config.threshold_3_star > config.threshold_2_star)

    classifier = _make_classifier(config)
    star_level = classifier._score_to_stars(score)

    # Result must always be exactly one of {1, 2, 3}
    assert star_level in {1, 2, 3}, (
        f"Star level must be 1, 2, or 3. Got: {star_level}"
    )

    # Verify threshold logic
    if score < config.threshold_2_star:
        assert star_level == 1, (
            f"Score {score} < threshold_2_star ({config.threshold_2_star}) "
            f"should yield 1-star, got {star_level}"
        )
    elif score < config.threshold_3_star:
        assert star_level == 2, (
            f"Score {score} >= threshold_2_star ({config.threshold_2_star}) "
            f"and < threshold_3_star ({config.threshold_3_star}) "
            f"should yield 2-star, got {star_level}"
        )
    else:
        assert star_level == 3, (
            f"Score {score} >= threshold_3_star ({config.threshold_3_star}) "
            f"should yield 3-star, got {star_level}"
        )


@given(
    word_count=st.integers(min_value=1, max_value=500),
    has_blogpost=st.booleans(),
    tier=st.sampled_from(["high", "medium", "base"]),
)
@settings(max_examples=100)
def test_property6_classify_returns_consistent_star_and_score(
    word_count: int,
    has_blogpost: bool,
    tier: str,
):
    """Property 6: classify() returns star level consistent with compute_score().

    The classify method must return a star level that matches what _score_to_stars
    would produce for the same computed score. This verifies end-to-end consistency.

    **Validates: Requirements 3.5, 3.6**
    """
    config = Config()
    classifier = _make_classifier(config)

    # Select service keyword based on tier
    if tier == "high":
        title = "Amazon Bedrock announces new feature"
    elif tier == "medium":
        title = "Kiro announces new feature"
    else:
        title = "AWS announces new storage feature update"

    # Build description with controlled word count
    words = ["word"] * word_count
    if has_blogpost:
        words[0] = "https://aws.amazon.com/blogs/machine-learning/example-post"

    description = " ".join(words)

    item = RSSItem(
        title=title,
        description=description,
        pub_date="2025-01-15",
        link="https://aws.amazon.com/about-aws/whats-new/2025/01/test",
    )

    # Get both the score and the star level from classify
    star_level, raw_score = classifier.classify(item)

    # Verify the star level matches what threshold comparison would give
    expected_star = classifier._score_to_stars(raw_score)
    assert star_level == expected_star, (
        f"classify() returned star_level={star_level} but _score_to_stars({raw_score}) "
        f"gives {expected_star}. Thresholds: 2-star={config.threshold_2_star}, "
        f"3-star={config.threshold_3_star}"
    )

    # Also verify the score matches compute_score
    expected_score = classifier.compute_score(item)
    assert abs(raw_score - expected_score) < 1e-9, (
        f"classify() returned score={raw_score} but compute_score() gives {expected_score}"
    )

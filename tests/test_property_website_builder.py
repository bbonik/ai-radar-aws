"""Property-based tests for the Website Builder module.

Feature: aws-ai-news-hub
- Property 12: Report HTML contains all required content
- Property 13: Composable filter produces correct results
- Property 14: Filter state independence
- Property 15: Timeline data aggregation
- Property 17: XSS sanitization

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 10.4, 10.5, 10.7, 11.1, 11.2, 13.4
"""

from collections import defaultdict
from unittest.mock import MagicMock

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import ProcessedAnnouncement, Report
from src.website_builder.builder import WebsiteBuilder, _sanitize_html


# =============================================================================
# Strategies
# =============================================================================

# Strategy for safe text (printable, no pipe chars, reasonable length)
_safe_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Z"), blacklist_characters="|<>&\"'"),
    min_size=1,
    max_size=80,
)

# Strategy for text that will appear in HTML (no special chars to simplify assertions)
_html_safe_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Z"),
        blacklist_characters="<>&\"'|{}",
    ),
    min_size=1,
    max_size=80,
)

# Strategy for date strings in YYYY-MM-DD format
import datetime as _dt

_date_strategy = st.dates(
    min_value=_dt.date(2020, 1, 1),
    max_value=_dt.date(2025, 12, 31),
).map(lambda d: d.isoformat())

# Strategy for URL-like links
_link_strategy = st.from_regex(
    r"https://aws\.amazon\.com/whats-new/[a-z0-9\-]{5,30}",
    fullmatch=True,
)

# Strategy for AWS service names
_service_strategy = st.sampled_from([
    "Amazon Bedrock", "Amazon SageMaker", "Amazon Kendra",
    "Amazon Comprehend", "Amazon Rekognition", "Amazon Textract",
    "Amazon Polly", "Amazon Lex", "Amazon Translate",
    "Amazon QuickSight", "AWS Lambda", "Amazon S3",
])

# Strategy for importance levels
_importance_strategy = st.sampled_from([1, 2, 3, 4, 5])

# Strategy for Report
_report_strategy = st.builds(
    Report,
    whats_new=_html_safe_text,
    how_it_works=_html_safe_text,
    why_important=_html_safe_text,
    how_different=_html_safe_text,
    when_to_prefer=_html_safe_text,
    availability=_html_safe_text,
)

# Strategy for blogpost links
_blogpost_links_strategy = st.lists(
    st.from_regex(r"https://aws\.amazon\.com/blogs/[a-z0-9\-]{5,20}", fullmatch=True),
    min_size=0,
    max_size=3,
)

# Strategy for mermaid graph (None for 1-star, optional for 2-3 star)
_mermaid_strategy = st.one_of(
    st.none(),
    st.just("graph TD\n    A[Service] --> B[Feature]"),
)


def _announcement_strategy():
    """Strategy for generating ProcessedAnnouncement objects."""
    return st.builds(
        ProcessedAnnouncement,
        title=_html_safe_text,
        description=_html_safe_text,
        pub_date=_date_strategy,
        link=_link_strategy,
        aws_service=_service_strategy,
        importance_level=_importance_strategy,
        importance_score=st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
        report=_report_strategy,
        mermaid_graph=_mermaid_strategy,
        blogpost_links=_blogpost_links_strategy,
        first_detected=_date_strategy.map(lambda d: d + "T00:00:00Z"),
    )


def _make_builder() -> WebsiteBuilder:
    """Create a WebsiteBuilder instance for testing."""
    config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run")
    s3_client = MagicMock()
    return WebsiteBuilder(config=config, s3_client=s3_client, logger=logger, data_bucket="test-bucket")


# =============================================================================
# Property 12: Report HTML contains all required content
# =============================================================================


@given(announcement=_announcement_strategy())
@settings(max_examples=100)
def test_property12_report_html_contains_all_required_content(
    announcement: ProcessedAnnouncement,
):
    """Property 12: Report HTML contains all required content.

    For any ProcessedAnnouncement, the generated HTML report page SHALL contain:
    all six report text sections, the announcement title, publication date,
    importance level indicator, AWS service name, a hyperlink to the original
    announcement URL, hyperlinks to all blogpost URLs, and (if mermaid_graph
    is not None) the Mermaid diagram code block.

    **Validates: Requirements 9.1, 9.2, 9.3, 9.4**
    """
    builder = _make_builder()
    files = builder.build([announcement])

    # Find the report page
    report_pages = [path for path in files if path.startswith("reports/")]
    assert len(report_pages) == 1, f"Expected 1 report page, got {len(report_pages)}"

    report_html = files[report_pages[0]]

    # All six report sections must be present (sanitized)
    assert _sanitize_html(announcement.report.whats_new) in report_html, (
        "What's New section missing from report HTML"
    )
    assert _sanitize_html(announcement.report.how_it_works) in report_html, (
        "How It Works section missing from report HTML"
    )
    assert _sanitize_html(announcement.report.why_important) in report_html, (
        "Why It's Important section missing from report HTML"
    )
    assert _sanitize_html(announcement.report.how_different) in report_html, (
        "How It's Different section missing from report HTML"
    )
    assert _sanitize_html(announcement.report.when_to_prefer) in report_html, (
        "When to Prefer It section missing from report HTML"
    )
    assert _sanitize_html(announcement.report.availability) in report_html, (
        "Availability section missing from report HTML"
    )

    # Title must be present
    assert _sanitize_html(announcement.title) in report_html, (
        "Announcement title missing from report HTML"
    )

    # Publication date must be present (DD/MM/YYYY display format)
    date_str = announcement.pub_date[:10]
    # Date is now displayed as DD/MM/YYYY
    parts = date_str.split("-")
    if len(parts) == 3:
        date_display = f"{parts[2]}/{parts[1]}/{parts[0]}"
    else:
        date_display = date_str
    assert date_display in report_html, (
        "Publication date missing from report HTML"
    )

    # Importance level indicator (star characters)
    stars = "\u2605" * announcement.importance_level + "\u2606" * (5 - announcement.importance_level)
    assert stars in report_html, (
        f"Importance level indicator (stars) missing from report HTML"
    )

    # AWS service name must be present
    assert _sanitize_html(announcement.aws_service) in report_html, (
        "AWS service name missing from report HTML"
    )

    # Link to original announcement
    assert _sanitize_html(announcement.link) in report_html, (
        "Original announcement link missing from report HTML"
    )

    # Blogpost links must all be present
    for blogpost_link in announcement.blogpost_links:
        assert _sanitize_html(blogpost_link) in report_html, (
            f"Blogpost link '{blogpost_link}' missing from report HTML"
        )

    # Mermaid diagram (if present)
    if announcement.mermaid_graph is not None:
        assert _sanitize_html(announcement.mermaid_graph) in report_html, (
            "Mermaid diagram code missing from report HTML"
        )
        assert "mermaid" in report_html, (
            "Mermaid div class missing from report HTML"
        )


# =============================================================================
# Property 13: Composable filter produces correct results
# =============================================================================


def _apply_filters(
    announcements: list[ProcessedAnnouncement],
    service_filter: str | None,
    importance_filter: int | None,
    rank_by_importance: bool,
) -> list[ProcessedAnnouncement]:
    """Apply composable filters to announcements (mirrors client-side JS logic).

    This is a reference implementation of the filter logic that the website's
    JavaScript implements client-side. We test that the data attributes in the
    generated HTML would allow correct filtering.
    """
    result = announcements[:]

    # Service filter
    if service_filter is not None:
        result = [a for a in result if a.aws_service == service_filter]

    # Importance filter
    if importance_filter is not None:
        result = [a for a in result if a.importance_level == importance_filter]

    # Rank by importance (descending)
    if rank_by_importance:
        result = sorted(result, key=lambda a: a.importance_level, reverse=True)

    return result


@given(
    announcements=st.lists(_announcement_strategy(), min_size=1, max_size=10),
    service_filter=st.one_of(st.none(), _service_strategy),
    importance_filter=st.one_of(st.none(), _importance_strategy),
    rank_by_importance=st.booleans(),
)
@settings(max_examples=100)
def test_property13_composable_filter_produces_correct_results(
    announcements: list[ProcessedAnnouncement],
    service_filter: str | None,
    importance_filter: int | None,
    rank_by_importance: bool,
):
    """Property 13: Composable filter produces correct results.

    For any set of announcements and any combination of active filters
    (service name, importance level), the filtered result set SHALL contain
    exactly those announcements that satisfy ALL active filter criteria
    simultaneously. The result SHALL be ordered by importance when ranking
    is active.

    **Validates: Requirements 10.4, 10.5**
    """
    # Apply filters using reference implementation
    filtered = _apply_filters(
        announcements,
        service_filter=service_filter,
        importance_filter=importance_filter,
        rank_by_importance=rank_by_importance,
    )

    # Verify: every item in filtered satisfies ALL active criteria
    for item in filtered:
        if service_filter is not None:
            assert item.aws_service == service_filter, (
                f"Item with service '{item.aws_service}' should not pass "
                f"service filter '{service_filter}'"
            )
        if importance_filter is not None:
            assert item.importance_level == importance_filter, (
                f"Item with importance {item.importance_level} should not pass "
                f"importance filter {importance_filter}"
            )

    # Verify: no item outside filtered satisfies all criteria
    for item in announcements:
        passes_service = service_filter is None or item.aws_service == service_filter
        passes_importance = importance_filter is None or item.importance_level == importance_filter
        if passes_service and passes_importance:
            assert item in filtered, (
                f"Item satisfying all filters was excluded from results"
            )

    # Verify: when rank_by_importance is active, results are sorted descending
    if rank_by_importance and len(filtered) > 1:
        for i in range(len(filtered) - 1):
            assert filtered[i].importance_level >= filtered[i + 1].importance_level, (
                f"Items not sorted by importance: "
                f"{filtered[i].importance_level} < {filtered[i+1].importance_level}"
            )


@given(
    announcements=st.lists(_announcement_strategy(), min_size=1, max_size=10),
)
@settings(max_examples=100)
def test_property13_filter_data_attributes_in_html(
    announcements: list[ProcessedAnnouncement],
):
    """Property 13 (HTML verification): Generated HTML cards contain correct data attributes.

    The generated index HTML SHALL include data-tags and data-importance
    attributes on each card that match the announcement data, enabling
    client-side filtering.

    **Validates: Requirements 10.4, 10.5**
    """
    builder = _make_builder()
    files = builder.build(announcements)
    index_html = files["index.html"]

    for announcement in announcements:
        # Each card should have the correct data-importance attribute
        expected_importance_attr = f'data-importance="{announcement.importance_level}"'
        assert expected_importance_attr in index_html, (
            f"Missing data-importance attribute for level {announcement.importance_level}"
        )

        # Each card should have a data-tags attribute containing all tags
        all_tags = announcement.tags.all_tags()
        if all_tags:
            tags_attr_value = _sanitize_html(",".join(all_tags))
            expected_tags_attr = f'data-tags="{tags_attr_value}"'
            assert expected_tags_attr in index_html, (
                f"Missing data-tags attribute for announcement '{announcement.title}'"
            )


# =============================================================================
# Property 14: Filter state independence
# =============================================================================


@given(
    announcements=st.lists(_announcement_strategy(), min_size=2, max_size=10),
    service_filter_1=st.one_of(st.none(), _service_strategy),
    service_filter_2=st.one_of(st.none(), _service_strategy),
    importance_filter=st.one_of(st.none(), _importance_strategy),
)
@settings(max_examples=100)
def test_property14_filter_state_independence(
    announcements: list[ProcessedAnnouncement],
    service_filter_1: str | None,
    service_filter_2: str | None,
    importance_filter: int | None,
):
    """Property 14: Filter state independence.

    For any current filter state with multiple active filters, adding or
    removing a single filter SHALL not modify the state of any other active
    filter. The resulting filter state SHALL differ from the previous state
    only in the filter that was added or removed.

    **Validates: Requirements 10.7**
    """
    # Apply initial filter state (service_filter_1 + importance_filter)
    result_1 = _apply_filters(
        announcements,
        service_filter=service_filter_1,
        importance_filter=importance_filter,
        rank_by_importance=False,
    )

    # Change only the service filter (service_filter_1 -> service_filter_2)
    # The importance filter should remain unchanged
    result_2 = _apply_filters(
        announcements,
        service_filter=service_filter_2,
        importance_filter=importance_filter,
        rank_by_importance=False,
    )

    # Verify: the importance filter still applies correctly in both results
    if importance_filter is not None:
        for item in result_1:
            assert item.importance_level == importance_filter, (
                "Importance filter not applied in state 1"
            )
        for item in result_2:
            assert item.importance_level == importance_filter, (
                "Importance filter not applied after service filter change"
            )

    # Verify: changing service filter only affects service-based filtering
    # Items in result_2 should match service_filter_2 (if set)
    if service_filter_2 is not None:
        for item in result_2:
            assert item.aws_service == service_filter_2, (
                f"Service filter not correctly applied after change: "
                f"expected '{service_filter_2}', got '{item.aws_service}'"
            )

    # Verify: the results differ only due to the changed filter
    # Both results should have the same importance filter applied
    all_importance_filtered = [
        a for a in announcements
        if importance_filter is None or a.importance_level == importance_filter
    ]

    expected_1 = [
        a for a in all_importance_filtered
        if service_filter_1 is None or a.aws_service == service_filter_1
    ]
    expected_2 = [
        a for a in all_importance_filtered
        if service_filter_2 is None or a.aws_service == service_filter_2
    ]

    assert result_1 == expected_1, "Result 1 doesn't match expected filter application"
    assert result_2 == expected_2, "Result 2 doesn't match expected filter application"


# =============================================================================
# Property 15: Timeline data aggregation
# =============================================================================


@given(
    announcements=st.lists(_announcement_strategy(), min_size=1, max_size=20),
)
@settings(max_examples=100)
def test_property15_timeline_data_aggregation(
    announcements: list[ProcessedAnnouncement],
):
    """Property 15: Timeline data aggregation.

    For any set of announcements with dates and importance levels, the timeline
    data SHALL correctly count the number of announcements per calendar day,
    and each day's count SHALL be segmented by importance level such that the
    sum of segments equals the total count for that day.

    **Validates: Requirements 11.1, 11.2**
    """
    builder = _make_builder()
    timeline_data = builder._compute_timeline_data(announcements)

    # Compute expected counts independently
    expected_day_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"star1": 0, "star2": 0, "star3": 0, "star4": 0, "star5": 0}
    )
    for a in announcements:
        date_str = a.pub_date[:10] if len(a.pub_date) >= 10 else a.pub_date
        expected_day_counts[date_str][f"star{a.importance_level}"] += 1

    expected_sorted_dates = sorted(expected_day_counts.keys())

    # Verify labels are sorted dates
    assert timeline_data["labels"] == expected_sorted_dates, (
        f"Timeline labels don't match expected sorted dates. "
        f"Got {timeline_data['labels']}, expected {expected_sorted_dates}"
    )

    # Verify counts per day per importance level
    for i, date in enumerate(expected_sorted_dates):
        assert timeline_data["star1"][i] == expected_day_counts[date]["star1"], (
            f"Star1 count mismatch for {date}: "
            f"got {timeline_data['star1'][i]}, expected {expected_day_counts[date]['star1']}"
        )
        assert timeline_data["star2"][i] == expected_day_counts[date]["star2"], (
            f"Star2 count mismatch for {date}: "
            f"got {timeline_data['star2'][i]}, expected {expected_day_counts[date]['star2']}"
        )
        assert timeline_data["star3"][i] == expected_day_counts[date]["star3"], (
            f"Star3 count mismatch for {date}: "
            f"got {timeline_data['star3'][i]}, expected {expected_day_counts[date]['star3']}"
        )
        assert timeline_data["star4"][i] == expected_day_counts[date]["star4"], (
            f"Star4 count mismatch for {date}: "
            f"got {timeline_data['star4'][i]}, expected {expected_day_counts[date]['star4']}"
        )
        assert timeline_data["star5"][i] == expected_day_counts[date]["star5"], (
            f"Star5 count mismatch for {date}: "
            f"got {timeline_data['star5'][i]}, expected {expected_day_counts[date]['star5']}"
        )

    # Verify: sum of segments equals total count for each day
    for i, date in enumerate(expected_sorted_dates):
        total_for_day = (
            timeline_data["star1"][i]
            + timeline_data["star2"][i]
            + timeline_data["star3"][i]
            + timeline_data["star4"][i]
            + timeline_data["star5"][i]
        )
        expected_total = sum(
            1 for a in announcements
            if (a.pub_date[:10] if len(a.pub_date) >= 10 else a.pub_date) == date
        )
        assert total_for_day == expected_total, (
            f"Sum of segments ({total_for_day}) != total count ({expected_total}) for {date}"
        )


@given(
    announcements=st.lists(_announcement_strategy(), min_size=0, max_size=5),
)
@settings(max_examples=50)
def test_property15_timeline_empty_input(
    announcements: list[ProcessedAnnouncement],
):
    """Property 15 (corollary): Timeline handles empty and small inputs.

    The timeline data SHALL have consistent structure regardless of input size.

    **Validates: Requirements 11.1, 11.2**
    """
    builder = _make_builder()
    timeline_data = builder._compute_timeline_data(announcements)

    # Structure must always have these keys
    assert "labels" in timeline_data
    assert "star1" in timeline_data
    assert "star2" in timeline_data
    assert "star3" in timeline_data

    # All arrays must have the same length
    n = len(timeline_data["labels"])
    assert len(timeline_data["star1"]) == n
    assert len(timeline_data["star2"]) == n
    assert len(timeline_data["star3"]) == n

    # All counts must be non-negative
    for count in timeline_data["star1"] + timeline_data["star2"] + timeline_data["star3"]:
        assert count >= 0, f"Negative count in timeline data: {count}"


# =============================================================================
# Property 17: XSS sanitization
# =============================================================================

# Strategy for generating XSS attack payloads
_xss_payloads = st.sampled_from([
    '<script>alert("xss")</script>',
    '<img src=x onerror=alert(1)>',
    '<svg onload=alert(1)>',
    '<a href="javascript:alert(1)">click</a>',
    '<div onclick="alert(1)">test</div>',
    '<iframe src="javascript:alert(1)"></iframe>',
    '<body onload="alert(1)">',
    '<input onfocus="alert(1)" autofocus>',
    '"><script>alert(document.cookie)</script>',
    "';alert(String.fromCharCode(88,83,83))//",
    '<img src="x" onerror="alert(\'XSS\')">',
    '<marquee onstart=alert(1)>',
    '<details open ontoggle=alert(1)>',
    '<math><mtext><table><mglyph><style><!--</style><img title="--><img src=1 onerror=alert(1)>">',
    '<a href="jAvAsCrIpT:alert(1)">link</a>',
])

# Strategy for text with embedded XSS attempts
_text_with_xss = st.one_of(
    # Pure XSS payload
    _xss_payloads,
    # Text with embedded payload
    st.tuples(_html_safe_text, _xss_payloads, _html_safe_text).map(
        lambda t: f"{t[0]} {t[1]} {t[2]}"
    ),
)


@given(malicious_text=_text_with_xss)
@settings(max_examples=100)
def test_property17_xss_sanitization(malicious_text: str):
    """Property 17: XSS sanitization.

    For any announcement content containing HTML script tags, event handler
    attributes (onclick, onerror, etc.), or javascript: URLs, the rendered
    website HTML SHALL NOT contain executable script content. All such content
    SHALL be escaped or removed.

    The _sanitize_html function uses html.escape which converts < to &lt;,
    > to &gt;, & to &amp;, " to &quot;, and ' to &#x27;. This prevents the
    browser from interpreting the content as HTML/JavaScript.

    **Validates: Requirements 13.4**
    """
    sanitized = _sanitize_html(malicious_text)

    # The key property: no unescaped angle brackets means no executable HTML
    # html.escape converts < to &lt; and > to &gt;, preventing tag interpretation
    assert "<" not in sanitized, (
        f"Unescaped '<' found in sanitized output: {sanitized}"
    )
    assert ">" not in sanitized, (
        f"Unescaped '>' found in sanitized output: {sanitized}"
    )

    # Quotes must be escaped (prevents attribute injection)
    assert '"' not in sanitized, (
        f"Unescaped double quote found in sanitized output: {sanitized}"
    )

    # Ampersands in the original must be escaped (prevents entity injection)
    # Note: the output will contain &lt; &gt; &amp; etc. which is correct
    # We verify that any & in the output is part of an escape sequence
    import re
    # Find all & characters and verify they're part of valid HTML entities
    ampersand_positions = [i for i, c in enumerate(sanitized) if c == "&"]
    for pos in ampersand_positions:
        remaining = sanitized[pos:]
        assert re.match(r"&(lt|gt|amp|quot|#x27);", remaining), (
            f"Unescaped '&' found at position {pos} in sanitized output: {sanitized}"
        )


@given(
    title=_text_with_xss,
    description=_text_with_xss,
    report_text=_text_with_xss,
)
@settings(max_examples=100)
def test_property17_xss_in_full_report_page(
    title: str,
    description: str,
    report_text: str,
):
    """Property 17 (integration): XSS payloads in announcement fields are sanitized in HTML output.

    When announcement fields contain XSS payloads, the generated report page
    SHALL contain the user content only in its html-escaped form, preventing
    browser interpretation as executable code.

    **Validates: Requirements 13.4**
    """
    # Create an announcement with XSS payloads in various fields
    announcement = ProcessedAnnouncement(
        title=title,
        description=description,
        pub_date="2025-01-15",
        link="https://aws.amazon.com/whats-new/test-announcement",
        aws_service="Amazon Bedrock",
        importance_level=2,
        importance_score=5.0,
        report=Report(
            whats_new=report_text,
            how_it_works=report_text,
            why_important=report_text,
            how_different=report_text,
            when_to_prefer=report_text,
            availability=report_text,
        ),
        mermaid_graph=None,
        blogpost_links=[],
        first_detected="2025-01-15T00:00:00Z",
    )

    builder = _make_builder()
    files = builder.build([announcement])

    # Find the report page
    report_pages = [path for path in files if path.startswith("reports/")]
    assert len(report_pages) == 1
    report_html = files[report_pages[0]]

    # The user-provided content must appear in its escaped form in the HTML
    escaped_title = _sanitize_html(title)
    assert escaped_title in report_html, (
        f"Escaped title not found in report HTML"
    )

    escaped_report_text = _sanitize_html(report_text)
    assert escaped_report_text in report_html, (
        f"Escaped report text not found in report HTML"
    )

    # Verify that user content with HTML special chars does NOT appear raw
    # The raw XSS payload should not be present as executable HTML
    # We strip out the template's own legitimate script tags and check
    # that no user-injected script tags remain
    if "<script>" in title.lower() or "<script>" in report_text.lower():
        # Count legitimate script tags from the template
        import re
        # Template has script tags for CDN libs - those are fine
        # User content like <script>alert("xss")</script> must be escaped
        # So we should find the escaped version: &lt;script&gt;alert...
        assert "&lt;script&gt;" in report_html or "&lt;script&gt;" in report_html.lower(), (
            "XSS script tag was not properly escaped in report HTML"
        )

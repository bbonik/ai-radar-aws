"""Property-based tests for PDF export content completeness.

Feature: aws-ai-news-hub
- Property 16: PDF contains complete report content

Since PDF generation is client-side (html2pdf.js captures the #report-content div),
we verify that the HTML structure contains all required content within that div,
ensuring the exported PDF will include all six report sections and header metadata.

Validates: Requirements 12.1, 12.3
"""

import re
from unittest.mock import MagicMock

import datetime as _dt
from hypothesis import given, settings
from hypothesis import strategies as st

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import ProcessedAnnouncement, Report
from src.website_builder.builder import WebsiteBuilder, _sanitize_html


# =============================================================================
# Strategies
# =============================================================================

# Strategy for safe text (printable, no pipe chars, reasonable length)
_html_safe_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Z"),
        blacklist_characters="<>&\"'|{}",
    ),
    min_size=1,
    max_size=80,
)

# Strategy for date strings in YYYY-MM-DD format
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


def _extract_report_content_div(html: str) -> str:
    """Extract the content within the #report-content div from the HTML."""
    start_marker = '<div id="report-content">'
    start_idx = html.find(start_marker)
    assert start_idx != -1, "Could not find #report-content div in HTML"

    # Find the matching closing div by counting nesting
    depth = 0
    i = start_idx
    while i < len(html):
        if html[i:i+4] == "<div":
            depth += 1
            i += 4
        elif html[i:i+6] == "</div>":
            depth -= 1
            if depth == 0:
                return html[start_idx:i + 6]
            i += 6
        else:
            i += 1

    raise AssertionError("Could not find closing tag for #report-content div")


# =============================================================================
# Property 16: PDF contains complete report content
# =============================================================================


@given(announcement=_announcement_strategy())
@settings(max_examples=100)
def test_property16_report_content_div_exists(
    announcement: ProcessedAnnouncement,
):
    """Property 16: The #report-content div exists in the generated report HTML.

    The html2pdf.js exportPDF() function captures document.getElementById('report-content'),
    so this div MUST exist in every generated report page.

    **Validates: Requirements 12.1, 12.3**
    """
    builder = _make_builder()
    files = builder.build([announcement])

    report_pages = [path for path in files if path.startswith("reports/")]
    assert len(report_pages) == 1
    report_html = files[report_pages[0]]

    # The #report-content div must exist
    assert 'id="report-content"' in report_html, (
        "The #report-content div is missing from the report HTML. "
        "html2pdf.js requires this element to generate the PDF."
    )


@given(announcement=_announcement_strategy())
@settings(max_examples=100)
def test_property16_all_report_sections_in_report_content_div(
    announcement: ProcessedAnnouncement,
):
    """Property 16: All six report sections are within the #report-content div.

    For any ProcessedAnnouncement, the generated report page SHALL have all six
    report section headings contained within the #report-content div that
    html2pdf.js captures for PDF export.

    **Validates: Requirements 12.1**
    """
    builder = _make_builder()
    files = builder.build([announcement])

    report_pages = [path for path in files if path.startswith("reports/")]
    assert len(report_pages) == 1
    report_html = files[report_pages[0]]

    # Extract the #report-content div content
    report_content_div = _extract_report_content_div(report_html)

    # All six report section headings must be within the #report-content div
    assert "What&#x27;s New" in report_content_div, (
        "What's New section heading missing from #report-content div"
    )
    assert "How It Works" in report_content_div, (
        "How It Works section heading missing from #report-content div"
    )
    assert "Why It&#x27;s Important" in report_content_div, (
        "Why It's Important section heading missing from #report-content div"
    )
    assert "How It&#x27;s Different" in report_content_div, (
        "How It's Different section heading missing from #report-content div"
    )
    assert "When to Prefer It" in report_content_div, (
        "When to Prefer It section heading missing from #report-content div"
    )
    assert "Availability" in report_content_div, (
        "Availability section heading missing from #report-content div"
    )

    # For non-whitespace content, verify it appears in the rendered output
    if announcement.report.whats_new.strip():
        assert _sanitize_html(announcement.report.whats_new) in report_content_div, (
            "What's New content missing from #report-content div"
        )


@given(announcement=_announcement_strategy())
@settings(max_examples=100)
def test_property16_header_metadata_in_report_content_div(
    announcement: ProcessedAnnouncement,
):
    """Property 16: Header metadata is within the #report-content div.

    The PDF header SHALL include the announcement title, date, importance level
    (stars), and service name — all within the #report-content div that
    html2pdf.js captures.

    **Validates: Requirements 12.3**
    """
    builder = _make_builder()
    files = builder.build([announcement])

    report_pages = [path for path in files if path.startswith("reports/")]
    assert len(report_pages) == 1
    report_html = files[report_pages[0]]

    # Extract the #report-content div content
    report_content_div = _extract_report_content_div(report_html)

    # Title must be within the report-content div
    assert _sanitize_html(announcement.title) in report_content_div, (
        "Announcement title missing from #report-content div (won't appear in PDF header)"
    )

    # Date must be within the report-content div (displayed as DD/MM/YYYY)
    date_str = announcement.pub_date[:10] if len(announcement.pub_date) >= 10 else announcement.pub_date
    # Convert to DD/MM/YYYY format as displayed
    parts = date_str.split("-")
    if len(parts) == 3:
        display_date = f"{parts[2]}/{parts[1]}/{parts[0]}"
    else:
        display_date = date_str
    assert display_date in report_content_div, (
        "Publication date missing from #report-content div (won't appear in PDF header)"
    )

    # Service name must be within the report-content div
    assert _sanitize_html(announcement.aws_service) in report_content_div, (
        "AWS service name missing from #report-content div (won't appear in PDF header)"
    )

    # Importance stars must be within the report-content div
    stars = "\u2605" * announcement.importance_level + "\u2606" * (5 - announcement.importance_level)
    assert stars in report_content_div, (
        "Importance level stars missing from #report-content div (won't appear in PDF header)"
    )


@given(announcement=_announcement_strategy())
@settings(max_examples=100)
def test_property16_html2pdf_cdn_loaded(
    announcement: ProcessedAnnouncement,
):
    """Property 16: The html2pdf.js CDN script is loaded in the report page.

    The page MUST load the html2pdf.js library from CDN so that the exportPDF()
    function can generate the PDF client-side without server-side processing.

    **Validates: Requirements 12.1, 12.3**
    """
    builder = _make_builder()
    files = builder.build([announcement])

    report_pages = [path for path in files if path.startswith("reports/")]
    assert len(report_pages) == 1
    report_html = files[report_pages[0]]

    # html2pdf.js CDN script must be present
    assert "html2pdf" in report_html, (
        "html2pdf.js CDN script not found in report page"
    )
    assert "cdnjs.cloudflare.com/ajax/libs/html2pdf.js" in report_html, (
        "html2pdf.js CDN URL not found in report page"
    )


@given(announcement=_announcement_strategy())
@settings(max_examples=100)
def test_property16_export_pdf_button_present(
    announcement: ProcessedAnnouncement,
):
    """Property 16: The Export PDF button is present in the report page.

    The report page MUST include a button that triggers the exportPDF() function,
    allowing visitors to download the report as a PDF.

    **Validates: Requirements 12.1, 12.3**
    """
    builder = _make_builder()
    files = builder.build([announcement])

    report_pages = [path for path in files if path.startswith("reports/")]
    assert len(report_pages) == 1
    report_html = files[report_pages[0]]

    # Export PDF button must be present with onclick handler
    assert "exportPDF()" in report_html, (
        "exportPDF() onclick handler not found in report page"
    )
    assert "Export as PDF" in report_html or "export" in report_html.lower(), (
        "PDF export button text not found in report page"
    )

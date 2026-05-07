"""Property-based test for RSS parsing field extraction.

Feature: aws-ai-news-hub, Property 1: RSS parsing extracts all fields

Validates: Requirements 1.2
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from src.config import Config
from src.pipeline.rss_fetcher import RSSFetcher
from src.shared.logger import StructuredLogger


# Characters safe for XML text content: avoid <, >, &, and control chars
# which would break XML structure or require escaping that complicates assertions.
_xml_safe_alphabet = st.characters(
    whitelist_categories=("L", "N", "P", "S", "Z"),
    blacklist_characters="<>&\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f"
    "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f",
)

# Strategy for generating non-empty plain text safe for XML embedding
xml_safe_text = st.text(
    alphabet=_xml_safe_alphabet,
    min_size=1,
    max_size=100,
)

# Strategy for description: plain text without HTML tags or entities
# so _clean_description() is a no-op (just strips whitespace)
description_text = st.text(
    alphabet=_xml_safe_alphabet,
    min_size=1,
    max_size=200,
)

# Strategy for link: simple URL-like strings
link_text = st.from_regex(
    r"https://aws\.amazon\.com/whats-new/[a-z0-9\-]{1,50}",
    fullmatch=True,
)

# Strategy for pub_date: RFC 2822-like date strings
pub_date_text = st.from_regex(
    r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-3][0-9] (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) 20[2-3][0-9] [0-2][0-9]:[0-5][0-9]:[0-5][0-9] GMT",
    fullmatch=True,
)


def _build_rss_xml(title: str, description: str, pub_date: str, link: str) -> bytes:
    """Build a minimal valid RSS XML document with one item."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        "    <title>Test Feed</title>\n"
        "    <item>\n"
        f"      <title>{title}</title>\n"
        f"      <description>{description}</description>\n"
        f"      <pubDate>{pub_date}</pubDate>\n"
        f"      <link>{link}</link>\n"
        "    </item>\n"
        "  </channel>\n"
        "</rss>"
    ).encode("utf-8")


@given(
    title=xml_safe_text,
    description=description_text,
    pub_date=pub_date_text,
    link=link_text,
)
@settings(max_examples=100)
def test_rss_parsing_extracts_all_fields(
    title: str, description: str, pub_date: str, link: str
):
    """Property 1: RSS parsing extracts all fields.

    For any valid RSS XML <item> element containing title, description,
    pubDate, and link sub-elements, the RSS parser SHALL produce an RSSItem
    with all four fields populated with the corresponding element text content.

    Feature: aws-ai-news-hub, Property 1: RSS parsing extracts all fields
    Validates: Requirements 1.2
    """
    # Build a valid RSS XML document with the generated values
    xml_content = _build_rss_xml(title, description, pub_date, link)

    # Create a fetcher instance (config/logger not used for _parse_feed)
    config = Config()
    logger = StructuredLogger(lambda_name="test", run_id="test-run")
    fetcher = RSSFetcher(config, logger)

    # Parse the feed
    items = fetcher._parse_feed(xml_content)

    # Exactly one item should be parsed
    assert len(items) == 1

    item = items[0]

    # All four fields must be populated (non-empty)
    assert item.title != "", "title should be populated"
    assert item.description != "", "description should be populated"
    assert item.pub_date != "", "pub_date should be populated"
    assert item.link != "", "link should be populated"

    # Fields should match the input values (after cleaning for description)
    # Since our generated description has no HTML tags or entities,
    # _clean_description just strips whitespace.
    assert item.title == title.strip()
    assert item.description == description.strip()
    assert item.pub_date == pub_date.strip()
    assert item.link == link.strip()

"""Unit tests for the RSS Fetcher module."""

import time
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from src.config import Config
from src.pipeline.rss_fetcher import RSSFetcher
from src.shared.logger import StructuredLogger


SAMPLE_RSS_XML = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>AWS What's New</title>
    <item>
      <title>Amazon Bedrock now supports new models</title>
      <description>&lt;p&gt;Amazon Bedrock adds support for &amp; new foundation models.&lt;/p&gt;</description>
      <pubDate>Mon, 15 Jan 2025 22:00:00 GMT</pubDate>
      <link>https://aws.amazon.com/about-aws/whats-new/2025/01/bedrock-new-models</link>
    </item>
    <item>
      <title>Amazon SageMaker AI updates</title>
      <description>SageMaker AI now includes improved training capabilities.</description>
      <pubDate>Tue, 16 Jan 2025 10:00:00 GMT</pubDate>
      <link>https://aws.amazon.com/about-aws/whats-new/2025/01/sagemaker-updates</link>
    </item>
  </channel>
</rss>
"""

EMPTY_RSS_XML = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>AWS What's New</title>
  </channel>
</rss>
"""


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def logger():
    return StructuredLogger(lambda_name="test", run_id="test-run-id")


@pytest.fixture
def fetcher(config, logger):
    return RSSFetcher(config, logger)


class TestRSSFetcherParsing:
    """Tests for RSS XML parsing logic."""

    def test_parse_extracts_all_fields(self, fetcher):
        """Verify all four fields are extracted from each item."""
        items = fetcher._parse_feed(SAMPLE_RSS_XML)

        assert len(items) == 2

        item1 = items[0]
        assert item1.title == "Amazon Bedrock now supports new models"
        assert "Amazon Bedrock adds support for & new foundation models." in item1.description
        assert item1.pub_date == "Mon, 15 Jan 2025 22:00:00 GMT"
        assert item1.link == "https://aws.amazon.com/about-aws/whats-new/2025/01/bedrock-new-models"

    def test_parse_cleans_html_entities(self, fetcher):
        """Verify HTML entities are unescaped in descriptions."""
        items = fetcher._parse_feed(SAMPLE_RSS_XML)
        # &amp; should become &, &lt;p&gt; tags should be stripped
        assert "&amp;" not in items[0].description
        assert "&lt;" not in items[0].description
        assert "<p>" not in items[0].description

    def test_parse_strips_html_tags(self, fetcher):
        """Verify HTML tags are removed from descriptions."""
        items = fetcher._parse_feed(SAMPLE_RSS_XML)
        assert "<p>" not in items[0].description
        assert "</p>" not in items[0].description

    def test_parse_empty_feed(self, fetcher):
        """Verify empty feed returns empty list."""
        items = fetcher._parse_feed(EMPTY_RSS_XML)
        assert items == []

    def test_parse_missing_elements(self, fetcher):
        """Verify missing sub-elements default to empty string."""
        xml = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Only title present</title>
    </item>
  </channel>
</rss>
"""
        items = fetcher._parse_feed(xml)
        assert len(items) == 1
        assert items[0].title == "Only title present"
        assert items[0].description == ""
        assert items[0].pub_date == ""
        assert items[0].link == ""


class TestRSSFetcherFetch:
    """Tests for the fetch method with HTTP mocking."""

    @patch("src.pipeline.rss_fetcher.urlopen")
    def test_successful_fetch(self, mock_urlopen, fetcher):
        """Verify successful fetch returns correct RSSItem list."""
        mock_response = MagicMock()
        mock_response.read.return_value = SAMPLE_RSS_XML
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        items = fetcher.fetch()

        assert len(items) == 2
        assert items[0].title == "Amazon Bedrock now supports new models"
        assert items[1].title == "Amazon SageMaker AI updates"

    @patch("src.pipeline.rss_fetcher.time.sleep")
    @patch("src.pipeline.rss_fetcher.urlopen")
    def test_retry_on_failure_then_success(self, mock_urlopen, mock_sleep, fetcher):
        """Verify retry logic succeeds after transient failures."""
        mock_response = MagicMock()
        mock_response.read.return_value = SAMPLE_RSS_XML
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        # Fail twice, then succeed
        mock_urlopen.side_effect = [
            URLError("Connection refused"),
            URLError("Timeout"),
            mock_response,
        ]

        items = fetcher.fetch()

        assert len(items) == 2
        assert mock_urlopen.call_count == 3
        # Backoff: 2^0=1s, 2^1=2s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    @patch("src.pipeline.rss_fetcher.time.sleep")
    @patch("src.pipeline.rss_fetcher.urlopen")
    def test_all_retries_exhausted_returns_empty(self, mock_urlopen, mock_sleep, fetcher):
        """Verify empty list returned after all retries exhausted."""
        mock_urlopen.side_effect = URLError("Connection refused")

        items = fetcher.fetch()

        assert items == []
        # Initial attempt + 3 retries = 4 calls
        assert mock_urlopen.call_count == 4
        # Backoff: 2^0=1s, 2^1=2s, 2^2=4s
        assert mock_sleep.call_count == 3
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(4)

    @patch("src.pipeline.rss_fetcher.time.sleep")
    @patch("src.pipeline.rss_fetcher.urlopen")
    def test_exponential_backoff_timing(self, mock_urlopen, mock_sleep, fetcher):
        """Verify exponential backoff uses correct delays (1s, 2s, 4s)."""
        mock_urlopen.side_effect = URLError("Connection refused")

        fetcher.fetch()

        expected_delays = [1, 2, 4]
        actual_delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert actual_delays == expected_delays


class TestRSSFetcherCleanDescription:
    """Tests for description cleaning utility."""

    def test_unescape_html_entities(self):
        # &amp; becomes &, &lt;/&gt; become < and > which are then stripped as tags
        assert RSSFetcher._clean_description("&amp; hello") == "& hello"
        assert RSSFetcher._clean_description("5 &gt; 3") == "5 > 3"

    def test_strip_html_tags(self):
        assert RSSFetcher._clean_description("<p>Hello <b>world</b></p>") == "Hello world"

    def test_combined_entities_and_tags(self):
        result = RSSFetcher._clean_description("&lt;p&gt;Test &amp; more&lt;/p&gt;")
        # After unescape: <p>Test & more</p>
        # After tag strip: Test & more
        assert result == "Test & more"

    def test_whitespace_trimming(self):
        assert RSSFetcher._clean_description("  hello  ") == "hello"

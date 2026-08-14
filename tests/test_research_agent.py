"""Unit tests for the ResearchAgent module."""

from unittest.mock import MagicMock, patch
import pytest

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import RSSItem, ResearchContext, PageContent
from src.pipeline.research_agent import (
    ResearchAgent,
    _TextExtractor,
    _GATE_RESERVATION_MS,
    _SAFETY_MARGIN_MS,
)


@pytest.fixture
def config():
    """Create a Config instance for testing."""
    return Config()


@pytest.fixture
def logger():
    """Create a StructuredLogger instance for testing."""
    return StructuredLogger(lambda_name="test", run_id="test-run-id")


@pytest.fixture
def mock_context_plenty_of_time():
    """Create a mock Lambda context with plenty of remaining time."""
    context = MagicMock()
    # 10 minutes remaining (600,000 ms)
    context.get_remaining_time_in_millis.return_value = 600_000
    return context


@pytest.fixture
def mock_context_low_time():
    """Create a mock Lambda context with insufficient remaining time."""
    context = MagicMock()
    # Only 20 seconds remaining (20,000 ms)
    context.get_remaining_time_in_millis.return_value = 20_000
    return context


@pytest.fixture
def sample_item():
    """Create a sample RSSItem for testing."""
    return RSSItem(
        title="Amazon Bedrock now supports new models",
        description='Check out the blog post at <a href="https://aws.amazon.com/blogs/ai/new-models">blog</a> for details.',
        pub_date="2025-01-15",
        link="https://aws.amazon.com/about-aws/whats-new/2025/01/bedrock-new-models",
    )


class TestHasSufficientTime:
    """Tests for the start-gate logic.

    The gate reserves a fixed 90 s (+ 30 s margin) rather than the full
    theoretical per-item maximum — the budget itself is enforced by the
    in-loop deadline. Plan: docs/audit-remediation-plan.md item 8 (D7).
    """

    def test_sufficient_time_returns_true(self, config, logger, mock_context_plenty_of_time):
        """Agent proceeds when there is plenty of remaining time."""
        agent = ResearchAgent(config=config, context=mock_context_plenty_of_time, logger=logger)
        assert agent._has_sufficient_time() is True

    def test_insufficient_time_returns_false(self, config, logger, mock_context_low_time):
        """Agent skips when remaining time is too low."""
        agent = ResearchAgent(config=config, context=mock_context_low_time, logger=logger)
        assert agent._has_sufficient_time() is False

    def test_exact_threshold_returns_true(self, config, logger):
        """Gate opens at exactly reservation (90s) + margin (30s) = 120s."""
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = (
            _GATE_RESERVATION_MS + _SAFETY_MARGIN_MS
        )
        agent = ResearchAgent(config=config, context=context, logger=logger)
        assert agent._has_sufficient_time() is True

    def test_one_below_threshold_returns_false(self, config, logger):
        """Agent skips when remaining time is one ms below threshold."""
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = (
            _GATE_RESERVATION_MS + _SAFETY_MARGIN_MS - 1
        )
        agent = ResearchAgent(config=config, context=context, logger=logger)
        assert agent._has_sufficient_time() is False

    def test_gate_opens_where_old_scheme_silently_skipped(self, config, logger):
        """The regression this change exists for: with 5 minutes left in the
        Lambda a typical item can easily be researched, but the old gate
        (330 s reservation) skipped it silently."""
        context = MagicMock()
        context.get_remaining_time_in_millis.return_value = 300_000  # 5 min
        agent = ResearchAgent(config=config, context=context, logger=logger)
        assert agent._has_sufficient_time() is True


class TestResearchDeadline:
    """The per-item budget is a ceiling enforced mid-loop, not a reservation."""

    def _item_with_urls(self, n: int) -> RSSItem:
        hrefs = " ".join(
            f'<a href="https://example.com/page-{i}">p{i}</a>' for i in range(n)
        )
        return RSSItem(
            title="Test",
            description=hrefs,
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/deadline-test",
        )

    def test_partial_content_returned_when_budget_exceeded(
        self, config, logger, mock_context_plenty_of_time
    ):
        """URLs fetched before the deadline are kept; the rest are skipped."""
        agent = ResearchAgent(
            config=config, context=mock_context_plenty_of_time, logger=logger
        )
        item = self._item_with_urls(5)

        fetched = []

        def fake_fetch(url):
            fetched.append(url)
            return PageContent(url=url, text="content", title="t")

        # Simulate the budget expiring after the second fetch
        clock = iter([0.0, 0.0, 0.0, 10_000.0, 10_000.0, 10_000.0])
        with patch.object(agent, "_fetch_and_extract", side_effect=fake_fetch), \
             patch("src.pipeline.research_agent.time.monotonic", side_effect=clock):
            result = agent.research(item)

        assert result.skipped is False
        assert 0 < len(result.gathered_content) < 6
        assert len(fetched) == len(result.gathered_content)

    def test_lambda_margin_stops_research_mid_item(self, config, logger):
        """Even inside the budget, research stops when the Lambda margin nears."""
        context = MagicMock()
        # Gate passes, then remaining time collapses below the margin
        context.get_remaining_time_in_millis.side_effect = [600_000, 600_000, 25_000, 25_000]
        agent = ResearchAgent(config=config, context=context, logger=logger)
        item = self._item_with_urls(4)

        with patch.object(
            agent, "_fetch_and_extract",
            side_effect=lambda url: PageContent(url=url, text="x", title="t"),
        ):
            result = agent.research(item)

        assert result.skipped is False
        assert len(result.gathered_content) < 5


class TestExtractUrls:
    """Tests for URL extraction from announcements."""

    def test_extracts_link_field(self, config, logger, mock_context_plenty_of_time):
        """The announcement's own link is always included."""
        agent = ResearchAgent(config=config, context=mock_context_plenty_of_time, logger=logger)
        item = RSSItem(
            title="Test",
            description="No URLs here",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/test",
        )
        urls = agent._extract_urls(item)
        assert "https://aws.amazon.com/whats-new/test" in urls

    def test_extracts_href_from_description(self, config, logger, mock_context_plenty_of_time):
        """URLs in href attributes are extracted from description."""
        agent = ResearchAgent(config=config, context=mock_context_plenty_of_time, logger=logger)
        item = RSSItem(
            title="Test",
            description='See <a href="https://aws.amazon.com/blogs/ai/post">blog</a>',
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/test",
        )
        urls = agent._extract_urls(item)
        assert "https://aws.amazon.com/blogs/ai/post" in urls

    def test_extracts_plain_text_urls(self, config, logger, mock_context_plenty_of_time):
        """Plain-text URLs are extracted from description."""
        agent = ResearchAgent(config=config, context=mock_context_plenty_of_time, logger=logger)
        item = RSSItem(
            title="Test",
            description="Visit https://docs.aws.amazon.com/bedrock/latest for more info",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/test",
        )
        urls = agent._extract_urls(item)
        assert "https://docs.aws.amazon.com/bedrock/latest" in urls

    def test_deduplicates_urls(self, config, logger, mock_context_plenty_of_time):
        """Duplicate URLs are not returned multiple times."""
        agent = ResearchAgent(config=config, context=mock_context_plenty_of_time, logger=logger)
        item = RSSItem(
            title="Test",
            description='Link: https://aws.amazon.com/whats-new/test and <a href="https://aws.amazon.com/whats-new/test">here</a>',
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/test",
        )
        urls = agent._extract_urls(item)
        # The link appears in both the link field and description, but should only appear once
        assert urls.count("https://aws.amazon.com/whats-new/test") == 1

    def test_empty_description_returns_only_link(self, config, logger, mock_context_plenty_of_time):
        """When description has no URLs, only the link field is returned."""
        agent = ResearchAgent(config=config, context=mock_context_plenty_of_time, logger=logger)
        item = RSSItem(
            title="Test",
            description="Just plain text with no links",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/test",
        )
        urls = agent._extract_urls(item)
        assert urls == ["https://aws.amazon.com/whats-new/test"]


class TestResearch:
    """Tests for the main research method."""

    def test_skips_when_time_insufficient(self, config, logger, mock_context_low_time, sample_item):
        """Research is skipped when Lambda time is running low."""
        agent = ResearchAgent(config=config, context=mock_context_low_time, logger=logger)
        result = agent.research(sample_item)
        assert result.skipped is True
        assert result.gathered_content == []

    @patch("src.pipeline.research_agent.urlopen")
    def test_successful_fetch(self, mock_urlopen, config, logger, mock_context_plenty_of_time):
        """Successfully fetches and extracts content from a URL."""
        html_content = b"<html><head><title>Test Page</title></head><body><p>Main content here</p></body></html>"
        mock_response = MagicMock()
        mock_response.read.return_value = html_content
        mock_response.headers.get.return_value = "text/html; charset=utf-8"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        agent = ResearchAgent(config=config, context=mock_context_plenty_of_time, logger=logger)
        item = RSSItem(
            title="Test",
            description="No extra URLs",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/test",
        )
        result = agent.research(item)

        assert result.skipped is False
        assert len(result.gathered_content) == 1
        assert "Main content" in result.gathered_content[0].text

    @patch("src.pipeline.research_agent.urlopen")
    def test_handles_fetch_failure_gracefully(self, mock_urlopen, config, logger, mock_context_plenty_of_time):
        """Failed URL fetches are logged and added to error_links."""
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("Connection refused")

        agent = ResearchAgent(config=config, context=mock_context_plenty_of_time, logger=logger)
        item = RSSItem(
            title="Test",
            description="No extra URLs",
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/test",
        )
        result = agent.research(item)

        assert result.skipped is False
        assert result.gathered_content == []
        assert "https://aws.amazon.com/whats-new/test" in result.error_links


class TestTextExtractor:
    """Tests for the HTML text extraction logic."""

    def test_extracts_body_text(self):
        """Extracts text from paragraph elements."""
        extractor = _TextExtractor()
        extractor.feed("<html><body><p>Hello world</p></body></html>")
        assert "Hello world" in extractor.text

    def test_strips_nav_content(self):
        """Navigation content is excluded."""
        extractor = _TextExtractor()
        extractor.feed("<html><body><nav>Menu items</nav><p>Main content</p></body></html>")
        assert "Menu items" not in extractor.text
        assert "Main content" in extractor.text

    def test_strips_header_content(self):
        """Header content is excluded."""
        extractor = _TextExtractor()
        extractor.feed("<html><body><header>Site header</header><p>Article text</p></body></html>")
        assert "Site header" not in extractor.text
        assert "Article text" in extractor.text

    def test_strips_footer_content(self):
        """Footer content is excluded."""
        extractor = _TextExtractor()
        extractor.feed("<html><body><p>Content</p><footer>Copyright 2025</footer></body></html>")
        assert "Copyright 2025" not in extractor.text
        assert "Content" in extractor.text

    def test_strips_script_and_style(self):
        """Script and style tags are excluded."""
        extractor = _TextExtractor()
        extractor.feed("<html><head><style>body{color:red}</style></head><body><script>alert('x')</script><p>Real text</p></body></html>")
        assert "color:red" not in extractor.text
        assert "alert" not in extractor.text
        assert "Real text" in extractor.text

    def test_extracts_title(self):
        """Page title is extracted."""
        extractor = _TextExtractor()
        extractor.feed("<html><head><title>My Page Title</title></head><body><p>Content</p></body></html>")
        assert extractor.title == "My Page Title"

    def test_empty_html(self):
        """Empty HTML produces empty text."""
        extractor = _TextExtractor()
        extractor.feed("")
        assert extractor.text == ""
        assert extractor.title == ""


class TestOutboundFetchBounds:
    """Fetch hardening (docs/audit-remediation-plan.md item 13): size cap,
    URL cap, https-only. Prerequisite for the multi-source track, which
    feeds user-submitted URLs (Hacker News) into this exact code path."""

    def test_url_count_capped_at_maximum(self, config, logger, mock_context_plenty_of_time):
        from src.pipeline.research_agent import _MAX_URLS_PER_ITEM

        hrefs = " ".join(
            f'<a href="https://example.com/p{i}">x</a>' for i in range(20)
        )
        item = RSSItem(title="t", description=hrefs, pub_date="2025-01-15",
                       link="https://aws.amazon.com/whats-new/cap-test")
        agent = ResearchAgent(config=config, context=mock_context_plenty_of_time, logger=logger)

        urls = agent._extract_urls(item)

        assert len(urls) == _MAX_URLS_PER_ITEM
        assert urls[0] == item.link  # own link always keeps the first slot

    def test_http_urls_are_dropped(self, config, logger, mock_context_plenty_of_time):
        item = RSSItem(
            title="t",
            description='<a href="http://insecure.example.com/page">x</a> '
                        '<a href="https://secure.example.com/page">y</a>',
            pub_date="2025-01-15",
            link="https://aws.amazon.com/whats-new/scheme-test",
        )
        agent = ResearchAgent(config=config, context=mock_context_plenty_of_time, logger=logger)

        urls = agent._extract_urls(item)

        assert "https://secure.example.com/page" in urls
        assert all(u.startswith("https://") for u in urls)

    def test_oversized_response_truncated_not_raised(
        self, config, logger, mock_context_plenty_of_time
    ):
        from src.pipeline.research_agent import _MAX_RESPONSE_BYTES

        big_html = b"<html><title>big</title><body>" + b"A" * (_MAX_RESPONSE_BYTES + 100_000)

        class FakeResponse:
            headers = {"Content-Type": "text/html; charset=utf-8"}
            def read(self, n=-1):
                return big_html[:n] if n and n > 0 else big_html
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        FakeResponse.headers = MagicMock()
        FakeResponse.headers.get = lambda k, d="": "text/html; charset=utf-8"

        agent = ResearchAgent(config=config, context=mock_context_plenty_of_time, logger=logger)
        with patch("src.pipeline.research_agent.urlopen", return_value=FakeResponse()):
            page = agent._fetch_and_extract("https://example.com/huge")

        # Truncated, decoded, and extracted — no exception, bounded memory
        assert page.title == "big"
        assert len(page.text) <= _MAX_RESPONSE_BYTES

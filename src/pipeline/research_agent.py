"""Research Agent module for the AI Radar AWS pipeline.

Follows links in announcements to gather additional context from
blogposts and documentation pages. Tracks remaining Lambda execution
time to avoid exceeding the timeout.
"""

import re
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.request import Request, urlopen

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import PageContent, RSSItem, ResearchContext


# Tags whose content should be excluded (navigation, headers, footers, ads)
_EXCLUDED_TAGS = frozenset({
    "nav", "header", "footer", "aside", "script", "style",
    "noscript", "iframe", "form", "button", "svg",
})

# Regex to extract URLs from HTML content (href attributes)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)

# Regex to match http/https URLs in plain text
_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')

# Default safety margin in milliseconds (30 seconds)
_SAFETY_MARGIN_MS = 30_000

# Per-URL fetch timeout in seconds
_URL_FETCH_TIMEOUT = 15


class _TextExtractor(HTMLParser):
    """HTML parser that extracts main text content, skipping boilerplate elements."""

    def __init__(self) -> None:
        super().__init__()
        self._text_parts: list[str] = []
        self._skip_depth: int = 0
        self._title: str = ""
        self._in_title: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in _EXCLUDED_TAGS:
            self._skip_depth += 1
        if tag_lower == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in _EXCLUDED_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag_lower == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and not self._title:
            self._title = data.strip()
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._text_parts.append(stripped)

    @property
    def text(self) -> str:
        """Return the extracted text content joined with spaces."""
        return " ".join(self._text_parts)

    @property
    def title(self) -> str:
        """Return the extracted page title."""
        return self._title


class ResearchAgent:
    """Gathers additional context by following links in announcements.

    Extracts URLs from the announcement description and link field,
    fetches page content, strips HTML boilerplate, and returns a
    ResearchContext. Tracks remaining Lambda execution time to skip
    research when time is running low.
    """

    def __init__(self, config: Config, context, logger: StructuredLogger) -> None:
        self._config = config
        self._context = context
        self._logger = logger

    def research(self, item: RSSItem) -> ResearchContext:
        """Research a single announcement by following its links.

        Extracts URLs from the item's description and link field, fetches
        each page, and extracts the main text content.

        If remaining Lambda execution time is insufficient, returns a
        ResearchContext with skipped=True.
        """
        # Check if we have enough time to research this announcement
        if not self._has_sufficient_time():
            self._logger.warning(
                "Research skipped due to insufficient remaining time",
                announcement_link=item.link,
                announcement_title=item.title,
                remaining_time_ms=self._context.get_remaining_time_in_millis(),
            )
            return ResearchContext(gathered_content=[], skipped=True)

        # Extract URLs to research
        urls = self._extract_urls(item)

        gathered_content: list[PageContent] = []
        error_links: list[str] = []

        for url in urls:
            try:
                page_content = self._fetch_and_extract(url)
                if page_content.text:
                    gathered_content.append(page_content)
            except Exception as exc:
                self._logger.warning(
                    "Failed to fetch research URL",
                    url=url,
                    announcement_link=item.link,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                error_links.append(url)

        self._logger.info(
            "Research completed for announcement",
            announcement_link=item.link,
            urls_attempted=len(urls),
            urls_successful=len(gathered_content),
            urls_failed=len(error_links),
        )

        return ResearchContext(
            gathered_content=gathered_content,
            skipped=False,
            error_links=error_links,
        )

    def _has_sufficient_time(self) -> bool:
        """Check if there is enough remaining Lambda time for research.

        Returns True if remaining time >= (research_timeout × 1000 + safety_margin).
        """
        remaining_ms = self._context.get_remaining_time_in_millis()
        required_ms = (self._config.research_timeout_per_announcement * 1000) + _SAFETY_MARGIN_MS
        return remaining_ms >= required_ms

    def _extract_urls(self, item: RSSItem) -> list[str]:
        """Extract unique URLs from the announcement's link and description.

        Returns a deduplicated list of URLs found in the item's link field
        and any URLs embedded in the description text.
        """
        urls: list[str] = []
        seen: set[str] = set()

        # Always include the announcement's own link
        if item.link and item.link not in seen:
            urls.append(item.link)
            seen.add(item.link)

        # Extract URLs from description (both href attributes and plain text)
        description_urls = self._extract_urls_from_text(item.description)
        for url in description_urls:
            if url not in seen:
                urls.append(url)
                seen.add(url)

        return urls

    def _extract_urls_from_text(self, text: str) -> list[str]:
        """Extract HTTP/HTTPS URLs from text content.

        Looks for both href attributes in HTML and plain-text URLs.
        """
        urls: list[str] = []

        # Extract from href attributes
        for match in _HREF_RE.finditer(text):
            url = match.group(1)
            if url.startswith(("http://", "https://")):
                urls.append(url)

        # Extract plain-text URLs
        for match in _URL_RE.finditer(text):
            url = match.group(0)
            if url not in urls:
                urls.append(url)

        return urls

    def _fetch_and_extract(self, url: str) -> PageContent:
        """Fetch a URL and extract its main text content.

        Strips navigation, headers, footers, and advertisements from
        the HTML to return only the meaningful text content.
        """
        request = Request(
            url,
            headers={"User-Agent": "AIRadarAWS/1.0"},
        )
        with urlopen(request, timeout=_URL_FETCH_TIMEOUT) as response:
            content_type = response.headers.get("Content-Type", "")
            # Only process HTML content
            if "html" not in content_type.lower() and "text" not in content_type.lower():
                return PageContent(url=url, text="", title="")

            raw_bytes = response.read()
            # Try to decode with charset from content-type, fallback to utf-8
            charset = self._extract_charset(content_type)
            html_content = raw_bytes.decode(charset, errors="replace")

        extractor = _TextExtractor()
        extractor.feed(html_content)

        return PageContent(
            url=url,
            text=extractor.text,
            title=extractor.title,
        )

    @staticmethod
    def _extract_charset(content_type: str) -> str:
        """Extract charset from Content-Type header, defaulting to utf-8."""
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                return part.split("=", 1)[1].strip().strip('"')
        return "utf-8"

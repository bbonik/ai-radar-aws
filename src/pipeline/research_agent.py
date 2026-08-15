"""Research Agent module for the AI Radar AWS pipeline.

Follows links in announcements to gather additional context from
blogposts and documentation pages. Tracks remaining Lambda execution
time to avoid exceeding the timeout.
"""

import re
import time
from html.parser import HTMLParser
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

# Reservation required in the gate before starting research on one item, in
# milliseconds. Deliberately much smaller than the per-item budget ceiling:
# the old gate reserved the full theoretical maximum (research_timeout ×
# 1000 + margin = 330 s), which silently disabled research for every item
# after ~minute 10 of a 15-minute Lambda even though a typical item takes
# seconds. The budget is enforced by an in-loop deadline instead.
# Plan: docs/audit-remediation-plan.md item 8, decision D7.
_GATE_RESERVATION_MS = 90_000

# Per-URL fetch timeout in seconds
_URL_FETCH_TIMEOUT = 15

# Outbound fetch bounds (docs/audit-remediation-plan.md item 13).
# These fetch URLs harvested from feed content — bound what any one page or
# item can cost. Redirect capping was considered and dropped: urlopen
# already errors on redirect loops, and the size cap bounds any target.
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # report generator uses ≤3000 chars/page
_MAX_URLS_PER_ITEM = 8                 # at 15s each, caps one item at ~2 min


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

        # Per-item budget: stop fetching further URLs once the budget is
        # spent (Requirement 4.4 — "up to 5 minutes" is a ceiling, not a
        # reservation) or once remaining Lambda time approaches the safety
        # margin. Content gathered before the deadline is kept.
        deadline = time.monotonic() + self._config.research_timeout_per_announcement

        for url in urls:
            if time.monotonic() >= deadline or not self._within_lambda_margin():
                self._logger.warning(
                    "Research budget exhausted, returning partial content",
                    announcement_link=item.link,
                    urls_completed=len(gathered_content) + len(error_links),
                    urls_remaining=len(urls) - len(gathered_content) - len(error_links),
                )
                break
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
        """Check if there is enough remaining Lambda time to START research.

        Requires a fixed 90 s reservation rather than the full theoretical
        per-item maximum. The per-item budget itself is enforced by the
        deadline inside research(); this gate only ensures a meaningful
        amount of work can happen before the Lambda margin is reached.
        """
        remaining_ms = self._context.get_remaining_time_in_millis()
        return remaining_ms >= _GATE_RESERVATION_MS + _SAFETY_MARGIN_MS

    def _within_lambda_margin(self) -> bool:
        """True while remaining Lambda time exceeds the safety margin."""
        return self._context.get_remaining_time_in_millis() > _SAFETY_MARGIN_MS

    def _extract_urls(self, item: RSSItem) -> list[str]:
        """Extract unique https URLs from the announcement's link and description.

        Returns a deduplicated list capped at _MAX_URLS_PER_ITEM, https only
        (cleartext fetches dropped — AWS links are all https). The item's own
        link always takes the first slot.
        """
        urls: list[str] = []
        seen: set[str] = set()

        # Always include the announcement's own link
        if item.link and item.link.startswith("https://") and item.link not in seen:
            urls.append(item.link)
            seen.add(item.link)

        # Extract URLs from description (both href attributes and plain text)
        description_urls = self._extract_urls_from_text(item.description)
        for url in description_urls:
            if not url.startswith("https://"):
                continue
            if url not in seen:
                urls.append(url)
                seen.add(url)

        if len(urls) > _MAX_URLS_PER_ITEM:
            self._logger.warning(
                "URL count capped for research",
                announcement_link=item.link,
                urls_found=len(urls),
                urls_kept=_MAX_URLS_PER_ITEM,
            )
            urls = urls[:_MAX_URLS_PER_ITEM]

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

            # Bounded read: one oversized (or hostile) page must not inflate
            # Lambda memory — the same failure class as the historical OOMs.
            # Over-length content is truncated, not rejected: partial page
            # text is still useful, and downstream uses ≤3000 chars anyway.
            raw_bytes = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw_bytes) > _MAX_RESPONSE_BYTES:
                self._logger.warning(
                    "Response truncated at size cap",
                    url=url,
                    cap_bytes=_MAX_RESPONSE_BYTES,
                )
                raw_bytes = raw_bytes[:_MAX_RESPONSE_BYTES]
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

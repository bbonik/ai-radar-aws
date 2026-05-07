"""RSS Fetcher module for the AI Radar AWS pipeline.

Fetches and parses the AWS "What's New" RSS feed with retry logic
and exponential backoff.
"""

import html
import re
import time
import xml.etree.ElementTree as ET
from urllib.error import URLError
from urllib.request import Request, urlopen

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import RSSItem

# Regex to strip HTML tags from description text
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class RSSFetcher:
    """Fetches and parses the AWS 'What's New' RSS feed.

    Implements retry logic with exponential backoff on failure.
    Returns a list of RSSItem dataclass instances extracted from the feed.
    """

    def __init__(self, config: Config, logger: StructuredLogger) -> None:
        self._config = config
        self._logger = logger

    def fetch(self) -> list[RSSItem]:
        """Fetch the RSS feed and return parsed items.

        Retries up to config.rss_max_retries times with exponential backoff
        (1s, 2s, 4s). On complete failure, logs the error and returns an
        empty list.
        """
        last_error: Exception | None = None

        for attempt in range(self._config.rss_max_retries + 1):
            try:
                xml_content = self._fetch_feed()
                items = self._parse_feed(xml_content)
                self._logger.info(
                    "RSS feed fetched successfully",
                    items_count=len(items),
                    attempt=attempt + 1,
                )
                return items
            except Exception as exc:
                last_error = exc
                self._logger.warning(
                    "RSS fetch attempt failed",
                    attempt=attempt + 1,
                    max_retries=self._config.rss_max_retries,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                # Apply exponential backoff if we have retries remaining
                if attempt < self._config.rss_max_retries:
                    backoff = 2**attempt  # 1s, 2s, 4s
                    time.sleep(backoff)

        # All retries exhausted
        self._logger.error(
            "RSS feed fetch failed after all retries",
            max_retries=self._config.rss_max_retries,
            error_type=type(last_error).__name__ if last_error else "Unknown",
            error_message=str(last_error) if last_error else "Unknown error",
        )
        return []

    def _fetch_feed(self) -> bytes:
        """Perform the HTTP request to fetch the RSS feed."""
        request = Request(
            self._config.rss_url,
            headers={"User-Agent": "AIRadarAWS/1.0"},
        )
        with urlopen(request, timeout=self._config.rss_fetch_timeout) as response:
            return response.read()

    def _parse_feed(self, xml_content: bytes) -> list[RSSItem]:
        """Parse RSS XML content and extract items."""
        root = ET.fromstring(xml_content)

        items: list[RSSItem] = []
        # RSS items are typically at channel/item
        for item_elem in root.iter("item"):
            title = self._get_element_text(item_elem, "title")
            description_raw = self._get_element_text(item_elem, "description")
            pub_date = self._get_element_text(item_elem, "pubDate")
            link = self._get_element_text(item_elem, "link")

            description = self._clean_description(description_raw)

            items.append(
                RSSItem(
                    title=title,
                    description=description,
                    pub_date=pub_date,
                    link=link,
                )
            )

        return items

    @staticmethod
    def _get_element_text(parent: ET.Element, tag: str) -> str:
        """Get text content of a child element, returning empty string if missing."""
        elem = parent.find(tag)
        if elem is not None and elem.text is not None:
            return elem.text.strip()
        return ""

    @staticmethod
    def _clean_description(raw: str) -> str:
        """Clean HTML entities and strip HTML tags from description text."""
        # Unescape HTML entities (e.g., &amp; -> &, &lt; -> <)
        text = html.unescape(raw)
        # Strip HTML tags
        text = _HTML_TAG_RE.sub("", text)
        return text.strip()

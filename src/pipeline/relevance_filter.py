"""Relevance Filter for AI/ML/GenAI announcements.

Evaluates RSS feed items against keyword patterns to determine
whether an announcement is related to AI/ML/GenAI topics.

Uses word-boundary matching to prevent false positives and applies
exclusion patterns before inclusion patterns.
"""

import re

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import RSSItem


class RelevanceFilter:
    """Filters RSS items for AI/ML/GenAI relevance using regex patterns.

    Matching is performed against the concatenation of the item title
    and the first 200 characters of the description. Exclusion patterns
    are applied first — if any exclusion matches, the item is rejected
    regardless of inclusion matches.

    An item is relevant if it matches at least one inclusion pattern
    and zero exclusion patterns.
    """

    def __init__(self, config: Config, logger: StructuredLogger) -> None:
        self.config = config
        self.logger = logger

        # Compile exclusion patterns (checked first)
        self._exclusion_patterns: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE)
            for p in self._get_exclusion_patterns()
        ]

        # Compile inclusion patterns
        self._inclusion_patterns: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE)
            for p in self._get_inclusion_patterns()
        ]

    def filter(self, items: list[RSSItem]) -> list[RSSItem]:
        """Return only relevant items from the input list.

        Args:
            items: List of RSS items to evaluate.

        Returns:
            List of items that pass relevance filtering.
        """
        relevant = [item for item in items if self.is_relevant(item)]
        self.logger.info(
            "Relevance filtering complete",
            total_items=len(items),
            relevant_items=len(relevant),
        )
        return relevant

    def is_relevant(self, item: RSSItem) -> bool:
        """Determine whether a single item is AI/ML/GenAI relevant.

        Matches against title + first 200 characters of description.
        Exclusion patterns are checked first. If any exclusion matches,
        the item is NOT relevant. Otherwise, the item is relevant if
        at least one inclusion pattern matches.

        Args:
            item: The RSS item to evaluate.

        Returns:
            True if the item is relevant, False otherwise.
        """
        text = item.title + " " + item.description[:200]

        # Apply exclusion patterns first
        for pattern in self._exclusion_patterns:
            if pattern.search(text):
                return False

        # Check inclusion patterns — need at least one match
        for pattern in self._inclusion_patterns:
            if pattern.search(text):
                return True

        return False

    @staticmethod
    def _get_exclusion_patterns() -> list[str]:
        """Return regex patterns for topics to exclude.

        These patterns identify announcements that mention AI-related
        terms but are not actually about AI/ML services (e.g., Amazon
        Connect uses 'agent' in a contact center context).
        """
        return [
            r"\bamazon connect\b",
            r"\bconnect\b.*\bagent\b",
            r"\bagent\b.*\bconnect\b",
        ]

    @staticmethod
    def _get_inclusion_patterns() -> list[str]:
        """Return regex patterns for AI/ML/GenAI topics.

        All patterns use word-boundary matching to prevent false
        positives (e.g., matching 'AI' but not 'SAID').
        """
        return [
            # Explicit AI/ML terms
            r"\bartificial intelligence\b",
            r"\bmachine learning\b",
            r"\bdeep learning\b",
            r"\bneural network\b",
            r"\bgenerative ai\b",
            r"\bgen ai\b",

            # AI/ML abbreviations (word boundaries prevent matching inside words)
            r"\bai\b",
            r"\bml\b",
            r"\bllm\b",

            # AI companies and models
            r"\bopenai\b",
            r"\banthropic\b",
            r"\bqwen\b",
            r"\bnova\b",
            r"\bamazon nova\b",

            # AWS AI services
            r"\bamazon bedrock\b",
            r"\bbedrock\b",
            r"\bamazon sagemaker\b",
            r"\bsagemaker\b",
            r"\bamazon comprehend\b",
            r"\bcomprehend\b",
            r"\bamazon rekognition\b",
            r"\brekognition\b",
            r"\bamazon textract\b",
            r"\btextract\b",
            r"\bamazon polly\b",
            r"\bpolly\b",
            r"\bamazon lex\b",
            r"\blex\b",
            r"\bamazon translate\b",
            r"\btranslate\b",
            r"\bamazon transcribe\b",
            r"\btranscribe\b",
            r"\bamazon personalize\b",
            r"\bpersonalize\b",
            r"\bamazon forecast\b",
            r"\bforecast\b",
            r"\bamazon kendra\b",
            r"\bkendra\b",
            r"\bfraud detector\b",

            # New AI services and tools
            r"\bamazon q\b",
            r"\bq developer\b",
            r"\bq business\b",
            r"\bamazon quicksight\b",
            r"\bquicksight q\b",
            r"\bamazon quick suite\b",
            r"\bquick suite\b",
            r"\bamazon quick\b",
            r"\bagentcore\b",
            r"\bagent core\b",
            r"\bkiro\b",
            r"\baws transform\b",

            # AI-related capabilities
            r"\bcomputer vision\b",
            r"\bnatural language\b",
            r"\btext analysis\b",
            r"\bsentiment analysis\b",
            r"\bimage recognition\b",
            r"\bspeech recognition\b",
            r"\bvoice synthesis\b",
            r"\btext-to-speech\b",
            r"\bspeech-to-text\b",

            # Agent and agentic AI terms
            r"\bagents\b",
            r"\bagentic ai\b",
            r"\bagentic\b",
            r"\bai agent\b",
            r"\bai agents\b",
            r"\bintelligent agent\b",
            r"\bautonomous agent\b",
            r"\bmulti-agent\b",
        ]

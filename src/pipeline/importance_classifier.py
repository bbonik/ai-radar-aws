"""Importance Classifier for AWS AI announcements.

Computes a point-based importance score for each announcement by
combining service tier points, blogpost link presence, word count,
and tag-based bonuses (when taxonomy tags are available).
Maps the raw score to a star level (1–5) using configurable thresholds.
"""

import re

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import AnnouncementTags, RSSItem


class ImportanceClassifier:
    """Classifies announcements by importance using a point-based scoring system.

    The score is computed as:
        service_tier_points + (blogpost_points if has_links else 0) + (word_count × word_count_scale)

    Service tiers:
        - High: Amazon Bedrock, Amazon Bedrock AgentCore, Amazon SageMaker AI, Amazon QuickSight
        - Medium: SageMaker, SageMaker Unified Studio, Kiro
        - Base: All other relevant services

    Star mapping:
        - score < threshold_2_star → 1★
        - threshold_2_star ≤ score < threshold_3_star → 2★
        - threshold_3_star ≤ score < threshold_4_star → 3★
        - threshold_4_star ≤ score < threshold_5_star → 4★
        - score ≥ threshold_5_star → 5★
    """

    # Service tier mappings using taxonomy tag names (from tagger output)
    HIGH_TIER_TAGS = {"bedrock", "bedrock-agentcore", "sagemaker-ai"}

    MEDIUM_TIER_TAGS = {
        "sagemaker", "sagemaker-jumpstart", "sagemaker-hyperpod",
        "sagemaker-unified-studio", "kiro", "quicksight", "quick", "quick-suite",
    }

    # Legacy text-based service detection (fallback when tags unavailable)
    HIGH_TIER_SERVICES = [
        "amazon bedrock",
        "amazon bedrock agentcore",
        "amazon sagemaker ai",
    ]

    MEDIUM_TIER_SERVICES = [
        "sagemaker",
        "sagemaker unified studio",
        "sagemaker jumpstart",
        "sagemaker hyperpod",
        "kiro",
        "amazon quick",
        "quick suite",
        "quicksight",
    ]

    def __init__(self, config: Config, logger: StructuredLogger) -> None:
        self.config = config
        self.logger = logger

        # Compile URL pattern for blogpost detection
        self._url_pattern = re.compile(r"https?://\S+")

    def classify(self, item: RSSItem, tags: AnnouncementTags | None = None) -> tuple[int, float]:
        """Classify an RSS item by importance.

        Args:
            item: The RSS item to classify.
            tags: Optional taxonomy tags (used for tag-based score bonuses).

        Returns:
            A tuple of (star_level, raw_score) where star_level is 1–5.
        """
        score = self.compute_score(item, tags)
        star_level = self._score_to_stars(score)

        self.logger.info(
            "Importance classification complete",
            title=item.title,
            score=score,
            star_level=star_level,
            service=self._extract_service(item),
        )

        return (star_level, score)

    def compute_score(self, item: RSSItem, tags: AnnouncementTags | None = None) -> float:
        """Compute the raw importance score for an item.

        Score = service_tier_points + blogpost_points + word_count_contribution + tag_bonus

        Args:
            item: The RSS item to score.
            tags: Optional taxonomy tags for tag-based bonuses.

        Returns:
            The computed importance score as a float.
        """
        # Use tags for service tier if available, fall back to text matching
        service_points = self._get_service_points_from_tags(tags)
        if service_points is None:
            service_name = self._extract_service(item)
            service_points = self._get_service_points(service_name)

        has_blogpost = self._has_blogpost_links(item)
        blogpost_points = self.config.blogpost_points if has_blogpost else 0

        word_count = len(item.description.split())
        word_count_contribution = word_count * self.config.word_count_scale

        tag_bonus = self._compute_tag_bonus(tags)

        return service_points + blogpost_points + word_count_contribution + tag_bonus

    def _get_service_points_from_tags(self, tags: AnnouncementTags | None) -> int | None:
        """Get service tier points from taxonomy tags.

        Returns the highest tier points if any service tags match, or None
        if no tags are available (caller should fall back to text matching).
        """
        if not tags or not tags.services:
            return None

        # Check if any tag matches high tier
        if any(tag in self.HIGH_TIER_TAGS for tag in tags.services):
            return self.config.service_points_high

        # Check if any tag matches medium tier
        if any(tag in self.MEDIUM_TIER_TAGS for tag in tags.services):
            return self.config.service_points_medium

        return self.config.service_points_base

    def _compute_tag_bonus(self, tags: AnnouncementTags | None) -> float:
        """Compute bonus points based on taxonomy tags.

        Applies configurable bonuses for specific announcement types.
        Only the highest applicable bonus is used (not cumulative).

        Args:
            tags: The taxonomy tags, or None if not available.

        Returns:
            The tag bonus points (0 if no tags or no matching bonuses).
        """
        if not tags:
            return 0.0

        bonus = 0.0

        # Type-based bonuses (highest wins, not cumulative)
        if tags.types:
            if "new-model" in tags.types:
                bonus = max(bonus, self.config.tag_bonus_new_model)
            if "new-service" in tags.types:
                bonus = max(bonus, self.config.tag_bonus_new_service)
            if "ga-launch" in tags.types:
                bonus = max(bonus, self.config.tag_bonus_ga_launch)

        # Provider-based bonus (additive — stacks with type bonus)
        if tags.providers:
            if "anthropic" in tags.providers or "openai" in tags.providers:
                bonus += self.config.tag_bonus_key_provider

        return bonus

    def _extract_service(self, item: RSSItem) -> str:
        """Extract the AWS service name from the item title or description.

        Checks for known service names in the title first, then falls back
        to the description. Returns the first matching service name found,
        or "Other" if no known service is identified.

        Args:
            item: The RSS item to extract the service from.

        Returns:
            The identified AWS service name.
        """
        text = (item.title + " " + item.description).lower()

        # Check high-tier services first (more specific names first)
        for service in self.HIGH_TIER_SERVICES:
            if service in text:
                return service.title()

        # Check medium-tier services
        for service in self.MEDIUM_TIER_SERVICES:
            if service in text:
                return service.title()

        return "Other"

    def _get_service_points(self, service_name: str) -> int:
        """Look up the point value for a service tier.

        Args:
            service_name: The extracted service name (title-cased).

        Returns:
            The point value for the service's tier.
        """
        service_lower = service_name.lower()

        for high_service in self.HIGH_TIER_SERVICES:
            if high_service == service_lower:
                return self.config.service_points_high

        for medium_service in self.MEDIUM_TIER_SERVICES:
            if medium_service == service_lower:
                return self.config.service_points_medium

        return self.config.service_points_base

    def _has_blogpost_links(self, item: RSSItem) -> bool:
        """Check if the item description contains external blogpost links.

        Looks for URLs (http:// or https://) in the description that are
        NOT the AWS whats-new URL (the item's own link).

        Args:
            item: The RSS item to check.

        Returns:
            True if external blogpost links are found.
        """
        urls = self._url_pattern.findall(item.description)

        for url in urls:
            # Exclude the item's own AWS whats-new link
            if not url.startswith("https://aws.amazon.com/about-aws/whats-new/"):
                return True

        return False

    def _score_to_stars(self, score: float) -> int:
        """Map a raw score to a star level using configured thresholds.

        Args:
            score: The raw importance score.

        Returns:
            Star level: 1, 2, 3, 4, or 5.
        """
        if score >= self.config.threshold_5_star:
            return 5
        elif score >= self.config.threshold_4_star:
            return 4
        elif score >= self.config.threshold_3_star:
            return 3
        elif score >= self.config.threshold_2_star:
            return 2
        else:
            return 1

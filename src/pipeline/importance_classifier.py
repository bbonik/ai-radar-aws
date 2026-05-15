"""Importance Classifier for AWS AI announcements.

Computes a point-based importance score for each announcement by
combining service tier points, blogpost link presence, word count,
tag-based bonuses (when taxonomy tags are available), and geographic
preference for region-expansion announcements.
Maps the raw score to a star level (1–5) using configurable thresholds.
"""

import re

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import AnnouncementTags, RSSItem


# ─── Geography Groups ─────────────────────────────────────────────────────────
# Keywords (all lowercase) that identify which geography a region expansion
# belongs to. Includes region codes, city names, and common variations.
# Source: AWS official region list as of May 2026.

GEOGRAPHY_KEYWORDS: dict[str, list[str]] = {
    "apj": [
        # Region codes
        "ap-east-1", "ap-east-2", "ap-northeast-1", "ap-northeast-2",
        "ap-northeast-3", "ap-south-1", "ap-south-2", "ap-southeast-1",
        "ap-southeast-2", "ap-southeast-3", "ap-southeast-4",
        "ap-southeast-5", "ap-southeast-6", "ap-southeast-7",
        # City/country names
        "hong kong", "taipei", "tokyo", "seoul", "osaka", "mumbai",
        "hyderabad", "singapore", "sydney", "jakarta", "melbourne",
        "malaysia", "new zealand", "thailand", "bangkok", "kuala lumpur",
        "auckland",
        # Common AWS phrasing
        "asia pacific", "asia-pacific",
    ],
    "emea": [
        # Region codes
        "eu-central-1", "eu-central-2", "eu-north-1", "eu-south-1",
        "eu-south-2", "eu-west-1", "eu-west-2", "eu-west-3",
        "eusc-de-east-1", "il-central-1", "me-central-1", "me-south-1",
        "af-south-1",
        # City/country names
        "frankfurt", "zurich", "stockholm", "milan", "spain", "ireland",
        "london", "paris", "germany", "tel aviv", "israel", "uae",
        "bahrain", "cape town", "south africa",
        # Common AWS phrasing
        "europe", "middle east", "africa",
        # Sovereign cloud
        "european sovereign",
    ],
    "americas": [
        # Region codes
        "us-east-1", "us-east-2", "us-west-1", "us-west-2",
        "ca-central-1", "ca-west-1", "sa-east-1", "mx-central-1",
        # City/country/state names
        "virginia", "n. virginia", "ohio", "oregon", "california",
        "n. california", "canada", "calgary", "sao paulo", "são paulo",
        "mexico",
        # Common AWS phrasing
        "us east", "us west", "south america",
    ],
    "gov": [
        # Region codes
        "us-gov-east-1", "us-gov-west-1",
        # Common phrasing
        "govcloud", "gov cloud", "us-gov",
    ],
}

# Keywords that indicate "all regions" / global availability
GLOBAL_AVAILABILITY_KEYWORDS: list[str] = [
    "all aws regions",
    "all supported regions",
    "all regions where",
    "all commercial regions",
    "globally available",
    "available globally",
    "worldwide",
    "all existing regions",
    "every region",
]


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

    # Services known to be available in APJ (used for GA inference)
    APJ_AVAILABLE_SERVICES = {
        "bedrock", "bedrock-agentcore", "sagemaker", "sagemaker-ai",
        "sagemaker-jumpstart", "sagemaker-hyperpod", "sagemaker-unified-studio",
        "quicksight", "quick", "quick-suite", "kiro", "q-developer",
    }

    def compute_geo_relevance(self, item: RSSItem, tags: AnnouncementTags | None = None) -> str:
        """Determine geographic relevance of an announcement.

        Logic (in order):
        1. "all regions" keywords → "global"
        2. Preferred geography keywords found → "local"
        3. Any non-preferred geography keywords found → "" (region-specific, not for you)
        4. No regions detected at all + GA/new-feature on APJ service → "global" (inferred)
        5. Otherwise → ""

        Returns:
            "local" — explicitly mentions the user's preferred geography
            "global" — all regions, or inferred global for GA/new-feature (no region mentioned)
            "" — not relevant or unknown
        """
        preferred = self.config.preferred_geography.lower()
        if preferred == "global":
            return ""  # No preference → no badge needed

        text = (item.title + " " + item.description).lower()

        # Step 1: Check for "all regions" / global availability keywords
        for keyword in GLOBAL_AVAILABILITY_KEYWORDS:
            if keyword in text:
                return "global"

        # Step 2: Check if preferred geography is explicitly mentioned
        preferred_mentioned = False
        if preferred in GEOGRAPHY_KEYWORDS:
            for keyword in GEOGRAPHY_KEYWORDS[preferred]:
                if keyword in text:
                    preferred_mentioned = True
                    break

        if preferred_mentioned:
            return "local"

        # Step 3: Check if ANY non-preferred geography is mentioned
        any_region_mentioned = False
        for geography, keywords in GEOGRAPHY_KEYWORDS.items():
            if geography == preferred:
                continue
            for keyword in keywords:
                if keyword in text:
                    any_region_mentioned = True
                    break
            if any_region_mentioned:
                break

        if any_region_mentioned:
            return ""  # Region-specific to somewhere else

        # Step 4: No regions detected — infer global for GA/new-feature on APJ-available service
        if tags and ("ga-launch" in tags.types or "new-feature" in tags.types):
            if any(svc in self.APJ_AVAILABLE_SERVICES for svc in tags.services):
                return "global"

        return ""

    def compute_score(self, item: RSSItem, tags: AnnouncementTags | None = None) -> float:
        """Compute the raw importance score for an item.

        Score = service_tier_points + blogpost_points + word_count_contribution
                + tag_bonus + region_geography_modifier

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

        region_modifier = self._compute_region_geography_modifier(item, tags)

        return service_points + blogpost_points + word_count_contribution + tag_bonus + region_modifier

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

    def _compute_region_geography_modifier(
        self, item: RSSItem, tags: AnnouncementTags | None
    ) -> float:
        """Compute a score modifier for region-expansion announcements based on geography.

        Only applies when the announcement has a "region-expansion" type tag.
        Scans the title and description (case-insensitive) for geography keywords.

        Logic:
        - If preferred_geography is "global" → no modifier (0.0)
        - If region-expansion AND mentions preferred geography → bonus
        - If region-expansion AND mentions ONLY other geographies → penalty
        - If region-expansion AND no specific regions detected → neutral (0.0)
        - If NOT region-expansion → no modifier (0.0)

        Args:
            item: The RSS item to check.
            tags: Taxonomy tags (checked for "region-expansion" type).

        Returns:
            The geographic modifier (positive bonus, negative penalty, or 0).
        """
        # Only applies to region-expansion announcements
        if not tags or "region-expansion" not in tags.types:
            return 0.0

        # If user has no geographic preference, skip
        preferred = self.config.preferred_geography.lower()
        if preferred == "global":
            return 0.0

        # Scan text for geography keywords (case-insensitive)
        text = (item.title + " " + item.description).lower()

        # Determine which geographies are mentioned
        mentioned_geographies: set[str] = set()
        for geography, keywords in GEOGRAPHY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    mentioned_geographies.add(geography)
                    break  # One match per geography is enough

        # No specific regions detected → neutral
        if not mentioned_geographies:
            return 0.0

        # Check if preferred geography is mentioned
        if preferred in mentioned_geographies:
            return self.config.region_expansion_bonus_local
        else:
            return self.config.region_expansion_penalty_remote

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

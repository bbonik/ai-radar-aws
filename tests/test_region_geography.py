"""Tests for region-expansion geographic preference scoring."""
import sys

import pytest

sys.path.insert(0, ".")
from src.config import Config
from src.pipeline.importance_classifier import ImportanceClassifier, GEOGRAPHY_KEYWORDS
from src.shared.logger import StructuredLogger
from src.shared.models import AnnouncementTags, RSSItem


@pytest.fixture
def classifier():
    config = Config()
    config.preferred_geography = "apj"
    logger = StructuredLogger(lambda_name="test", run_id="test")
    return ImportanceClassifier(config, logger)


def _make_item(title: str, description: str = "") -> RSSItem:
    return RSSItem(
        title=title,
        link="https://example.com",
        description=description,
        pub_date="2026-05-13",
    )


def _make_tags(types=None, services=None):
    return AnnouncementTags(
        services=services or ["bedrock"],
        types=types or [],
        concepts=[],
        use_cases=[],
        providers=[],
    )


class TestRegionGeographyModifier:
    def test_apj_region_gets_bonus(self, classifier):
        """Region expansion mentioning Tokyo should get a bonus for APJ user."""
        item = _make_item(
            "Amazon Bedrock now available in Asia Pacific (Tokyo)",
            "We are excited to announce availability in ap-northeast-1.",
        )
        tags = _make_tags(types=["region-expansion"])
        modifier = classifier._compute_region_geography_modifier(item, tags)
        assert modifier == 1.0

    def test_emea_region_gets_penalty(self, classifier):
        """Region expansion only in Frankfurt should get penalty for APJ user."""
        item = _make_item(
            "Amazon Bedrock now available in Europe (Frankfurt)",
            "Now available in eu-central-1.",
        )
        tags = _make_tags(types=["region-expansion"])
        modifier = classifier._compute_region_geography_modifier(item, tags)
        assert modifier == -1.5

    def test_americas_region_gets_penalty(self, classifier):
        """Region expansion only in US regions should get penalty for APJ user."""
        item = _make_item(
            "Amazon SageMaker now available in US East (Ohio)",
            "Customers can now use SageMaker in us-east-2.",
        )
        tags = _make_tags(types=["region-expansion"])
        modifier = classifier._compute_region_geography_modifier(item, tags)
        assert modifier == -1.5

    def test_govcloud_gets_penalty(self, classifier):
        """GovCloud expansion should get penalty for APJ user."""
        item = _make_item(
            "Amazon Bedrock now available in AWS GovCloud (US-West)",
            "Available in us-gov-west-1.",
        )
        tags = _make_tags(types=["region-expansion"])
        modifier = classifier._compute_region_geography_modifier(item, tags)
        assert modifier == -1.5

    def test_mixed_regions_with_apj_gets_bonus(self, classifier):
        """If expansion mentions both APJ and other regions, bonus applies."""
        item = _make_item(
            "Amazon Bedrock expands to 5 new regions",
            "Now available in Tokyo, Frankfurt, Sydney, and Oregon.",
        )
        tags = _make_tags(types=["region-expansion"])
        modifier = classifier._compute_region_geography_modifier(item, tags)
        assert modifier == 1.0  # APJ is mentioned, so bonus

    def test_no_region_expansion_tag_no_modifier(self, classifier):
        """Non-region-expansion announcements get no modifier."""
        item = _make_item(
            "Amazon Bedrock launches new feature in Tokyo",
            "A great new feature available in ap-northeast-1.",
        )
        tags = _make_tags(types=["new-feature"])
        modifier = classifier._compute_region_geography_modifier(item, tags)
        assert modifier == 0.0

    def test_no_regions_detected_neutral(self, classifier):
        """Region expansion with no specific regions mentioned is neutral."""
        item = _make_item(
            "Amazon Bedrock expands to additional regions",
            "Now available in more regions worldwide.",
        )
        tags = _make_tags(types=["region-expansion"])
        modifier = classifier._compute_region_geography_modifier(item, tags)
        assert modifier == 0.0

    def test_global_preference_no_modifier(self, classifier):
        """If preferred_geography is 'global', no modifier is applied."""
        classifier.config.preferred_geography = "global"
        item = _make_item(
            "Amazon Bedrock now available in Europe (Frankfurt)",
            "Now available in eu-central-1.",
        )
        tags = _make_tags(types=["region-expansion"])
        modifier = classifier._compute_region_geography_modifier(item, tags)
        assert modifier == 0.0

    def test_case_insensitive_matching(self, classifier):
        """Keywords should match regardless of case in the announcement."""
        item = _make_item(
            "AMAZON BEDROCK NOW AVAILABLE IN ASIA PACIFIC (TOKYO)",
            "Available in AP-NORTHEAST-1 region.",
        )
        tags = _make_tags(types=["region-expansion"])
        modifier = classifier._compute_region_geography_modifier(item, tags)
        assert modifier == 1.0

    def test_no_tags_no_modifier(self, classifier):
        """If tags are None, no modifier is applied."""
        item = _make_item("Some announcement about Tokyo")
        modifier = classifier._compute_region_geography_modifier(item, None)
        assert modifier == 0.0

    def test_singapore_detected_as_apj(self, classifier):
        """Singapore should be detected as APJ."""
        item = _make_item(
            "Service now available in Singapore",
            "Customers in ap-southeast-1 can now use this.",
        )
        tags = _make_tags(types=["region-expansion"])
        modifier = classifier._compute_region_geography_modifier(item, tags)
        assert modifier == 1.0

    def test_sao_paulo_detected_as_americas(self, classifier):
        """Sao Paulo should be detected as Americas, penalty for APJ user."""
        item = _make_item(
            "Service now available in South America (Sao Paulo)",
            "Available in sa-east-1.",
        )
        tags = _make_tags(types=["region-expansion"])
        modifier = classifier._compute_region_geography_modifier(item, tags)
        assert modifier == -1.5


class TestGeographyKeywordsCoverage:
    def test_all_geographies_have_keywords(self):
        """All geography groups should have keywords defined."""
        assert "apj" in GEOGRAPHY_KEYWORDS
        assert "emea" in GEOGRAPHY_KEYWORDS
        assert "americas" in GEOGRAPHY_KEYWORDS
        assert "gov" in GEOGRAPHY_KEYWORDS

    def test_all_keywords_are_lowercase(self):
        """All keywords must be lowercase for case-insensitive matching."""
        for geo, keywords in GEOGRAPHY_KEYWORDS.items():
            for kw in keywords:
                assert kw == kw.lower(), f"Keyword '{kw}' in {geo} is not lowercase"

    def test_apj_covers_all_ap_regions(self):
        """APJ should cover all ap-* region codes."""
        ap_regions = [
            "ap-east-1", "ap-east-2", "ap-northeast-1", "ap-northeast-2",
            "ap-northeast-3", "ap-south-1", "ap-south-2", "ap-southeast-1",
            "ap-southeast-2", "ap-southeast-3", "ap-southeast-4",
            "ap-southeast-5", "ap-southeast-6", "ap-southeast-7",
        ]
        for region in ap_regions:
            assert region in GEOGRAPHY_KEYWORDS["apj"], f"{region} missing from APJ"

    def test_emea_covers_all_eu_me_af_regions(self):
        """EMEA should cover all eu-*, me-*, af-*, il-* region codes."""
        emea_regions = [
            "eu-central-1", "eu-central-2", "eu-north-1", "eu-south-1",
            "eu-south-2", "eu-west-1", "eu-west-2", "eu-west-3",
            "me-central-1", "me-south-1", "af-south-1", "il-central-1",
        ]
        for region in emea_regions:
            assert region in GEOGRAPHY_KEYWORDS["emea"], f"{region} missing from EMEA"

    def test_americas_covers_all_us_ca_sa_mx_regions(self):
        """Americas should cover all us-*, ca-*, sa-*, mx-* region codes."""
        americas_regions = [
            "us-east-1", "us-east-2", "us-west-1", "us-west-2",
            "ca-central-1", "ca-west-1", "sa-east-1", "mx-central-1",
        ]
        for region in americas_regions:
            assert region in GEOGRAPHY_KEYWORDS["americas"], f"{region} missing from Americas"



class TestComputeGeoRelevance:
    """Tests for the public compute_geo_relevance method."""

    def test_local_when_apj_mentioned(self, classifier):
        """Should return 'local' when APJ region is mentioned."""
        item = _make_item(
            "Amazon Bedrock now available in Asia Pacific (Tokyo)",
            "Customers in ap-northeast-1 can now use Bedrock.",
        )
        assert classifier.compute_geo_relevance(item) == "local"

    def test_global_when_all_regions(self, classifier):
        """Should return 'global' when 'all regions' is mentioned."""
        item = _make_item(
            "Amazon Bedrock feature available in all AWS Regions",
            "This feature is now available in all AWS Regions where Bedrock is supported.",
        )
        assert classifier.compute_geo_relevance(item) == "global"

    def test_global_when_globally_available(self, classifier):
        """Should return 'global' for 'globally available' phrasing."""
        item = _make_item(
            "New feature globally available",
            "This feature is now globally available.",
        )
        assert classifier.compute_geo_relevance(item) == "global"

    def test_empty_when_only_emea(self, classifier):
        """Should return '' when only EMEA regions mentioned (not relevant to APJ)."""
        item = _make_item(
            "Service now available in Europe (Frankfurt)",
            "Available in eu-central-1.",
        )
        assert classifier.compute_geo_relevance(item) == ""

    def test_empty_when_no_region_info(self, classifier):
        """Should return '' when no region information is present."""
        item = _make_item(
            "Amazon Bedrock adds new model support",
            "A new foundation model is now available.",
        )
        assert classifier.compute_geo_relevance(item) == ""

    def test_empty_when_global_preference(self, classifier):
        """Should return '' when preferred_geography is 'global'."""
        classifier.config.preferred_geography = "global"
        item = _make_item(
            "Service available in Tokyo",
            "Now in ap-northeast-1.",
        )
        assert classifier.compute_geo_relevance(item) == ""

    def test_local_with_singapore(self, classifier):
        """Singapore mention should return 'local' for APJ user."""
        item = _make_item(
            "Service expands to Singapore",
            "Now available in the Singapore region.",
        )
        assert classifier.compute_geo_relevance(item) == "local"

    def test_global_takes_priority_over_local(self, classifier):
        """'All regions' should return 'global' even if APJ is also mentioned."""
        item = _make_item(
            "Feature available in all supported regions including Tokyo",
            "Now available in all supported regions.",
        )
        assert classifier.compute_geo_relevance(item) == "global"

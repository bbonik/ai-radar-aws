"""Unit tests for the data models module."""

import pytest

from src.shared.models import (
    AnnouncementError,
    PageContent,
    PipelineRunSummary,
    ProcessedAnnouncement,
    Report,
    ResearchContext,
    RSSItem,
)


class TestRSSItem:
    """Tests for the RSSItem dataclass."""

    def test_creation(self):
        item = RSSItem(
            title="New Feature",
            description="A new feature was released",
            pub_date="2024-01-15",
            link="https://aws.amazon.com/about-aws/whats-new/2024/01/feature",
        )
        assert item.title == "New Feature"
        assert item.description == "A new feature was released"
        assert item.pub_date == "2024-01-15"
        assert item.link == "https://aws.amazon.com/about-aws/whats-new/2024/01/feature"


class TestPageContent:
    """Tests for the PageContent dataclass."""

    def test_creation(self):
        page = PageContent(
            url="https://example.com/blog",
            text="Blog content here",
            title="Blog Title",
        )
        assert page.url == "https://example.com/blog"
        assert page.text == "Blog content here"
        assert page.title == "Blog Title"


class TestResearchContext:
    """Tests for the ResearchContext dataclass."""

    def test_creation_with_defaults(self):
        ctx = ResearchContext(gathered_content=[])
        assert ctx.gathered_content == []
        assert ctx.skipped is False
        assert ctx.error_links == []

    def test_creation_with_content(self):
        page = PageContent(url="https://example.com", text="content", title="Title")
        ctx = ResearchContext(
            gathered_content=[page],
            skipped=True,
            error_links=["https://failed.com"],
        )
        assert len(ctx.gathered_content) == 1
        assert ctx.skipped is True
        assert ctx.error_links == ["https://failed.com"]


class TestReport:
    """Tests for the Report dataclass."""

    def test_creation(self):
        report = Report(
            whats_new="Summary",
            how_it_works="Technical details",
            why_important="Significance",
            how_different="Comparison",
            when_to_prefer="Guidance",
            availability="GA in us-east-1",
        )
        assert report.whats_new == "Summary"
        assert report.how_it_works == "Technical details"
        assert report.why_important == "Significance"
        assert report.how_different == "Comparison"
        assert report.when_to_prefer == "Guidance"
        assert report.availability == "GA in us-east-1"


class TestProcessedAnnouncement:
    """Tests for the ProcessedAnnouncement dataclass."""

    @pytest.fixture
    def sample_announcement(self):
        return ProcessedAnnouncement(
            title="Amazon Bedrock Update",
            description="New models available",
            pub_date="2024-01-15",
            link="https://aws.amazon.com/about-aws/whats-new/2024/01/bedrock",
            aws_service="Amazon Bedrock",
            importance_level=3,
            importance_score=7.5,
            report=Report(
                whats_new="New models",
                how_it_works="Via API",
                why_important="More options",
                how_different="Better performance",
                when_to_prefer="For complex tasks",
                availability="GA in all regions",
            ),
            mermaid_graph="graph TD\n  A-->B",
            blogpost_links=[
                "https://aws.amazon.com/blogs/post1",
                "https://aws.amazon.com/blogs/post2",
            ],
            first_detected="2024-01-15T10:00:00Z",
        )

    def test_to_csv_row_keys(self, sample_announcement):
        row = sample_announcement.to_csv_row()
        expected_keys = {
            "title",
            "description",
            "pub_date",
            "link",
            "aws_service",
            "importance_level",
            "importance_score",
            "whats_new",
            "how_it_works",
            "why_important",
            "how_different",
            "when_to_prefer",
            "availability",
            "mermaid_graph",
            "blogpost_links",
            "first_detected",
            "card_summary",
            "tags",
        }
        assert set(row.keys()) == expected_keys

    def test_to_csv_row_values(self, sample_announcement):
        row = sample_announcement.to_csv_row()
        assert row["title"] == "Amazon Bedrock Update"
        assert row["importance_level"] == "3"
        assert row["importance_score"] == "7.5"
        assert row["whats_new"] == "New models"
        assert row["mermaid_graph"] == "graph TD\n  A-->B"
        assert row["blogpost_links"] == "https://aws.amazon.com/blogs/post1|https://aws.amazon.com/blogs/post2"

    def test_to_csv_row_none_mermaid_graph(self, sample_announcement):
        sample_announcement.mermaid_graph = None
        row = sample_announcement.to_csv_row()
        assert row["mermaid_graph"] == ""

    def test_to_csv_row_empty_blogpost_links(self, sample_announcement):
        sample_announcement.blogpost_links = []
        row = sample_announcement.to_csv_row()
        assert row["blogpost_links"] == ""

    def test_from_csv_row(self, sample_announcement):
        row = sample_announcement.to_csv_row()
        restored = ProcessedAnnouncement.from_csv_row(row)
        assert restored.title == sample_announcement.title
        assert restored.description == sample_announcement.description
        assert restored.pub_date == sample_announcement.pub_date
        assert restored.link == sample_announcement.link
        assert restored.aws_service == sample_announcement.aws_service
        assert restored.importance_level == sample_announcement.importance_level
        assert restored.importance_score == sample_announcement.importance_score
        assert restored.report.whats_new == sample_announcement.report.whats_new
        assert restored.report.how_it_works == sample_announcement.report.how_it_works
        assert restored.report.why_important == sample_announcement.report.why_important
        assert restored.report.how_different == sample_announcement.report.how_different
        assert restored.report.when_to_prefer == sample_announcement.report.when_to_prefer
        assert restored.report.availability == sample_announcement.report.availability
        assert restored.mermaid_graph == sample_announcement.mermaid_graph
        assert restored.blogpost_links == sample_announcement.blogpost_links
        assert restored.first_detected == sample_announcement.first_detected

    def test_from_csv_row_none_mermaid(self):
        row = {
            "title": "Test",
            "description": "Desc",
            "pub_date": "2024-01-01",
            "link": "https://example.com",
            "aws_service": "Lambda",
            "importance_level": "1",
            "importance_score": "1.5",
            "whats_new": "New",
            "how_it_works": "Works",
            "why_important": "Important",
            "how_different": "Different",
            "when_to_prefer": "Prefer",
            "availability": "GA",
            "mermaid_graph": "",
            "blogpost_links": "",
            "first_detected": "2024-01-01T00:00:00Z",
        }
        restored = ProcessedAnnouncement.from_csv_row(row)
        assert restored.mermaid_graph is None
        assert restored.blogpost_links == []

    def test_from_csv_row_single_blogpost_link(self):
        row = {
            "title": "Test",
            "description": "Desc",
            "pub_date": "2024-01-01",
            "link": "https://example.com",
            "aws_service": "Lambda",
            "importance_level": "2",
            "importance_score": "4.0",
            "whats_new": "New",
            "how_it_works": "Works",
            "why_important": "Important",
            "how_different": "Different",
            "when_to_prefer": "Prefer",
            "availability": "GA",
            "mermaid_graph": "graph TD\n  A-->B",
            "blogpost_links": "https://aws.amazon.com/blogs/post1",
            "first_detected": "2024-01-01T00:00:00Z",
        }
        restored = ProcessedAnnouncement.from_csv_row(row)
        assert restored.blogpost_links == ["https://aws.amazon.com/blogs/post1"]

    def test_csv_round_trip(self, sample_announcement):
        """Serialize and deserialize should produce equivalent object."""
        row = sample_announcement.to_csv_row()
        restored = ProcessedAnnouncement.from_csv_row(row)
        assert restored == sample_announcement


class TestAnnouncementError:
    """Tests for the AnnouncementError dataclass."""

    def test_creation(self):
        error = AnnouncementError(
            link="https://aws.amazon.com/whats-new/2024/01/feature",
            title="Feature X",
            stage="report_gen",
            error_type="BedrockError",
            error_message="Model throttled",
            timestamp="2024-01-15T10:00:00Z",
            run_id="abc-123",
        )
        assert error.link == "https://aws.amazon.com/whats-new/2024/01/feature"
        assert error.stage == "report_gen"
        assert error.error_type == "BedrockError"
        assert error.run_id == "abc-123"


class TestPipelineRunSummary:
    """Tests for the PipelineRunSummary dataclass."""

    def test_creation(self):
        summary = PipelineRunSummary(
            run_id="run-001",
            start_time="2024-01-15T10:00:00Z",
            end_time="2024-01-15T10:05:00Z",
            total_fetched=50,
            total_deduplicated=10,
            total_relevant=20,
            total_processed_ok=18,
            total_failed=2,
            failed_items=[
                {"link": "https://example.com/1", "title": "Failed 1", "stage": "research", "error": "timeout"}
            ],
            research_skipped=3,
            website_builder_invoked=True,
        )
        assert summary.run_id == "run-001"
        assert summary.total_fetched == 50
        assert summary.total_deduplicated == 10
        assert summary.total_relevant == 20
        assert summary.total_processed_ok == 18
        assert summary.total_failed == 2
        assert len(summary.failed_items) == 1
        assert summary.research_skipped == 3
        assert summary.website_builder_invoked is True

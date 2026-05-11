"""Data models for the AI Radar AWS pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class RSSItem:
    """A single item from the AWS 'What's New' RSS feed."""

    title: str
    description: str
    pub_date: str
    link: str


@dataclass
class AnnouncementTags:
    """Multi-dimensional taxonomy tags for an announcement.

    Dimensions:
    - services: AWS services involved (Dimension 1)
    - types: Announcement type (Dimension 2)
    - concepts: AI/ML concepts (Dimension 3)
    - use_cases: Use case / industry (Dimension 4)
    - providers: Model providers (Dimension 5)
    """

    services: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    use_cases: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)

    def all_tags(self) -> list[str]:
        """Return all tags as a flat list."""
        return self.services + self.types + self.concepts + self.use_cases + self.providers

    def serialize(self) -> str:
        """Serialize to JSON string for CSV storage."""
        return json.dumps({
            "services": self.services,
            "types": self.types,
            "concepts": self.concepts,
            "use_cases": self.use_cases,
            "providers": self.providers,
        })

    @classmethod
    def deserialize(cls, data: str) -> "AnnouncementTags":
        """Deserialize from JSON string."""
        if not data or data == "":
            return cls()
        try:
            d = json.loads(data)
            return cls(
                services=d.get("services", []),
                types=d.get("types", []),
                concepts=d.get("concepts", []),
                use_cases=d.get("use_cases", []),
                providers=d.get("providers", []),
            )
        except (json.JSONDecodeError, TypeError):
            return cls()


@dataclass
class PageContent:
    """Text content extracted from a web page."""

    url: str
    text: str
    title: str


@dataclass
class ResearchContext:
    """Context gathered by the Research Agent for an announcement."""

    gathered_content: list[PageContent]
    skipped: bool = False
    error_links: list[str] = field(default_factory=list)


@dataclass
class Report:
    """Structured LLM-generated report for an announcement."""

    whats_new: str
    how_it_works: str
    why_important: str
    how_different: str
    when_to_prefer: str
    availability: str


@dataclass
class ProcessedAnnouncement:
    """A fully processed announcement with report and metadata."""

    title: str
    description: str
    pub_date: str
    link: str
    aws_service: str
    importance_level: int  # 1, 2, or 3
    importance_score: float
    report: Report
    mermaid_graph: str | None  # None for 1-star
    blogpost_links: list[str]
    first_detected: str  # ISO timestamp
    tags: AnnouncementTags = field(default_factory=AnnouncementTags)

    def to_csv_row(self) -> dict:
        """Serialize to a flat dict matching the CSV schema columns."""
        return {
            "title": self.title,
            "description": self.description,
            "pub_date": self.pub_date,
            "link": self.link,
            "aws_service": self.aws_service,
            "importance_level": str(self.importance_level),
            "importance_score": str(self.importance_score),
            "whats_new": self.report.whats_new,
            "how_it_works": self.report.how_it_works,
            "why_important": self.report.why_important,
            "how_different": self.report.how_different,
            "when_to_prefer": self.report.when_to_prefer,
            "availability": self.report.availability,
            "mermaid_graph": self.mermaid_graph if self.mermaid_graph is not None else "",
            "blogpost_links": "|".join(self.blogpost_links),
            "first_detected": self.first_detected,
            "tags": self.tags.serialize(),
        }

    @classmethod
    def from_csv_row(cls, row: dict) -> ProcessedAnnouncement:
        """Reconstruct a ProcessedAnnouncement from a CSV row dict."""
        report = Report(
            whats_new=row["whats_new"],
            how_it_works=row["how_it_works"],
            why_important=row["why_important"],
            how_different=row["how_different"],
            when_to_prefer=row["when_to_prefer"],
            availability=row["availability"],
        )

        mermaid_graph = row["mermaid_graph"] if row["mermaid_graph"] != "" else None

        blogpost_links_raw = row["blogpost_links"]
        blogpost_links = blogpost_links_raw.split("|") if blogpost_links_raw else []

        # Tags column may not exist in older CSV rows (backward compatibility)
        tags_raw = row.get("tags", "")
        tags = AnnouncementTags.deserialize(tags_raw)

        return cls(
            title=row["title"],
            description=row["description"],
            pub_date=row["pub_date"],
            link=row["link"],
            aws_service=row["aws_service"],
            importance_level=int(row["importance_level"]),
            importance_score=float(row["importance_score"]),
            report=report,
            mermaid_graph=mermaid_graph,
            blogpost_links=blogpost_links,
            first_detected=row["first_detected"],
            tags=tags,
        )


@dataclass
class AnnouncementError:
    """Record of a failed announcement processing attempt."""

    link: str
    title: str
    stage: str  # research, report_gen, graph_gen, storage
    error_type: str
    error_message: str
    timestamp: str  # ISO timestamp
    run_id: str  # Correlation ID of the pipeline run


@dataclass
class PipelineRunSummary:
    """Summary of a complete pipeline execution run."""

    run_id: str
    start_time: str
    end_time: str
    total_fetched: int
    total_deduplicated: int
    total_relevant: int
    total_processed_ok: int
    total_failed: int
    failed_items: list[dict]
    research_skipped: int
    website_builder_invoked: bool

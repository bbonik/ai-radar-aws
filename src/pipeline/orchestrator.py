"""Pipeline Orchestrator for the AI Radar AWS pipeline (Lambda 1).

Coordinates all pipeline stages sequentially:
fetch → deduplicate → filter → classify → research → report → graph → store

Tracks per-announcement success/failure, records failed announcements to an
error file, and generates a PipelineRunSummary at completion. Invokes Lambda 2
(Website Builder) asynchronously at the end.
"""

import json
import os
import re
from datetime import datetime, timezone

import boto3

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import (
    AnnouncementError,
    PipelineRunSummary,
    ProcessedAnnouncement,
    RSSItem,
)
from src.pipeline.graph_generator import GraphGenerator
from src.pipeline.importance_classifier import ImportanceClassifier
from src.pipeline.importance_classifier import GEOGRAPHY_KEYWORDS, GLOBAL_AVAILABILITY_KEYWORDS
from src.pipeline.relevance_filter import RelevanceFilter
from src.pipeline.report_generator import ReportGenerator, ReportGenerationError
from src.pipeline.research_agent import ResearchAgent
from src.pipeline.rss_fetcher import RSSFetcher
from src.pipeline.storage_manager import StorageManager
from src.pipeline.tagger import Tagger


class PipelineOrchestrator:
    """Orchestrates the full report generation pipeline.

    Coordinates all stages sequentially, tracks per-announcement success/failure,
    logs a structured PipelineRunSummary at completion, and invokes Lambda 2
    asynchronously at the end (fire-and-forget).

    Uses a correlation ID (UUID) for all log entries in this run.
    """

    def __init__(self, config: Config, context, logger: StructuredLogger) -> None:
        self._config = config
        self._context = context
        self._logger = logger
        self._run_id = logger.run_id

        # S3 data bucket from environment variable
        self._data_bucket = os.environ.get("DATA_BUCKET_NAME", "ai-radar-data")

        # Initialize pipeline components
        self._s3_client = boto3.client("s3", region_name=config.aws_region)
        self._rss_fetcher = RSSFetcher(config, logger)
        self._relevance_filter = RelevanceFilter(config, logger)
        self._importance_classifier = ImportanceClassifier(config, logger)
        self._tagger = Tagger(config, logger)
        self._research_agent = ResearchAgent(config, context, logger)
        self._report_generator = ReportGenerator(config, logger)
        self._graph_generator = GraphGenerator(config, logger)
        self._storage_manager = StorageManager(
            config, self._s3_client, logger, self._data_bucket
        )

    def run(self) -> PipelineRunSummary:
        """Execute the full pipeline and return a run summary.

        Stages: fetch → deduplicate → filter → classify → research → report → graph → store

        Individual announcement failures do not halt the pipeline. Failed
        announcements are recorded to the error file for retry/investigation.
        """
        start_time = datetime.now(timezone.utc)
        self._logger.info("Pipeline run started", run_id=self._run_id)

        # Stage 1: Fetch RSS feed
        all_items = self._rss_fetcher.fetch()
        total_fetched = len(all_items)
        self._logger.info("RSS fetch complete", total_fetched=total_fetched)

        # Stage 2: Deduplicate against existing announcements
        existing_links = self._storage_manager.load_existing_links()
        new_items = [item for item in all_items if item.link not in existing_links]
        total_deduplicated = total_fetched - len(new_items)
        self._logger.info(
            "Deduplication complete",
            total_new=len(new_items),
            total_deduplicated=total_deduplicated,
        )

        # Stage 3: Filter for AI/ML relevance
        relevant_items = self._relevance_filter.filter(new_items)
        total_relevant = len(relevant_items)
        self._logger.info("Relevance filtering complete", total_relevant=total_relevant)

        # Process each relevant announcement through remaining stages
        total_processed_ok = 0
        total_failed = 0
        research_skipped = 0
        failed_items: list[dict] = []

        for item in relevant_items:
            success, was_research_skipped, failure_info = self._process_announcement(item)
            if success:
                total_processed_ok += 1
            else:
                total_failed += 1
                if failure_info:
                    failed_items.append(failure_info)
            if was_research_skipped:
                research_skipped += 1

        # Invoke Lambda 2 (Website Builder) asynchronously
        website_builder_invoked = self._invoke_website_builder()

        # Generate run summary
        end_time = datetime.now(timezone.utc)
        summary = PipelineRunSummary(
            run_id=self._run_id,
            start_time=start_time.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            end_time=end_time.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            total_fetched=total_fetched,
            total_deduplicated=total_deduplicated,
            total_relevant=total_relevant,
            total_processed_ok=total_processed_ok,
            total_failed=total_failed,
            failed_items=failed_items,
            research_skipped=research_skipped,
            website_builder_invoked=website_builder_invoked,
        )

        # Log the summary
        duration_seconds = (end_time - start_time).total_seconds()
        self._logger.info(
            "Pipeline run complete",
            summary={
                "start_time": summary.start_time,
                "end_time": summary.end_time,
                "duration_seconds": round(duration_seconds, 1),
                "total_fetched": summary.total_fetched,
                "total_deduplicated": summary.total_deduplicated,
                "total_relevant": summary.total_relevant,
                "total_processed_ok": summary.total_processed_ok,
                "total_failed": summary.total_failed,
                "research_skipped": summary.research_skipped,
                "website_builder_invoked": summary.website_builder_invoked,
                "failed_items": summary.failed_items,
            },
        )

        return summary

    def _process_announcement(self, item: RSSItem) -> tuple[bool, bool, dict | None]:
        """Process a single announcement through classify → research → report → graph → store.

        Returns a tuple of (success, research_was_skipped, failure_info).
        failure_info is None on success, or a dict with link, title, stage, error on failure.
        On failure, records the error and continues.
        """
        research_skipped = False

        try:
            # Stage 4: Tag (non-fatal — empty tags on failure)
            tags = self._tagger.tag(item)

            # Stage 5: Classify importance (uses tags for bonus scoring)
            star_level, score = self._importance_classifier.classify(item, tags)

            # Stage 5b: Compute geographic relevance for card badge
            # Primary: use LLM tagger's geo_availability output
            # Fallback: keyword-based detection if tagger returns empty/unknown
            geo_relevance = self._resolve_geo_relevance(item, tags)

            # Stage 6: Research
            research_context = self._research_agent.research(item)
            if research_context.skipped:
                research_skipped = True

            # Stage 7: Generate report
            report = self._report_generator.generate(item, research_context)

            # Stage 8: Generate graph (only for 2-star and 3-star)
            mermaid_graph = self._graph_generator.generate(item, report, star_level, research_context)

            # Stage 9: Store the processed announcement
            processed = ProcessedAnnouncement(
                title=item.title,
                description=item.description,
                pub_date=item.pub_date,
                link=item.link,
                aws_service=self._extract_service_name(item),
                importance_level=star_level,
                importance_score=score,
                report=report,
                mermaid_graph=mermaid_graph,
                blogpost_links=self._extract_blogpost_links(item),
                first_detected=datetime.now(timezone.utc).isoformat(
                    timespec="milliseconds"
                ).replace("+00:00", "Z"),
                tags=tags,
                geo_relevance=geo_relevance,
            )

            saved = self._storage_manager.save_announcement(processed)
            if not saved:
                self._record_failure(item, "storage", "StorageError", "Failed to save announcement to S3 after all retries")
                return (False, research_skipped, {
                    "link": item.link,
                    "title": item.title,
                    "stage": "storage",
                    "error": "Failed to save announcement to S3 after all retries",
                })

            self._logger.info(
                "Announcement processed successfully",
                announcement_link=item.link,
                announcement_title=item.title,
                importance_level=star_level,
                importance_score=score,
            )
            return (True, research_skipped, None)

        except ReportGenerationError as exc:
            self._record_failure(item, "report_generation", type(exc).__name__, str(exc))
            return (False, research_skipped, {
                "link": item.link,
                "title": item.title,
                "stage": "report_generation",
                "error": f"{type(exc).__name__}: {exc}",
            })

        except Exception as exc:
            # Determine the stage based on what we know
            stage = self._determine_failure_stage(exc)
            self._record_failure(item, stage, type(exc).__name__, str(exc))
            return (False, research_skipped, {
                "link": item.link,
                "title": item.title,
                "stage": stage,
                "error": f"{type(exc).__name__}: {exc}",
            })

    def _record_failure(self, item: RSSItem, stage: str, error_type: str, error_message: str) -> None:
        """Record a failed announcement to the error file and log it."""
        self._logger.error(
            "Announcement processing failed",
            announcement_link=item.link,
            announcement_title=item.title,
            stage=stage,
            error_type=error_type,
            error_message=error_message,
        )

        error_record = AnnouncementError(
            link=item.link,
            title=item.title,
            stage=stage,
            error_type=error_type,
            error_message=error_message,
            timestamp=datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
            run_id=self._run_id,
        )

        self._storage_manager.save_error_record(error_record)

    def _invoke_website_builder(self) -> bool:
        """Invoke Lambda 2 (Website Builder) asynchronously.

        Fire-and-forget invocation. Returns True if invocation succeeded,
        False if it failed (website build will happen on next successful run).
        """
        function_name = os.environ.get(
            "WEBSITE_BUILDER_FUNCTION_NAME",
            self._config.website_builder_function_name,
        )

        payload = json.dumps({
            "run_id": self._run_id,
            "source": "pipeline-orchestrator",
        })

        try:
            lambda_client = boto3.client("lambda", region_name=self._config.aws_region)
            lambda_client.invoke(
                FunctionName=function_name,
                InvocationType="Event",  # Asynchronous invocation
                Payload=payload.encode("utf-8"),
            )
            self._logger.info(
                "Website builder Lambda invoked successfully",
                function_name=function_name,
            )
            return True

        except Exception as exc:
            self._logger.error(
                "Failed to invoke website builder Lambda",
                function_name=function_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return False

    def _extract_service_name(self, item: RSSItem) -> str:
        """Extract the AWS service name from the item using the importance classifier's logic."""
        return self._importance_classifier._extract_service(item)

    def _resolve_geo_relevance(self, item: RSSItem, tags) -> str:
        """Resolve geographic relevance as a comma-separated list of geographies.

        Combines LLM tagger output with keyword detection.
        Returns: "apj,emea" or "global" or "" (comma-separated).
        """
        text = (item.title + " " + item.description).lower()
        detected_geos: set[str] = set()

        # Check for "all regions" keywords → global
        for keyword in GLOBAL_AVAILABILITY_KEYWORDS:
            if keyword in text:
                return "global"

        # Check each geography via keywords
        for geo_name, keywords in GEOGRAPHY_KEYWORDS.items():
            if geo_name == "gov":
                # Map gov to americas
                for keyword in keywords:
                    if keyword in text:
                        detected_geos.add("americas")
                        break
            else:
                for keyword in keywords:
                    if keyword in text:
                        detected_geos.add(geo_name)
                        break

        # If keywords found geographies, use them
        if detected_geos:
            return ",".join(sorted(detected_geos))

        # Use tagger's geo_availability as fallback
        if tags and tags.geo_availability:
            if "global" in tags.geo_availability:
                return "global"
            valid_geos = [g for g in tags.geo_availability if g in ("apj", "emea", "americas")]
            if valid_geos:
                return ",".join(sorted(valid_geos))

        # Final fallback: infer global for GA/new-feature on APJ-available service
        if tags and ("ga-launch" in tags.types or "new-feature" in tags.types):
            if any(svc in self._importance_classifier.APJ_AVAILABLE_SERVICES for svc in tags.services):
                return "global"

        return ""

    def _extract_blogpost_links(self, item: RSSItem) -> list[str]:
        """Extract external blogpost links from the item description.

        Strips trailing punctuation from URLs and filters out:
        - The item's own AWS whats-new link
        - AWS service homepages (e.g., https://aws.amazon.com/transform)
        """
        url_pattern = re.compile(r"https?://\S+")
        raw_urls = url_pattern.findall(item.description)

        cleaned = []
        for url in raw_urls:
            # Strip trailing punctuation
            while url and url[-1] in ".),;:!?\"'":
                url = url[:-1]
            if not url:
                continue
            # Exclude the item's own AWS whats-new link
            if url.startswith("https://aws.amazon.com/about-aws/whats-new/"):
                continue
            # Exclude AWS service homepages
            if re.match(r"https?://aws\.amazon\.com/[a-z0-9-]+/?$", url):
                continue
            cleaned.append(url)

        return cleaned

    @staticmethod
    def _determine_failure_stage(exc: Exception) -> str:
        """Best-effort determination of which stage failed based on exception type."""
        exc_name = type(exc).__name__
        exc_msg = str(exc).lower()

        if "report" in exc_msg or "generation" in exc_msg:
            return "report_generation"
        if "graph" in exc_msg or "mermaid" in exc_msg:
            return "graph_generation"
        if "s3" in exc_msg or "storage" in exc_msg or "bucket" in exc_msg:
            return "storage"
        if "research" in exc_msg or "fetch" in exc_msg or "url" in exc_msg:
            return "research"

        return "unknown"

"""AI Radar AWS - Website Builder Module.

Generates a static website from processed announcement data stored in S3 CSV.
Produces index.html (listing + composable filters + timeline), individual report
pages, and shared CSS/JS assets. Uses Python string templates for HTML generation.

Features:
- Mermaid.js rendering for diagrams (CDN)
- Chart.js for timeline visualization (CDN)
- html2pdf.js for client-side PDF export (CDN)
- Client-side filtering (time period, service, importance ranking)
- Responsive design for desktop, tablet, and mobile
- "AI Radar AWS" branding with AWS-inspired color scheme
"""

import csv
import html
import io
import json
import os
import re
import sys
from collections import defaultdict

# Increase CSV field size limit to handle large descriptions/mermaid graphs
csv.field_size_limit(sys.maxsize)

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import AnnouncementTags, ProcessedAnnouncement


def _sanitize_html(text: str) -> str:
    """Sanitize text for safe HTML rendering, preventing XSS."""
    return html.escape(text, quote=True)


def _format_date_display(date_str: str) -> str:
    """Convert a date string to DD/MM/YYYY for display.
    
    Handles:
    - YYYY-MM-DD (ISO format)
    - RFC 2822 format (e.g., 'Wed, 29 Apr 2026 22:00:00 GMT')
    - Any other format (returned as-is)
    """
    from email.utils import parsedate_to_datetime
    
    # Try ISO format first (YYYY-MM-DD)
    if len(date_str) >= 10 and date_str[4:5] == "-" and date_str[7:8] == "-":
        parts = date_str[:10].split("-")
        if len(parts) == 3:
            return f"{parts[2]}/{parts[1]}/{parts[0]}"
    
    # Try RFC 2822 format
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        pass
    
    return date_str


def _extract_date_sortable(date_str: str) -> str:
    """Extract a YYYY-MM-DD sortable date from various formats.
    
    Used for data-date attributes and JS filtering.
    """
    from email.utils import parsedate_to_datetime
    
    # Already ISO format
    if len(date_str) >= 10 and date_str[4:5] == "-" and date_str[7:8] == "-":
        return date_str[:10]
    
    # Try RFC 2822 format
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    
    return date_str[:10] if len(date_str) >= 10 else date_str


def _markdown_to_html(text: str) -> str:
    """Convert simple markdown-like text to HTML.

    Handles:
    - **bold** -> <strong>bold</strong>
    - *italic* -> <em>italic</em>
    - Lines starting with '- ' or '• ' -> <li> items wrapped in <ul>
    - Blank lines -> paragraph breaks

    Input text should already be sanitized via _sanitize_html.
    """
    if not text:
        return "<p></p>"

    # Split into lines
    lines = text.split("\n")
    result_blocks: list[str] = []
    current_list: list[str] = []
    current_paragraph: list[str] = []

    def flush_paragraph():
        if current_paragraph:
            para_text = " ".join(current_paragraph)
            para_text = _apply_inline_formatting(para_text)
            result_blocks.append(f"<p>{para_text}</p>")
            current_paragraph.clear()

    def flush_list():
        if current_list:
            items = "".join(f"<li>{_apply_inline_formatting(item)}</li>" for item in current_list)
            result_blocks.append(f"<ul>{items}</ul>")
            current_list.clear()

    for line in lines:
        stripped = line.strip()

        if not stripped:
            # Blank line: flush current context
            flush_paragraph()
            flush_list()
            continue

        # Check for bullet point lines
        if stripped.startswith("- ") or stripped.startswith("&bull; ") or stripped.startswith("• "):
            flush_paragraph()
            # Remove the bullet prefix
            if stripped.startswith("- "):
                item_text = stripped[2:]
            elif stripped.startswith("&bull; "):
                item_text = stripped[7:]
            else:
                item_text = stripped[2:]
            current_list.append(item_text)
        else:
            flush_list()
            current_paragraph.append(stripped)

    # Flush remaining
    flush_paragraph()
    flush_list()

    return "".join(result_blocks) if result_blocks else "<p></p>"


def _apply_inline_formatting(text: str) -> str:
    """Apply inline markdown formatting (bold, italic) to text.

    Expects already-sanitized text (no raw HTML special chars).
    """
    # Bold: **text** -> <strong>text</strong>
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic: *text* -> <em>text</em>
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    return text


def _text_to_bullet_html(text: str) -> str:
    """Convert plain text to bullet-point HTML for report sections.

    If text already contains bullet points (lines starting with '- ' or '• '),
    use _markdown_to_html directly. Otherwise, split sentences into bullets.

    Input text should already be sanitized via _sanitize_html.
    """
    if not text or not text.strip():
        return "<p></p>"

    # Check if text already has bullet points
    has_bullets = any(
        line.strip().startswith("- ") or line.strip().startswith("&bull; ") or line.strip().startswith("• ")
        for line in text.split("\n")
        if line.strip()
    )

    if has_bullets or "\n" in text:
        return _markdown_to_html(text)

    # Split on '. ' followed by a capital letter, or on newlines
    # This regex splits on period-space-capital pattern
    sentences = re.split(r"(?<=\.)\s+(?=[A-Z])", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    if len(sentences) <= 1:
        # Single sentence - just apply inline formatting
        return f"<p>{_apply_inline_formatting(text)}</p>"

    # Multiple sentences -> bullet points
    items = "".join(f"<li>{_apply_inline_formatting(s)}</li>" for s in sentences)
    return f"<ul>{items}</ul>"


def _slug_from_link(link: str) -> str:
    """Generate a URL-safe slug from an announcement link."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", link)
    slug = slug.strip("-")
    if len(slug) > 80:
        slug = slug[:80].rstrip("-")
    return slug


def _tag_css_class(tag: str, tags: "AnnouncementTags") -> str:
    """Determine the CSS class for a tag based on which dimension it belongs to."""
    if tag in tags.services:
        return "tag-service"
    elif tag in tags.types:
        return "tag-type"
    elif tag in tags.concepts:
        return "tag-concept"
    elif tag in tags.use_cases:
        return "tag-usecase"
    elif tag in tags.providers:
        return "tag-provider"
    return "tag-concept"


class WebsiteBuilder:
    """Generates static HTML/CSS/JS website from announcement CSV data.

    Reads all announcements from CSV in S3 data bucket, generates static files
    using Python string templates, and returns them as a dict of path -> content.
    """

    def __init__(self, config: Config, s3_client, logger: StructuredLogger, data_bucket: str) -> None:
        self._config = config
        self._s3 = s3_client
        self._logger = logger
        self._data_bucket = data_bucket

    def load_announcements(self) -> list[ProcessedAnnouncement]:
        """Load all announcements from CSV in S3 data bucket."""
        try:
            response = self._s3.get_object(
                Bucket=self._data_bucket,
                Key="database/announcements.csv",
            )
            csv_content = response["Body"].read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(csv_content))
            announcements = []
            for row in reader:
                try:
                    announcements.append(ProcessedAnnouncement.from_csv_row(row))
                except (KeyError, ValueError) as exc:
                    self._logger.warning(
                        "Skipping malformed CSV row",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
            self._logger.info(
                "Loaded announcements from S3",
                count=len(announcements),
            )
            return announcements
        except Exception as exc:
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if error_code in ("NoSuchKey", "404"):
                self._logger.warning("No announcements CSV found in S3")
                return []
            self._logger.error(
                "Failed to load announcements from S3",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def build(self, announcements: list[ProcessedAnnouncement]) -> dict[str, str]:
        """Generate all static website files.

        Returns a dict mapping file paths (relative) to file content strings.
        """
        files: dict[str, str] = {}

        # Sort announcements by date (newest first)
        sorted_announcements = sorted(
            announcements,
            key=lambda a: _extract_date_sortable(a.pub_date),
            reverse=True,
        )

        # Generate shared assets
        files["assets/style.css"] = self._generate_css()
        files["assets/app.js"] = self._generate_js(sorted_announcements)

        # Generate index page
        files["index.html"] = self._generate_index(sorted_announcements)

        # Generate individual report pages
        for announcement in sorted_announcements:
            slug = _slug_from_link(announcement.link)
            files[f"reports/{slug}.html"] = self._generate_report_page(announcement)

        self._logger.info(
            "Website files generated",
            total_files=len(files),
            total_announcements=len(sorted_announcements),
        )
        return files

    def build_and_get_files(self) -> dict[str, str]:
        """Load announcements and build the website. Returns file dict."""
        announcements = self.load_announcements()
        return self.build(announcements)

    # -------------------------------------------------------------------------
    # CSS Generation
    # -------------------------------------------------------------------------

    def _generate_css(self) -> str:
        """Generate the shared CSS stylesheet with AWS-inspired branding."""
        return CSS_TEMPLATE

    # -------------------------------------------------------------------------
    # JavaScript Generation
    # -------------------------------------------------------------------------

    def _generate_js(self, announcements: list[ProcessedAnnouncement]) -> str:
        """Generate the shared JavaScript with filtering, timeline, and PDF."""
        announcements_data = []
        all_tags_set: set[str] = set()
        for a in announcements:
            tags_list = a.tags.all_tags()
            all_tags_set.update(tags_list)
            announcements_data.append({
                "title": a.title,
                "pub_date": a.pub_date,
                "link": a.link,
                "aws_service": a.aws_service,
                "importance_level": a.importance_level,
                "slug": _slug_from_link(a.link),
                "tags": tags_list,
            })

        # Compute tag counts per dimension for faceted filter chips
        tags_by_dimension: dict[str, dict[str, int]] = {
            "services": defaultdict(int),
            "types": defaultdict(int),
            "concepts": defaultdict(int),
            "use_cases": defaultdict(int),
            "providers": defaultdict(int),
            "geography": defaultdict(int),
        }
        for a in announcements:
            for tag in a.tags.services:
                tags_by_dimension["services"][tag] += 1
            for tag in a.tags.types:
                tags_by_dimension["types"][tag] += 1
            for tag in a.tags.concepts:
                tags_by_dimension["concepts"][tag] += 1
            for tag in a.tags.use_cases:
                tags_by_dimension["use_cases"][tag] += 1
            for tag in a.tags.providers:
                tags_by_dimension["providers"][tag] += 1
            # Geography from geo_relevance field
            if a.geo_relevance:
                for geo in a.geo_relevance.split(","):
                    geo = geo.strip()
                    if geo:
                        tags_by_dimension["geography"][geo] += 1

        # Convert defaultdicts to regular dicts for JSON serialization
        tags_by_dimension_serializable = {
            k: dict(v) for k, v in tags_by_dimension.items()
        }

        timeline_data = self._compute_timeline_data(announcements)

        js = JS_TEMPLATE.replace(
            "/*__ANNOUNCEMENTS_DATA__*/",
            json.dumps(announcements_data, ensure_ascii=False),
        )
        js = js.replace(
            "/*__TIMELINE_DATA__*/",
            json.dumps(timeline_data, ensure_ascii=False),
        )
        js = js.replace(
            "/*__ALL_TAGS__*/",
            json.dumps(sorted(all_tags_set), ensure_ascii=False),
        )
        js = js.replace(
            "/*__TAGS_BY_DIMENSION__*/",
            json.dumps(tags_by_dimension_serializable, ensure_ascii=False),
        )
        # Inject analytics API URL from environment
        analytics_url = os.environ.get("ANALYTICS_API_URL", "")
        js = js.replace("/*__ANALYTICS_URL__*/", analytics_url)
        return js

    def _compute_timeline_data(self, announcements: list[ProcessedAnnouncement]) -> dict:
        """Compute timeline data: count per day segmented by importance level."""
        day_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"star1": 0, "star2": 0, "star3": 0, "star4": 0, "star5": 0}
        )
        for a in announcements:
            date_str = _extract_date_sortable(a.pub_date)
            day_counts[date_str][f"star{a.importance_level}"] += 1

        sorted_dates = sorted(day_counts.keys())
        return {
            "labels": sorted_dates,
            "star1": [day_counts[d]["star1"] for d in sorted_dates],
            "star2": [day_counts[d]["star2"] for d in sorted_dates],
            "star3": [day_counts[d]["star3"] for d in sorted_dates],
            "star4": [day_counts[d]["star4"] for d in sorted_dates],
            "star5": [day_counts[d]["star5"] for d in sorted_dates],
        }

    # -------------------------------------------------------------------------
    # Index Page Generation
    # -------------------------------------------------------------------------

    def _generate_index(self, announcements: list[ProcessedAnnouncement]) -> str:
        """Generate the main index.html page."""
        cards_html = "\n".join(
            self._render_announcement_card(a) for a in announcements
        )

        return INDEX_TEMPLATE.replace("{{CARDS}}", cards_html)

    def _render_announcement_card(self, a: ProcessedAnnouncement) -> str:
        """Render a single announcement card for the index listing."""
        slug = _slug_from_link(a.link)
        stars = "\u2605" * a.importance_level + "\u2606" * (5 - a.importance_level)
        title_safe = _sanitize_html(a.title)
        date_sortable = _extract_date_sortable(a.pub_date)
        date_attr_safe = _sanitize_html(date_sortable)
        # Use YYYY-MM-DD to match the timeline graph format
        date_display = date_sortable
        summary_safe = _sanitize_html(a.report.card_summary if a.report.card_summary else a.report.whats_new[:200])

        # Build tag chips: prioritize Services first, then Types, then others
        # Services and Types always visible; fill remaining with concepts
        card_tags_ordered: list[tuple[str, str]] = []  # (tag, css_class)
        for tag in a.tags.services:
            card_tags_ordered.append((tag, "tag-service"))
        for tag in a.tags.types:
            card_tags_ordered.append((tag, "tag-type"))
        for tag in a.tags.concepts[:3]:  # max 3 concepts after services+types
            card_tags_ordered.append((tag, "tag-concept"))
        # Cap total at 6 to avoid overflow
        card_tags_ordered = card_tags_ordered[:6]

        tags_html = ""
        if card_tags_ordered:
            chips = []
            for tag, css_class in card_tags_ordered:
                tag_safe = _sanitize_html(tag)
                chips.append(f'<span class="tag {css_class}" data-tag="{tag_safe}">{tag_safe}</span>')
            tags_html = f'  <div class="card-tags">{"".join(chips)}</div>\n'

        # All tags for data attribute (for JS filtering)
        all_tags = a.tags.all_tags()
        all_tags_attr = _sanitize_html(",".join(all_tags)) if all_tags else ""

        # Geo relevance badges (bottom-right corner)
        geo_badge_html = ""
        if a.geo_relevance:
            geos = a.geo_relevance.split(",")
            badges = []
            for geo in geos:
                geo = geo.strip()
                if geo == "global":
                    badges.append('<span class="geo-badge geo-global">\U0001f310 Global</span>')
                elif geo == "apj":
                    badges.append('<span class="geo-badge geo-region">\U0001f30f APJ</span>')
                elif geo == "emea":
                    badges.append('<span class="geo-badge geo-region">\U0001f30d EMEA</span>')
                elif geo == "americas":
                    badges.append('<span class="geo-badge geo-region">\U0001f30e AMER</span>')
            if badges:
                geo_badge_html = '    ' + ' '.join(badges) + '\n'

        return (
            f'<article class="announcement-card" '
            f'data-date="{date_attr_safe}" '
            f'data-importance="{a.importance_level}" '
            f'data-tags="{all_tags_attr}" '
            f'data-geo="{a.geo_relevance}">\n'
            f'  <div class="card-header">\n'
            f'    <span class="card-stars importance-{a.importance_level}">{stars}</span>\n'
            f'    <span class="card-date">{date_display}</span>\n'
            f'  </div>\n'
            f'  <h3 class="card-title"><a href="reports/{slug}.html">{title_safe}</a></h3>\n'
            f'{tags_html}'
            f'  <p class="card-summary">{summary_safe}</p>\n'
            f'  <div class="card-footer">\n'
            f'    <a href="reports/{slug}.html" class="card-link">Full report &rarr;</a>\n'
            f'{geo_badge_html}'
            f'  </div>\n'
            f'</article>'
        )

    # -------------------------------------------------------------------------
    # Report Page Generation
    # -------------------------------------------------------------------------

    def _generate_report_page(self, a: ProcessedAnnouncement) -> str:
        """Generate an individual report page for an announcement."""
        stars = "\u2605" * a.importance_level + "\u2606" * (5 - a.importance_level)
        title_safe = _sanitize_html(a.title)
        service_safe = _sanitize_html(a.aws_service)
        date_display = _format_date_display(a.pub_date)
        link_safe = _sanitize_html(a.link)

        # Sanitize report text first, then convert to HTML
        whats_new_safe = _sanitize_html(a.report.whats_new)
        how_it_works_safe = _sanitize_html(a.report.how_it_works)
        why_important_safe = _sanitize_html(a.report.why_important)
        how_different_safe = _sanitize_html(a.report.how_different)
        when_to_prefer_safe = _sanitize_html(a.report.when_to_prefer)
        availability_safe = _sanitize_html(a.report.availability)

        # What's New stays as a paragraph; other sections get bullet formatting
        whats_new_html = f"<p>{_apply_inline_formatting(whats_new_safe)}</p>"
        how_it_works_html = _text_to_bullet_html(how_it_works_safe)
        why_important_html = _text_to_bullet_html(why_important_safe)
        how_different_html = _text_to_bullet_html(how_different_safe)
        when_to_prefer_html = _text_to_bullet_html(when_to_prefer_safe)
        availability_html = _text_to_bullet_html(availability_safe)

        mermaid_section = ""
        if a.mermaid_graph:
            mermaid_code_safe = _sanitize_html(a.mermaid_graph)
            mermaid_section = (
                '<section class="report-section mermaid-section">\n'
                '  <h2>Visual Summary</h2>\n'
                f'  <div class="mermaid">{mermaid_code_safe}</div>\n'
                '</section>'
            )

        # Tags section (all tags grouped by dimension)
        tags_section = ""
        if a.tags.all_tags():
            tags_parts = []
            tags_parts.append('<section class="report-section report-tags-section">\n')
            tags_parts.append('  <h2>Tags</h2>\n')
            tags_parts.append('  <div class="report-tags-grid">\n')
            if a.tags.services:
                tags_parts.append('    <div class="report-tag-group"><span class="tag-group-label">Services</span>')
                for t in a.tags.services:
                    tags_parts.append(f'<span class="tag tag-service">{_sanitize_html(t)}</span>')
                tags_parts.append('</div>\n')
            if a.tags.types:
                tags_parts.append('    <div class="report-tag-group"><span class="tag-group-label">Type</span>')
                for t in a.tags.types:
                    tags_parts.append(f'<span class="tag tag-type">{_sanitize_html(t)}</span>')
                tags_parts.append('</div>\n')
            if a.tags.concepts:
                tags_parts.append('    <div class="report-tag-group"><span class="tag-group-label">Concepts</span>')
                for t in a.tags.concepts:
                    tags_parts.append(f'<span class="tag tag-concept">{_sanitize_html(t)}</span>')
                tags_parts.append('</div>\n')
            if a.tags.use_cases:
                tags_parts.append('    <div class="report-tag-group"><span class="tag-group-label">Use Cases</span>')
                for t in a.tags.use_cases:
                    tags_parts.append(f'<span class="tag tag-usecase">{_sanitize_html(t)}</span>')
                tags_parts.append('</div>\n')
            if a.tags.providers:
                tags_parts.append('    <div class="report-tag-group"><span class="tag-group-label">Providers</span>')
                for t in a.tags.providers:
                    tags_parts.append(f'<span class="tag tag-provider">{_sanitize_html(t)}</span>')
                tags_parts.append('</div>\n')
            tags_parts.append('  </div>\n')
            tags_parts.append('</section>')
            tags_section = "".join(tags_parts)

        blogpost_links_html = ""
        if a.blogpost_links:
            links_items = "\n".join(
                f'<li><a href="{_sanitize_html(link)}" target="_blank" '
                f'rel="noopener noreferrer">{_sanitize_html(link)}</a></li>'
                for link in a.blogpost_links
            )
            blogpost_links_html = (
                '<section class="report-section">\n'
                '  <h2>Related Resources</h2>\n'
                f'  <ul class="blogpost-links">{links_items}</ul>\n'
                '</section>'
            )

        return (
            REPORT_TEMPLATE
            .replace("{{TITLE}}", title_safe)
            .replace("{{SERVICE}}", service_safe)
            .replace("{{DATE}}", date_display)
            .replace("{{STARS}}", stars)
            .replace("{{IMPORTANCE_LEVEL}}", str(a.importance_level))
            .replace("{{LINK}}", link_safe)
            .replace("{{WHATS_NEW}}", whats_new_html)
            .replace("{{HOW_IT_WORKS}}", how_it_works_html)
            .replace("{{WHY_IMPORTANT}}", why_important_html)
            .replace("{{HOW_DIFFERENT}}", how_different_html)
            .replace("{{WHEN_TO_PREFER}}", when_to_prefer_html)
            .replace("{{AVAILABILITY}}", availability_html)
            .replace("{{TAGS_SECTION}}", tags_section)
            .replace("{{MERMAID_SECTION}}", mermaid_section)
            .replace("{{BLOGPOST_LINKS}}", blogpost_links_html)
        )


# =============================================================================
# CSS Template - AWS-inspired color scheme with responsive design
# =============================================================================

CSS_TEMPLATE = """\
/* AI Radar AWS - Main Stylesheet */
/* AWS-inspired color scheme: orange accents on dark/light backgrounds */

:root {
  --aws-orange: #ff9900;
  --aws-orange-dark: #ec7211;
  --aws-dark: #232f3e;
  --aws-dark-secondary: #37475a;
  --aws-light: #f5f7fa;
  --aws-white: #ffffff;
  --aws-text: #16191f;
  --aws-text-secondary: #545b64;
  --aws-border: #d5dbdb;
  --aws-success: #1d8102;
  --aws-warning: #ff9900;
  --aws-error: #d13212;
  --star-1: #9e9e9e;
  --star-2: #2476F9;
  --star-3: #24F93D;
  --star-4: #f9a825;
  --star-5: #f924e1;
  --radius: 8px;
  --shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  --shadow-hover: 0 4px 16px rgba(0, 0, 0, 0.15);
  --transition: all 0.2s ease;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background: var(--aws-light);
  color: var(--aws-text);
  line-height: 1.6;
  min-height: 100vh;
}

/* Header */
.site-header {
  background: var(--aws-dark);
  color: var(--aws-white);
  padding: 1rem 2rem;
  position: sticky;
  top: 0;
  z-index: 1000;
  box-shadow: var(--shadow);
}

.header-content {
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.site-logo {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  text-decoration: none;
  color: var(--aws-white);
}

.logo-icon-img {
  height: 36px;
  width: auto;
  border-radius: 4px;
}

.site-logo h1 {
  font-size: 1.4rem;
  font-weight: 700;
  letter-spacing: -0.5px;
}

.site-logo h1 span {
  color: var(--aws-orange);
}

.logo-text {
  display: flex;
  flex-direction: column;
}

.tagline {
  font-size: 0.65rem;
  color: rgba(255, 255, 255, 0.6);
  font-weight: 400;
  letter-spacing: 0.3px;
  margin-top: -2px;
}

.about-tagline {
  font-size: 0.9rem;
  color: var(--aws-text-secondary);
  font-style: italic;
  margin-bottom: 1.5rem;
}

.header-nav a {
  color: var(--aws-white);
  text-decoration: none;
  margin-left: 1.5rem;
  font-size: 0.9rem;
  opacity: 0.85;
  transition: var(--transition);
}

.header-nav a:hover {
  opacity: 1;
  color: var(--aws-orange);
}

/* Main Content */
.main-content {
  max-width: 1400px;
  margin: 0 auto;
  padding: 2rem;
}

/* Filters Section */
.filters-section {
  background: var(--aws-white);
  border-radius: var(--radius);
  padding: 1.25rem 1.5rem;
  margin-bottom: 2rem;
  box-shadow: var(--shadow);
}

.filters-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.filters-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--aws-dark);
}

.filters-actions {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.sort-select {
  padding: 0.35rem 0.6rem;
  border: 1px solid var(--aws-border);
  border-radius: 4px;
  font-size: 0.8rem;
  background: var(--aws-white);
  color: var(--aws-text);
  cursor: pointer;
}

.filter-reset {
  padding: 0.35rem 0.75rem;
  background: var(--aws-dark-secondary);
  color: var(--aws-white);
  border: none;
  border-radius: 4px;
  font-size: 0.8rem;
  cursor: pointer;
  transition: var(--transition);
}

.filter-reset:hover {
  background: var(--aws-dark);
}

.filter-dimension {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.5rem 0;
  border-top: 1px solid var(--aws-light);
}

.filter-dimension:first-of-type {
  border-top: none;
}

.dimension-label {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--aws-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  min-width: 65px;
  padding-top: 0.3rem;
}

.dimension-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}

.filter-chip {
  font-size: 0.75rem;
  padding: 0.2rem 0.6rem;
  border-radius: 12px;
  border: 1px solid var(--aws-border);
  background: var(--aws-white);
  color: var(--aws-text-secondary);
  cursor: pointer;
  transition: var(--transition);
  white-space: nowrap;
}

.filter-chip:hover {
  border-color: var(--aws-orange);
  color: var(--aws-orange-dark);
}

.filter-chip.active {
  background: var(--aws-orange);
  color: var(--aws-white);
  border-color: var(--aws-orange);
}

.filter-chip .chip-count {
  font-size: 0.65rem;
  opacity: 0.7;
  margin-left: 0.2rem;
}

.filter-dimension-collapsed {
  border-top: 1px solid var(--aws-light);
  padding: 0.5rem 0;
}

.show-more-btn {
  font-size: 0.8rem;
  color: var(--aws-orange-dark);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0.25rem 0;
  font-weight: 500;
}

.show-more-btn:hover {
  color: var(--aws-orange);
}

.more-filters-content {
  width: 100%;
}

.filter-dimension-inner {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.5rem 0;
}

.active-filters {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.75rem 0 0.25rem;
  border-top: 1px solid var(--aws-light);
  margin-top: 0.25rem;
}

.active-filters-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}

.active-filter-chip {
  font-size: 0.75rem;
  padding: 0.2rem 0.5rem;
  border-radius: 12px;
  background: var(--aws-orange);
  color: var(--aws-white);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  transition: var(--transition);
}

.active-filter-chip:hover {
  background: var(--aws-orange-dark);
}

.active-filter-chip .remove-x {
  font-weight: bold;
  font-size: 0.85rem;
  line-height: 1;
}

/* Timeline Section */
.timeline-section {
  background: var(--aws-white);
  border-radius: var(--radius);
  padding: 1.5rem;
  margin-bottom: 2rem;
  box-shadow: var(--shadow);
}

.timeline-section h2 {
  font-size: 1.1rem;
  font-weight: 600;
  margin-bottom: 1rem;
  color: var(--aws-dark);
}

.timeline-chart-container {
  position: relative;
  height: 250px;
  width: 100%;
}

/* Announcement Cards */
.announcements-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
  gap: 1.5rem;
}

.announcement-card {
  background: var(--aws-white);
  border-radius: var(--radius);
  padding: 1.5rem;
  box-shadow: var(--shadow);
  transition: var(--transition);
  border-left: 4px solid var(--aws-border);
  display: flex;
  flex-direction: column;
}

.announcement-card:hover {
  box-shadow: var(--shadow-hover);
  transform: translateY(-2px);
}

.announcement-card[data-importance="5"] {
  border-left-color: var(--star-5);
}

.announcement-card[data-importance="4"] {
  border-left-color: var(--star-4);
}

.announcement-card[data-importance="3"] {
  border-left-color: var(--star-3);
}

.announcement-card[data-importance="2"] {
  border-left-color: var(--star-2);
}

.announcement-card[data-importance="1"] {
  border-left-color: var(--star-1);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.75rem;
}

.card-stars {
  font-size: 1rem;
}

.importance-5 { color: var(--star-5); }
.importance-4 { color: var(--star-4); }
.importance-3 { color: var(--star-3); }
.importance-2 { color: var(--star-2); }
.importance-1 { color: var(--star-1); }

.card-date {
  font-size: 0.8rem;
  color: var(--aws-text-secondary);
}

.card-title {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 0.5rem;
  line-height: 1.4;
}

.card-title a {
  color: var(--aws-text);
  text-decoration: none;
  transition: var(--transition);
}

.card-title a:hover {
  color: var(--aws-orange-dark);
}

.card-summary {
  font-size: 0.875rem;
  color: var(--aws-text-secondary);
  margin-bottom: 1rem;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-link {
  font-size: 0.85rem;
  color: var(--aws-orange-dark);
  text-decoration: none;
  font-weight: 500;
  transition: var(--transition);
}

.card-link:hover {
  color: var(--aws-orange);
}

/* Card Footer (link + geo badge) */
.card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: auto;
  padding-top: 0.75rem;
}

/* Geo Relevance Badge */
.geo-badge {
  font-size: 0.7rem;
  padding: 0.2rem 0.5rem;
  border-radius: 10px;
  font-weight: 500;
  white-space: nowrap;
}

.geo-local, .geo-region {
  background: #e8f5e9;
  color: #2e7d32;
  border: 1px solid #a5d6a7;
}

.geo-global {
  background: #e3f2fd;
  color: #1565c0;
  border: 1px solid #90caf9;
}

/* Tag Chips */
.card-tags { display: flex; flex-wrap: wrap; gap: 0.25rem; margin: 0.5rem 0; }
.tag { font-size: 0.7rem; padding: 0.15rem 0.5rem; border-radius: 12px; font-weight: 500; cursor: pointer; transition: var(--transition); }
.tag:hover { opacity: 0.8; }
.tag-service { background: #e3f2fd; color: #1565c0; }
.tag-type { background: #f3e5f5; color: #7b1fa2; }
.tag-concept { background: #e8f5e9; color: #2e7d32; }
.tag-usecase { background: #fff3e0; color: #e65100; }
.tag-provider { background: #fce4ec; color: #c62828; }

/* Tag Filter - kept for card tag chips */

/* Report Tags Section */
.report-tags-section .report-tags-grid { display: flex; flex-direction: column; gap: 0.75rem; }
.report-tag-group { display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem; }
.tag-group-label { font-size: 0.75rem; font-weight: 600; color: var(--aws-text-secondary); text-transform: uppercase; letter-spacing: 0.5px; min-width: 70px; }

/* Report Page */
.report-container {
  max-width: 900px;
  margin: 0 auto;
  padding: 2rem;
}

.report-header {
  background: var(--aws-white);
  border-radius: var(--radius);
  padding: 2rem;
  margin-bottom: 2rem;
  box-shadow: var(--shadow);
  border-top: 4px solid var(--aws-orange);
}

.report-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  align-items: center;
  margin-bottom: 1rem;
}

.report-meta .stars {
  font-size: 1.2rem;
}

.report-meta .date {
  font-size: 0.9rem;
  color: var(--aws-text-secondary);
}

.report-meta .service {
  font-size: 0.85rem;
  background: var(--aws-light);
  padding: 0.25rem 0.75rem;
  border-radius: 12px;
  color: var(--aws-orange-dark);
  font-weight: 500;
}

.report-title {
  font-size: 1.75rem;
  font-weight: 700;
  line-height: 1.3;
  margin-bottom: 1rem;
  color: var(--aws-dark);
}

.report-source-link {
  font-size: 0.85rem;
  color: var(--aws-orange-dark);
  text-decoration: none;
}

.report-source-link:hover {
  text-decoration: underline;
}

.report-actions {
  display: flex;
  gap: 0.75rem;
  margin-top: 1rem;
}

.btn-pdf {
  padding: 0.5rem 1rem;
  background: var(--aws-orange);
  color: var(--aws-white);
  border: none;
  border-radius: 4px;
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
  transition: var(--transition);
}

.btn-pdf:hover {
  background: var(--aws-orange-dark);
}

.report-section {
  background: var(--aws-white);
  border-radius: var(--radius);
  padding: 1.5rem 2rem;
  margin-bottom: 1rem;
  box-shadow: var(--shadow);
}

.report-section h2 {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--aws-dark);
  margin-bottom: 0.75rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--aws-light);
}

.report-section p {
  font-size: 0.95rem;
  line-height: 1.7;
  color: var(--aws-text);
}

.mermaid-section .mermaid {
  background: var(--aws-light);
  padding: 1.5rem;
  border-radius: 4px;
  overflow-x: auto;
}

.blogpost-links {
  list-style: none;
  padding: 0;
}

.blogpost-links li {
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--aws-light);
}

.blogpost-links li:last-child {
  border-bottom: none;
}

.blogpost-links a {
  color: var(--aws-orange-dark);
  text-decoration: none;
  font-size: 0.9rem;
  word-break: break-all;
}

.blogpost-links a:hover {
  text-decoration: underline;
}

.back-link {
  display: inline-block;
  margin-bottom: 1.5rem;
  color: var(--aws-orange-dark);
  text-decoration: none;
  font-size: 0.9rem;
  font-weight: 500;
}

.back-link:hover {
  color: var(--aws-orange);
}

/* Footer */
.site-footer {
  background: var(--aws-dark);
  color: var(--aws-white);
  text-align: center;
  padding: 1.5rem;
  margin-top: 3rem;
  font-size: 0.85rem;
  opacity: 0.8;
}

/* No results */
.no-results {
  text-align: center;
  padding: 3rem;
  color: var(--aws-text-secondary);
  font-size: 1rem;
  display: none;
}

/* Report section lists (I1) */
.report-section ul {
  list-style: none;
  padding: 0;
  margin: 0.5rem 0;
}

.report-section ul li {
  position: relative;
  padding: 0.4rem 0 0.4rem 1.5rem;
  font-size: 0.95rem;
  line-height: 1.7;
  color: var(--aws-text);
}

.report-section ul li::before {
  content: "\\25B8";
  position: absolute;
  left: 0;
  color: var(--aws-orange);
  font-size: 0.85rem;
  top: 0.5rem;
}

.report-section ul li + li {
  border-top: 1px solid var(--aws-light);
}

/* About Modal (I2) */
.about-modal-overlay {
  display: none;
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.6);
  z-index: 2000;
  align-items: center;
  justify-content: center;
}

.about-modal-overlay.active {
  display: flex;
}

.about-modal {
  background: var(--aws-white);
  border-radius: var(--radius);
  max-width: 640px;
  width: 90%;
  max-height: 85vh;
  overflow-y: auto;
  padding: 2rem;
  position: relative;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.about-modal-close {
  position: absolute;
  top: 1rem;
  right: 1rem;
  background: none;
  border: none;
  font-size: 1.5rem;
  cursor: pointer;
  color: var(--aws-text-secondary);
  line-height: 1;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  transition: var(--transition);
}

.about-modal-close:hover {
  background: var(--aws-light);
  color: var(--aws-text);
}

.about-header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1.5rem;
}

.about-logo {
  height: 80px;
  width: auto;
  border-radius: 8px;
}

.about-modal h2 {
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--aws-dark);
  margin-bottom: 0;
}

.about-modal h2 span {
  color: var(--aws-orange);
}

.about-modal h3 {
  font-size: 1rem;
  font-weight: 600;
  color: var(--aws-dark);
  margin-top: 1.5rem;
  margin-bottom: 0.5rem;
}

.about-modal p {
  font-size: 0.95rem;
  line-height: 1.7;
  color: var(--aws-text);
  margin-bottom: 1rem;
}

.about-modal ol {
  padding-left: 1.5rem;
  margin-bottom: 1rem;
}

.about-modal ol li {
  font-size: 0.9rem;
  line-height: 1.8;
  color: var(--aws-text);
  padding: 0.2rem 0;
}

.about-modal ul {
  padding-left: 1.5rem;
  margin-bottom: 1rem;
}

.about-modal ul li {
  font-size: 0.9rem;
  line-height: 1.7;
  color: var(--aws-text);
  padding: 0.15rem 0;
}

.about-tabs {
  display: flex;
  border-bottom: 2px solid var(--aws-border);
  margin-bottom: 1.25rem;
  gap: 0;
}

.about-tab {
  padding: 0.6rem 1.2rem;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--aws-text-secondary);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.about-tab:hover { color: var(--aws-orange-dark); }
.about-tab.active { color: var(--aws-orange); border-bottom-color: var(--aws-orange); }

.about-tab-content { display: none; }
.about-tab-content.active { display: block; }

.scoring-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
  margin: 0.75rem 0;
}

.scoring-table th, .scoring-table td {
  padding: 0.5rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid var(--aws-border);
}

.scoring-table th { background: var(--aws-light); font-weight: 600; color: var(--aws-dark); }
.scoring-table .pts-pos { color: #2e7d32; font-weight: 600; }
.scoring-table .pts-neg { color: #d13212; font-weight: 600; }

.geo-legend {
  display: flex;
  gap: 1.5rem;
  margin: 0.75rem 0;
  flex-wrap: wrap;
}

.geo-legend-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85rem;
}

.star-scale {
  display: flex;
  gap: 0.4rem;
  margin: 0.75rem 0;
  flex-wrap: wrap;
}

.star-scale-item {
  font-size: 0.8rem;
  padding: 0.3rem 0.6rem;
  border-radius: 4px;
  background: var(--aws-light);
}

.about-modal .highlight-box {
  background: var(--aws-light);
  border-left: 3px solid var(--aws-orange);
  padding: 0.75rem 1rem;
  border-radius: 0 4px 4px 0;
  margin: 1rem 0;
  font-size: 0.9rem;
  color: var(--aws-text-secondary);
}

/* Responsive Design */
@media (max-width: 1024px) {
  .announcements-grid {
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  }
}

@media (max-width: 768px) {
  .site-header {
    padding: 0.75rem 1rem;
  }

  .header-content {
    flex-direction: column;
    gap: 0.5rem;
  }

  .header-nav a {
    margin-left: 1rem;
    font-size: 0.8rem;
  }

  .main-content {
    padding: 1rem;
  }

  .filters-section {
    padding: 1rem;
  }

  .filter-dimension {
    flex-direction: column;
    gap: 0.25rem;
  }

  .announcements-grid {
    grid-template-columns: 1fr;
  }

  .timeline-chart-container {
    height: 200px;
  }

  .report-container {
    padding: 1rem;
  }

  .report-header {
    padding: 1.5rem;
  }

  .report-title {
    font-size: 1.3rem;
  }

  .report-section {
    padding: 1rem 1.25rem;
  }
}

@media (max-width: 480px) {
  .site-logo h1 {
    font-size: 1.1rem;
  }

  .report-meta {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.5rem;
  }

  .report-actions {
    flex-direction: column;
  }
}
"""

# =============================================================================
# JavaScript Template - Client-side filtering, timeline, and PDF export
# =============================================================================

JS_TEMPLATE = """\
/* AI Radar AWS - Client-side Application Logic */
(function() {
  'use strict';

  // Announcement data injected at build time
  var announcements = /*__ANNOUNCEMENTS_DATA__*/;
  var timelineData = /*__TIMELINE_DATA__*/;
  var allTags = /*__ALL_TAGS__*/;
  var tagsByDimension = /*__TAGS_BY_DIMENSION__*/;

  // Filter state
  var filters = {
    timePeriod: 'all',
    sort: 'newest',
    selectedTags: {
      services: [],
      types: [],
      concepts: [],
      use_cases: [],
      providers: [],
      geography: []
    }
  };

  // DOM references
  var cardsContainer = document.getElementById('announcements-grid');
  var noResults = document.getElementById('no-results');
  var sortSelect = document.getElementById('sort-select');
  var resetBtn = document.getElementById('filter-reset');
  var showMoreBtn = document.getElementById('show-more-filters');
  var moreFiltersContent = document.getElementById('more-filters-content');
  var activeFiltersSection = document.getElementById('active-filters');
  var activeFiltersChips = document.getElementById('active-filters-chips');

  // Initialize
  buildFilterChips();
  updateTimeFilterCounts();
  initFilters();
  initTimeline();
  initCardTagClicks();
  applyFilters(); // Sort cards by date on initial load

  // Fallback: if Chart.js was not ready, retry on window load
  window.addEventListener('load', function() {
    var ctx = document.getElementById('timeline-chart');
    if (ctx && !ctx._chartInitialized) {
      initTimeline();
    }
  });

  function buildFilterChips() {
    var dimensions = [
      { key: 'services', containerId: 'service-chips' },
      { key: 'types', containerId: 'type-chips' },
      { key: 'concepts', containerId: 'concept-chips' },
      { key: 'use_cases', containerId: 'usecase-chips' },
      { key: 'providers', containerId: 'provider-chips' }
    ];

    dimensions.forEach(function(dim) {
      var container = document.getElementById(dim.containerId);
      if (!container) return;
      var tags = tagsByDimension[dim.key] || {};
      // Sort by count descending
      var sorted = Object.keys(tags).sort(function(a, b) {
        return tags[b] - tags[a];
      });
      container.innerHTML = sorted.map(function(tag) {
        return '<button class="filter-chip" data-dimension="' + dim.key + '" data-tag="' + tag + '">' +
          tag + ' <span class="chip-count">(' + tags[tag] + ')</span></button>';
      }).join('');

      // Hide the dimension row if no tags
      if (sorted.length === 0) {
        var row = container.closest('.filter-dimension') || container.closest('.filter-dimension-inner');
        if (row) row.style.display = 'none';
      }
    });
  }

  function updateTimeFilterCounts() {
    var timeRow = document.getElementById('filter-time-row');
    if (!timeRow) return;
    var cards = document.querySelectorAll('.announcement-card');
    var now = new Date();
    var weekCutoff = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    var monthCutoff = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    var threeMonthCutoff = new Date(now.getTime() - 90 * 24 * 60 * 60 * 1000);

    var total = cards.length;
    var weekCount = 0, monthCount = 0, threeMonthCount = 0;

    cards.forEach(function(card) {
      var dateStr = card.getAttribute('data-date');
      if (dateStr) {
        var d = new Date(dateStr + 'T00:00:00Z');
        if (d >= weekCutoff) weekCount++;
        if (d >= monthCutoff) monthCount++;
        if (d >= threeMonthCutoff) threeMonthCount++;
      }
    });

    var buttons = timeRow.querySelectorAll('.filter-chip[data-time]');
    buttons.forEach(function(btn) {
      var time = btn.getAttribute('data-time');
      var count = total;
      if (time === 'week') count = weekCount;
      else if (time === 'month') count = monthCount;
      else if (time === '3months') count = threeMonthCount;
      var label = btn.textContent.replace(/\s*\(\d+\)/, '');
      btn.textContent = label + ' (' + count + ')';
    });
  }

  function initFilters() {
    // Time chips
    var timeRow = document.getElementById('filter-time-row');
    if (timeRow) {
      timeRow.addEventListener('click', function(e) {
        var chip = e.target.closest('.filter-chip[data-time]');
        if (!chip) return;
        // Deactivate all time chips, activate clicked one
        timeRow.querySelectorAll('.filter-chip').forEach(function(c) { c.classList.remove('active'); });
        chip.classList.add('active');
        filters.timePeriod = chip.getAttribute('data-time');
        applyFilters();
        updateTimeline();
      });
    }

    // Sort select
    if (sortSelect) {
      sortSelect.addEventListener('change', function() {
        filters.sort = this.value;
        applyFilters();
      });
    }

    // Tag dimension chips (services, types, concepts, use_cases, providers)
    var dimensionContainers = ['service-chips', 'type-chips', 'concept-chips', 'usecase-chips', 'provider-chips'];
    dimensionContainers.forEach(function(id) {
      var container = document.getElementById(id);
      if (!container) return;
      container.addEventListener('click', function(e) {
        var chip = e.target.closest('.filter-chip');
        if (!chip) return;
        var dimension = chip.getAttribute('data-dimension');
        var tag = chip.getAttribute('data-tag');
        toggleTagFilter(dimension, tag, chip);
      });
    });

    // Reset button
    if (resetBtn) {
      resetBtn.addEventListener('click', resetAllFilters);
    }

    // Show more button
    if (showMoreBtn && moreFiltersContent) {
      showMoreBtn.addEventListener('click', function() {
        var isHidden = moreFiltersContent.style.display === 'none';
        moreFiltersContent.style.display = isHidden ? 'block' : 'none';
        showMoreBtn.textContent = isHidden ? 'Less filters...' : 'More filters...';
      });
    }
  }

  function toggleTagFilter(dimension, tag, chipEl) {
    var arr = filters.selectedTags[dimension];
    var idx = arr.indexOf(tag);
    if (idx === -1) {
      arr.push(tag);
      if (chipEl) chipEl.classList.add('active');
    } else {
      arr.splice(idx, 1);
      if (chipEl) chipEl.classList.remove('active');
    }
    renderActiveFilters();
    applyFilters();
  }

  function resetAllFilters() {
    filters.timePeriod = 'all';
    filters.sort = 'newest';
    filters.selectedTags = { services: [], types: [], concepts: [], use_cases: [], providers: [], geography: [] };

    // Reset time chips
    var timeRow = document.getElementById('filter-time-row');
    if (timeRow) {
      timeRow.querySelectorAll('.filter-chip').forEach(function(c) { c.classList.remove('active'); });
      var allChip = timeRow.querySelector('[data-time="all"]');
      if (allChip) allChip.classList.add('active');
    }

    // Reset sort
    if (sortSelect) sortSelect.value = 'newest';

    // Reset all dimension chips
    document.querySelectorAll('.filter-chip[data-dimension]').forEach(function(c) {
      c.classList.remove('active');
    });

    renderActiveFilters();
    applyFilters();
    updateTimeline();
  }

  function renderActiveFilters() {
    var allActive = [];
    Object.keys(filters.selectedTags).forEach(function(dim) {
      filters.selectedTags[dim].forEach(function(tag) {
        allActive.push({ dimension: dim, tag: tag });
      });
    });

    if (activeFiltersSection) {
      activeFiltersSection.style.display = allActive.length > 0 ? 'flex' : 'none';
    }
    if (activeFiltersChips) {
      activeFiltersChips.innerHTML = allActive.map(function(item) {
        return '<span class="active-filter-chip" data-dimension="' + item.dimension + '" data-tag="' + item.tag + '">' +
          item.tag + ' <span class="remove-x">&times;</span></span>';
      }).join('');

      activeFiltersChips.querySelectorAll('.active-filter-chip').forEach(function(el) {
        el.addEventListener('click', function() {
          var dim = this.getAttribute('data-dimension');
          var tag = this.getAttribute('data-tag');
          // Remove from state
          var arr = filters.selectedTags[dim];
          var idx = arr.indexOf(tag);
          if (idx !== -1) arr.splice(idx, 1);
          // Deactivate the chip button
          var chipBtn = document.querySelector('.filter-chip[data-dimension="' + dim + '"][data-tag="' + tag + '"]');
          if (chipBtn) chipBtn.classList.remove('active');
          renderActiveFilters();
          applyFilters();
        });
      });
    }
  }

  function initCardTagClicks() {
    if (!cardsContainer) return;
    cardsContainer.addEventListener('click', function(e) {
      var tagEl = e.target.closest('.tag[data-tag]');
      if (!tagEl) return;
      e.preventDefault();
      var tag = tagEl.getAttribute('data-tag');
      // Determine which dimension this tag belongs to
      var dimension = findTagDimension(tag);
      if (dimension) {
        // Activate the chip in the filter bar
        var chipBtn = document.querySelector('.filter-chip[data-dimension="' + dimension + '"][data-tag="' + tag + '"]');
        if (filters.selectedTags[dimension].indexOf(tag) === -1) {
          filters.selectedTags[dimension].push(tag);
          if (chipBtn) chipBtn.classList.add('active');
          renderActiveFilters();
          applyFilters();
        }
      }
    });
  }

  function findTagDimension(tag) {
    var dims = ['services', 'types', 'concepts', 'use_cases', 'providers', 'geography'];
    for (var i = 0; i < dims.length; i++) {
      if (tagsByDimension[dims[i]] && tagsByDimension[dims[i]][tag] !== undefined) {
        return dims[i];
      }
    }
    return null;
  }

  function applyFilters() {
    if (!cardsContainer) return;
    var cards = cardsContainer.querySelectorAll('.announcement-card');
    var now = new Date();
    now.setHours(0, 0, 0, 0);
    var visibleCount = 0;

    // Determine date threshold (midnight-aligned for consistent day boundaries)
    var dateThreshold = null;
    if (filters.timePeriod === 'week') {
      dateThreshold = new Date(now.getTime() - 6 * 24 * 60 * 60 * 1000);
    } else if (filters.timePeriod === 'month') {
      dateThreshold = new Date(now.getTime() - 29 * 24 * 60 * 60 * 1000);
    } else if (filters.timePeriod === '3months') {
      dateThreshold = new Date(now.getTime() - 89 * 24 * 60 * 60 * 1000);
    }

    var cardArray = Array.prototype.slice.call(cards);

    cardArray.forEach(function(card) {
      var cardDate = card.getAttribute('data-date');
      var cardTags = (card.getAttribute('data-tags') || '').split(',').filter(Boolean);
      var visible = true;

      // Time period filter
      if (dateThreshold && cardDate) {
        var cardDateObj = new Date(cardDate + 'T00:00:00');
        if (cardDateObj < dateThreshold) {
          visible = false;
        }
      }

      // Tag filters: OR within dimension, AND across dimensions
      if (visible) {
        var dims = ['services', 'types', 'concepts', 'use_cases', 'providers'];
        for (var i = 0; i < dims.length; i++) {
          var selected = filters.selectedTags[dims[i]];
          if (selected.length > 0) {
            // Card must have at least one of the selected tags in this dimension (OR)
            var hasAny = false;
            for (var j = 0; j < selected.length; j++) {
              if (cardTags.indexOf(selected[j]) !== -1) {
                hasAny = true;
                break;
              }
            }
            if (!hasAny) {
              visible = false;
              break;
            }
          }
        }
      }

      // Geography filter: OR logic (show if card matches any selected geo)
      if (visible && filters.selectedTags.geography && filters.selectedTags.geography.length > 0) {
        var cardGeo = (card.getAttribute('data-geo') || '').split(',');
        var geoMatch = false;
        for (var g = 0; g < filters.selectedTags.geography.length; g++) {
          if (cardGeo.indexOf(filters.selectedTags.geography[g]) !== -1) {
            geoMatch = true;
            break;
          }
        }
        if (!geoMatch) visible = false;
      }

      card.style.display = visible ? '' : 'none';
      if (visible) visibleCount++;
    });

    // Sort visible cards
    if (filters.sort === 'importance') {
      var visibleCards = cardArray.filter(function(c) { return c.style.display !== 'none'; });
      visibleCards.sort(function(a, b) {
        var impA = parseInt(a.getAttribute('data-importance'), 10);
        var impB = parseInt(b.getAttribute('data-importance'), 10);
        return impB - impA;
      });
      visibleCards.forEach(function(card) {
        cardsContainer.appendChild(card);
      });
    } else {
      // Newest first (restore original order by date)
      var visibleCards = cardArray.filter(function(c) { return c.style.display !== 'none'; });
      visibleCards.sort(function(a, b) {
        var dateA = a.getAttribute('data-date') || '';
        var dateB = b.getAttribute('data-date') || '';
        return dateB.localeCompare(dateA);
      });
      visibleCards.forEach(function(card) {
        cardsContainer.appendChild(card);
      });
    }

    // Show/hide no results message
    if (noResults) {
      noResults.style.display = visibleCount === 0 ? 'block' : 'none';
    }
  }

  // Timeline Chart (Chart.js)
  var timelineChart = null;

  function initTimeline() {
    var ctx = document.getElementById('timeline-chart');
    if (!ctx || !window.Chart || !timelineData.labels) return;
    ctx._chartInitialized = true;
    // Use updateTimeline for consistent gap-filling logic from the start
    updateTimeline();
  }

  function updateTimeline() {
    if (!window.Chart || !cardsContainer) return;

    // Build timeline from VISIBLE cards only (matches what user sees after filtering)
    var dayCounts = {};
    var visibleCards = cardsContainer.querySelectorAll('.announcement-card');
    visibleCards.forEach(function(card) {
      if (card.style.display === 'none') return;
      var dateStr = card.getAttribute('data-date') || '';
      var importance = parseInt(card.getAttribute('data-importance'), 10) || 1;
      if (!dateStr) return;
      if (!dayCounts[dateStr]) dayCounts[dateStr] = {s1:0, s2:0, s3:0, s4:0, s5:0};
      dayCounts[dateStr]['s' + importance]++;
    });

    // Generate complete date range based on time filter (fill gaps with zeros)
    var labels, s1, s2, s3, s4, s5;
    var today = new Date();
    today.setHours(0, 0, 0, 0);

    if (filters.timePeriod === 'week' || filters.timePeriod === 'month' || filters.timePeriod === '3months') {
      var days;
      if (filters.timePeriod === 'week') days = 7;
      else if (filters.timePeriod === 'month') days = 30;
      else days = 90;
      var fullRange = generateDailyRange(today, days);
      labels = fullRange;
      s1 = fullRange.map(function(d) { return dayCounts[d] ? dayCounts[d].s1 : 0; });
      s2 = fullRange.map(function(d) { return dayCounts[d] ? dayCounts[d].s2 : 0; });
      s3 = fullRange.map(function(d) { return dayCounts[d] ? dayCounts[d].s3 : 0; });
      s4 = fullRange.map(function(d) { return dayCounts[d] ? dayCounts[d].s4 : 0; });
      s5 = fullRange.map(function(d) { return dayCounts[d] ? dayCounts[d].s5 : 0; });
    } else {
      // "All" — weekly aggregation with gap filling
      var sortedDates = Object.keys(dayCounts).sort();
      if (sortedDates.length > 0) {
        var firstDate = new Date(sortedDates[0] + 'T00:00:00');
        var totalDays = Math.ceil((today - firstDate) / (1000 * 60 * 60 * 24)) + 1;
        var fullRange = generateDailyRange(today, totalDays);
        var weekData = aggregateByWeekFromRange(dayCounts, fullRange);
        labels = weekData.labels;
        s1 = weekData.s1;
        s2 = weekData.s2;
        s3 = weekData.s3;
        s4 = weekData.s4;
        s5 = weekData.s5;
      } else {
        labels = []; s1 = []; s2 = []; s3 = []; s4 = []; s5 = [];
      }
    }

    renderTimeline(labels, s1, s2, s3, s4, s5);
  }

  function generateDailyRange(endDate, numDays) {
    // Generate array of YYYY-MM-DD strings for the last numDays ending at endDate
    // Uses local date (not UTC) to match the user's timezone
    var range = [];
    for (var i = numDays - 1; i >= 0; i--) {
      var d = new Date(endDate);
      d.setDate(d.getDate() - i);
      var yyyy = d.getFullYear();
      var mm = String(d.getMonth() + 1).padStart(2, '0');
      var dd = String(d.getDate()).padStart(2, '0');
      range.push(yyyy + '-' + mm + '-' + dd);
    }
    return range;
  }

  function aggregateByWeekFromRange(dayCounts, dailyRange) {
    // Group a complete daily range into ISO weeks, filling gaps with zeros
    var weeks = {};
    dailyRange.forEach(function(dateStr) {
      var d = new Date(dateStr + 'T00:00:00Z');
      var day = d.getUTCDay();
      var diff = d.getUTCDate() - day + (day === 0 ? -6 : 1);
      var monday = new Date(d);
      monday.setUTCDate(diff);
      var weekLabel = monday.toISOString().slice(0, 10);

      if (!weeks[weekLabel]) weeks[weekLabel] = {s1:0, s2:0, s3:0, s4:0, s5:0};
      var dc = dayCounts[dateStr];
      if (dc) {
        weeks[weekLabel].s1 += dc.s1;
        weeks[weekLabel].s2 += dc.s2;
        weeks[weekLabel].s3 += dc.s3;
        weeks[weekLabel].s4 += dc.s4;
        weeks[weekLabel].s5 += dc.s5;
      }
    });

    var weekLabels = Object.keys(weeks).sort();
    return {
      labels: weekLabels.map(function(w) { return 'W/' + w; }),
      s1: weekLabels.map(function(w) { return weeks[w].s1; }),
      s2: weekLabels.map(function(w) { return weeks[w].s2; }),
      s3: weekLabels.map(function(w) { return weeks[w].s3; }),
      s4: weekLabels.map(function(w) { return weeks[w].s4; }),
      s5: weekLabels.map(function(w) { return weeks[w].s5; })
    };
  }

  function renderTimeline(labels, s1, s2, s3, s4, s5) {
    var ctx = document.getElementById('timeline-chart');
    if (!ctx) return;

    // Get existing chart instance from canvas (Chart.js stores it)
    var existingChart = Chart.getChart(ctx);
    if (existingChart) {
      existingChart.data.labels = labels;
      existingChart.data.datasets[0].data = s5;
      existingChart.data.datasets[1].data = s4;
      existingChart.data.datasets[2].data = s3;
      existingChart.data.datasets[3].data = s2;
      existingChart.data.datasets[4].data = s1;
      existingChart.update();
      return;
    }

    timelineChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [
          {
            label: '5-Star (Critical)',
            data: s5,
            backgroundColor: '#f924e1',
            borderRadius: 2
          },
          {
            label: '4-Star (Important)',
            data: s4,
            backgroundColor: '#f9a825',
            borderRadius: 2
          },
          {
            label: '3-Star (Notable)',
            data: s3,
            backgroundColor: '#24F93D',
            borderRadius: 2
          },
          {
            label: '2-Star (Standard)',
            data: s2,
            backgroundColor: '#2476F9',
            borderRadius: 2
          },
          {
            label: '1-Star (Peripheral)',
            data: s1,
            backgroundColor: '#9e9e9e',
            borderRadius: 2
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'top',
            labels: { font: { size: 11 } }
          },
          tooltip: {
            mode: 'index',
            intersect: false
          }
        },
        scales: {
          x: {
            stacked: true,
            grid: { display: false },
            ticks: { font: { size: 10 }, maxRotation: 45 }
          },
          y: {
            stacked: true,
            beginAtZero: true,
            ticks: { stepSize: 1, font: { size: 11 } }
          }
        }
      }
    });
  }

  // PDF Export (html2pdf.js)
  window.exportPDF = function() {
    var element = document.getElementById('report-content');
    if (!element || !window.html2pdf) return;

    var title = document.querySelector('.report-title');
    var filename = title ? title.textContent.substring(0, 50).replace(/[^a-zA-Z0-9]/g, '_') : 'report';

    var opt = {
      margin: [10, 10, 10, 10],
      filename: filename + '.pdf',
      image: { type: 'jpeg', quality: 0.95 },
      html2canvas: { scale: 2, useCORS: true },
      jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
    };

    html2pdf().set(opt).from(element).save();
  };

  // About Modal
  window.openAboutModal = function() {
    var overlay = document.getElementById('about-modal-overlay');
    if (overlay) overlay.classList.add('active');
  };

  window.closeAboutModal = function() {
    var overlay = document.getElementById('about-modal-overlay');
    if (overlay) overlay.classList.remove('active');
  };

  window.switchAboutTab = function(tabId) {
    var tabs = document.querySelectorAll('.about-tab');
    var contents = document.querySelectorAll('.about-tab-content');
    tabs.forEach(function(t) { t.classList.remove('active'); });
    contents.forEach(function(c) { c.classList.remove('active'); });
    var activeTab = document.querySelector('.about-tab[onclick*="' + tabId + '"]');
    var activeContent = document.getElementById('about-tab-' + tabId);
    if (activeTab) activeTab.classList.add('active');
    if (activeContent) activeContent.classList.add('active');
  };

  // Close modal on overlay click
  var aboutOverlay = document.getElementById('about-modal-overlay');
  if (aboutOverlay) {
    aboutOverlay.addEventListener('click', function(e) {
      if (e.target === aboutOverlay) {
        window.closeAboutModal();
      }
    });
  }

  // Close modal on Escape key
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      window.closeAboutModal();
    }
  });

  // ─── Analytics Tracking ──────────────────────────────────────────────
  (function() {
    var ANALYTICS_URL = '/*__ANALYTICS_URL__*/';
    if (!ANALYTICS_URL || ANALYTICS_URL.indexOf('__') !== -1) return;

    var sessionId = sessionStorage.getItem('_ar_sid');
    if (!sessionId) {
      sessionId = 'sid_' + Math.random().toString(36).substr(2, 12);
      sessionStorage.setItem('_ar_sid', sessionId);
    }

    var eventQueue = [];

    function track(eventType, data) {
      var evt = {
        event_type: eventType,
        path: window.location.pathname,
        session_id: sessionId,
        timestamp: new Date().toISOString()
      };
      if (data) {
        for (var k in data) { evt[k] = data[k]; }
      }
      eventQueue.push(evt);
    }

    function flush() {
      if (eventQueue.length === 0) return;
      var batch = eventQueue.splice(0, eventQueue.length);
      var payload = JSON.stringify({ events: batch });
      if (navigator.sendBeacon) {
        navigator.sendBeacon(ANALYTICS_URL + '/events', payload);
      } else {
        var xhr = new XMLHttpRequest();
        xhr.open('POST', ANALYTICS_URL + '/events', true);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.send(payload);
      }
    }

    // Track pageview
    track('pageview');

    // Flush every 10 seconds
    setInterval(flush, 10000);

    // Flush on page unload
    window.addEventListener('beforeunload', flush);
    document.addEventListener('visibilitychange', function() {
      if (document.visibilityState === 'hidden') flush();
    });

    // Track report clicks (from index cards)
    var grid = document.getElementById('announcements-grid');
    if (grid) {
      grid.addEventListener('click', function(e) {
        var link = e.target.closest('.card-link, .card-title a');
        if (link) {
          var href = link.getAttribute('href') || '';
          var slug = href.replace('reports/', '').replace('.html', '');
          track('report_click', { report_slug: slug });
        }
      });
    }

    // Track filter usage (tag clicks in filter bar)
    var filtersSection = document.getElementById('filters');
    if (filtersSection) {
      filtersSection.addEventListener('click', function(e) {
        var chip = e.target.closest('.filter-chip[data-dimension]');
        if (chip) {
          track('filter_tag', {
            dimension: chip.getAttribute('data-dimension'),
            tag: chip.getAttribute('data-tag')
          });
        }
      });
    }

    // Track PDF export
    var origExportPDF = window.exportPDF;
    window.exportPDF = function() {
      track('pdf_export', { report_slug: window.location.pathname.replace('/reports/', '').replace('.html', '') });
      flush();
      if (origExportPDF) origExportPDF();
    };

    // Track About modal
    var origOpenAbout = window.openAboutModal;
    window.openAboutModal = function() {
      track('about_open');
      if (origOpenAbout) origOpenAbout();
    };
  })();

})();
"""

# =============================================================================
# Index Page HTML Template
# =============================================================================

INDEX_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Radar AWS - AWS AI/ML News Hub</title>
  <link rel="icon" type="image/png" href="assets/favicon.png">
  <link rel="stylesheet" href="assets/style.css">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js" integrity="sha384-jb8JQMbMoBUzgWatfe6COACi2ljcDdZQ2OxczGA3bGNeWe+6DChMTBJemed7ZnvJ" crossorigin="anonymous"></script>
</head>
<body>
  <header class="site-header">
    <div class="header-content">
      <a href="index.html" class="site-logo">
        <img src="assets/logo-header.png" alt="AI Radar AWS" class="logo-icon-img">
        <div class="logo-text">
          <h1>AI Radar <span>AWS</span></h1>
          <p class="tagline">AWS AI/ML news &mdash; curated, researched, explained</p>
        </div>
      </a>
      <nav class="header-nav">
        <a href="#filters">Filters</a>
        <a href="#timeline">Timeline</a>
        <a href="#announcements">News</a>
        <a href="#" onclick="openAboutModal(); return false;">About</a>
      </nav>
    </div>
  </header>

  <main class="main-content">
    <!-- Filters -->
    <section class="filters-section" id="filters">
      <div class="filters-header">
        <h2 class="filters-title">Filter Announcements</h2>
        <div class="filters-actions">
          <select id="sort-select" class="sort-select">
            <option value="newest">Newest first</option>
            <option value="importance">Most important first</option>
          </select>
          <button class="filter-reset" id="filter-reset">Reset</button>
        </div>
      </div>

      <div class="filter-dimension" id="filter-time-row">
        <span class="dimension-label">Time</span>
        <div class="dimension-chips">
          <button class="filter-chip active" data-time="all">All</button>
          <button class="filter-chip" data-time="week">Last Week</button>
          <button class="filter-chip" data-time="month">Last Month</button>
          <button class="filter-chip" data-time="3months">Last 3 Months</button>
        </div>
      </div>

      <div class="filter-dimension" id="filter-services-row">
        <span class="dimension-label">Services</span>
        <div class="dimension-chips" id="service-chips"></div>
      </div>

      <div class="filter-dimension" id="filter-types-row">
        <span class="dimension-label">Type</span>
        <div class="dimension-chips" id="type-chips"></div>
      </div>

      <div class="filter-dimension" id="filter-concepts-row">
        <span class="dimension-label">Concepts</span>
        <div class="dimension-chips" id="concept-chips"></div>
      </div>

      <div class="filter-dimension filter-dimension-collapsed" id="filter-more-row">
        <button class="show-more-btn" id="show-more-filters">More filters...</button>
        <div class="more-filters-content" id="more-filters-content" style="display:none;">
          <div class="filter-dimension-inner" id="filter-usecases-row">
            <span class="dimension-label">Use Cases</span>
            <div class="dimension-chips" id="usecase-chips"></div>
          </div>
          <div class="filter-dimension-inner" id="filter-providers-row">
            <span class="dimension-label">Providers</span>
            <div class="dimension-chips" id="provider-chips"></div>
          </div>
        </div>
      </div>

      <div class="active-filters" id="active-filters" style="display:none;">
        <span class="dimension-label">Active</span>
        <div class="active-filters-chips" id="active-filters-chips"></div>
      </div>
    </section>

    <!-- Timeline -->
    <section class="timeline-section" id="timeline">
      <h2>Announcement Timeline</h2>
      <div class="timeline-chart-container">
        <canvas id="timeline-chart"></canvas>
      </div>
    </section>

    <!-- Announcements Grid -->
    <section id="announcements">
      <div class="announcements-grid" id="announcements-grid">
        {{CARDS}}
      </div>
      <div class="no-results" id="no-results">
        <p>No announcements match your current filters.</p>
      </div>
    </section>
  </main>

  <footer class="site-footer">
    <p>AI Radar AWS &mdash; Automatically curated AWS AI/ML news. Generated daily.</p>
  </footer>

  <!-- About Modal -->
  <div class="about-modal-overlay" id="about-modal-overlay">
    <div class="about-modal">
      <button class="about-modal-close" onclick="closeAboutModal()" aria-label="Close">&times;</button>
      <div class="about-header">
        <img src="assets/logo-about.png" alt="AI Radar AWS" class="about-logo">
        <h2>AI Radar <span>AWS</span></h2>
        <p class="about-tagline">AWS AI/ML news &mdash; curated, researched, explained</p>
      </div>

      <div class="about-tabs">
        <button class="about-tab active" onclick="switchAboutTab('overview')">Overview</button>
        <button class="about-tab" onclick="switchAboutTab('scoring')">Scoring &amp; Geography</button>
      </div>

      <div class="about-tab-content active" id="about-tab-overview">
        <p>An automated intelligence platform that curates, researches, and analyzes AWS AI/ML/GenAI announcements daily. Every report is backed by real research — the system reads linked blog posts and documentation to provide accurate, in-depth analysis.</p>

        <h3>How Each Report Is Generated</h3>
        <ol>
          <li><strong>Collection</strong> — Daily monitoring of the AWS "What's New" RSS feed</li>
          <li><strong>Filtering</strong> — AI-powered relevance detection for AI/ML/GenAI topics</li>
          <li><strong>Taxonomy Tagging</strong> — LLM-based classification across 6 dimensions</li>
          <li><strong>Importance Scoring</strong> — Point-based system with tag bonuses (1-5 stars)</li>
          <li><strong>Research Phase</strong> — Follows links to blog posts and documentation</li>
          <li><strong>Report Generation</strong> — Claude Sonnet produces structured 6-section analysis</li>
          <li><strong>Visual Summary</strong> — Claude Opus generates Mermaid diagrams for key items</li>
          <li><strong>Publishing</strong> — Static website rebuilt and deployed via CloudFront</li>
        </ol>

        <h3>Features</h3>
        <ul>
          <li>Faceted filtering by service, type, concept, and more</li>
          <li>Multi-dimensional taxonomy with 80+ tags across 6 dimensions</li>
          <li>Geographic relevance badges (APJ / Global)</li>
          <li>Timeline visualization of announcement volume</li>
          <li>PDF export for offline reading</li>
          <li>Mermaid visual summaries for key announcements</li>
          <li>Daily automated updates — no manual curation</li>
        </ul>

        <div class="highlight-box">
          <strong>What makes this different:</strong> Each report involves a dedicated research phase where the system reads linked blog posts and AWS documentation pages. This produces analysis that goes beyond the original announcement text.
        </div>

        <h3>Technology</h3>
        <p>Built with Python, AWS Lambda, Amazon Bedrock (Claude Sonnet 4.6, Opus 4.6, Haiku 4.5), S3, CloudFront, WAF, EventBridge, and CDK.</p>

        <h3>Open Source</h3>
        <p>This project is open source. Fork it, customize it for your needs, and deploy your own instance.<br>
        <a href="https://github.com/bbonik/ai-radar-aws" target="_blank" rel="noopener noreferrer">&#x1F4E6; github.com/bbonik/ai-radar-aws</a></p>
      </div>

      <div class="about-tab-content" id="about-tab-scoring">
        <h3>How Importance Scoring Works</h3>
        <p>Each announcement receives a point score based on multiple factors. The total score maps to a 1-5 star rating:</p>

        <div class="star-scale">
          <span class="star-scale-item" style="border-left: 3px solid #9e9e9e;">1★ &lt; 2 pts</span>
          <span class="star-scale-item" style="border-left: 3px solid #2476F9;">2★ &ge; 2 pts</span>
          <span class="star-scale-item" style="border-left: 3px solid #24F93D;">3★ &ge; 3.5 pts</span>
          <span class="star-scale-item" style="border-left: 3px solid #f9a825;">4★ &ge; 5 pts</span>
          <span class="star-scale-item" style="border-left: 3px solid #f924e1;">5★ &ge; 6.5 pts</span>
        </div>

        <h3>Point Breakdown</h3>
        <table class="scoring-table">
          <thead><tr><th>Factor</th><th>Points</th><th>When</th></tr></thead>
          <tbody>
            <tr><td>Core AI service (Bedrock, AgentCore, SageMaker AI)</td><td class="pts-pos">+4</td><td>Service named in title</td></tr>
            <tr><td>Key AI service (SageMaker, Kiro, QuickSight)</td><td class="pts-pos">+2</td><td>Service named in title</td></tr>
            <tr><td>Other AI-related service</td><td class="pts-pos">+1</td><td>Default</td></tr>
            <tr><td>Blog post link</td><td class="pts-pos">+3</td><td>Link to aws.amazon.com/blogs/</td></tr>
            <tr><td>GitHub samples link</td><td class="pts-pos">+2</td><td>Link to github.com/aws*</td></tr>
            <tr><td>Documentation link</td><td class="pts-pos">+1</td><td>Link to docs.aws.amazon.com/</td></tr>
            <tr><td>New model</td><td class="pts-pos">+1.5</td><td>Tagged as "new-model"</td></tr>
            <tr><td>New service</td><td class="pts-pos">+1</td><td>Tagged as "new-service"</td></tr>
            <tr><td>New feature</td><td class="pts-pos">+0.5</td><td>Tagged as "new-feature"</td></tr>
            <tr><td>Anthropic / OpenAI provider</td><td class="pts-pos">+2</td><td>Provider explicitly mentioned</td></tr>
            <tr><td>Instance / notebook announcement</td><td class="pts-neg">-2</td><td>Hardware/capacity, not feature</td></tr>
            <tr><td>Performance / pricing / security</td><td class="pts-neg">-0.5</td><td>Incremental updates</td></tr>
            <tr><td>Region expansion to APJ</td><td class="pts-pos">+1</td><td>Expands to Asia Pacific</td></tr>
            <tr><td>Region expansion (non-APJ only)</td><td class="pts-neg">-1.5</td><td>Only expands to other regions</td></tr>
          </tbody>
        </table>

        <h3>Geographic Relevance Badges</h3>
        <p>Each announcement card shows a small badge indicating whether the feature is available in your region:</p>

        <div class="geo-legend">
          <div class="geo-legend-item"><span class="geo-badge geo-local">&#127759; APJ</span> Confirmed available in Asia Pacific</div>
          <div class="geo-legend-item"><span class="geo-badge geo-global">&#127760; Global</span> Available in all regions (includes APJ)</div>
          <div class="geo-legend-item"><span style="color:var(--aws-text-secondary);">No badge</span> Not available in APJ, or region unknown</div>
        </div>

        <div class="highlight-box">
          <strong>How geography is detected:</strong> An LLM analyzes each announcement to determine geographic availability. If the text mentions Asia Pacific regions (Tokyo, Singapore, Sydney, etc.), it gets the APJ badge. If it says "all regions" or is a new feature on a globally-available service, it gets the Global badge.
        </div>
      </div>
    </div>
  </div>

  <script src="assets/app.js"></script>
</body>
</html>
"""

# =============================================================================
# Report Page HTML Template
# =============================================================================

REPORT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{TITLE}} - AI Radar AWS</title>
  <link rel="icon" type="image/png" href="../assets/favicon.png">
  <link rel="stylesheet" href="../assets/style.css">
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.6/dist/mermaid.min.js" integrity="sha384-qX9VvWkP79m/O121ZE6sOYp0nf/pldQgtvWDbkpzi+3mUo4Wn4Ix4cFzNPay3VaB" crossorigin="anonymous"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js" integrity="sha384-Yv5O+t3uE3hunW8uyrbpPW3iw6/5/Y7HitWJBLgqfMoA36NogMmy+8wWZMpn3HWc" crossorigin="anonymous"></script>
</head>
<body>
  <header class="site-header">
    <div class="header-content">
      <a href="../index.html" class="site-logo">
        <img src="../assets/logo-header.png" alt="AI Radar AWS" class="logo-icon-img">
        <div class="logo-text">
          <h1>AI Radar <span>AWS</span></h1>
          <p class="tagline">AWS AI/ML news &mdash; curated, researched, explained</p>
        </div>
      </a>
      <nav class="header-nav">
        <a href="../index.html">Home</a>
        <a href="#" onclick="openAboutModal(); return false;">About</a>
      </nav>
    </div>
  </header>

  <main class="report-container">
    <a href="../index.html" class="back-link">&larr; Back to all announcements</a>

    <div id="report-content">
      <header class="report-header">
        <div class="report-meta">
          <span class="stars importance-{{IMPORTANCE_LEVEL}}">{{STARS}}</span>
          <span class="date">{{DATE}}</span>
          <span class="service">{{SERVICE}}</span>
        </div>
        <h1 class="report-title">{{TITLE}}</h1>
        <a href="{{LINK}}" class="report-source-link" target="_blank" rel="noopener noreferrer">View original announcement &rarr;</a>
        <div class="report-actions">
          <button class="btn-pdf" onclick="exportPDF()">Export as PDF</button>
        </div>
      </header>

      <section class="report-section">
        <h2>What&#x27;s New</h2>
        {{WHATS_NEW}}
      </section>

      <section class="report-section">
        <h2>How It Works</h2>
        {{HOW_IT_WORKS}}
      </section>

      <section class="report-section">
        <h2>Why It&#x27;s Important</h2>
        {{WHY_IMPORTANT}}
      </section>

      <section class="report-section">
        <h2>How It&#x27;s Different</h2>
        {{HOW_DIFFERENT}}
      </section>

      <section class="report-section">
        <h2>When to Prefer It</h2>
        {{WHEN_TO_PREFER}}
      </section>

      <section class="report-section">
        <h2>Availability</h2>
        {{AVAILABILITY}}
      </section>

      {{TAGS_SECTION}}
      {{MERMAID_SECTION}}
      {{BLOGPOST_LINKS}}
    </div>
  </main>

  <footer class="site-footer">
    <p>AI Radar AWS &mdash; Automatically curated AWS AI/ML news. Generated daily.</p>
  </footer>

  <!-- About Modal -->
  <div class="about-modal-overlay" id="about-modal-overlay">
    <div class="about-modal">
      <button class="about-modal-close" onclick="closeAboutModal()" aria-label="Close">&times;</button>
      <div class="about-header">
        <img src="../assets/logo-about.png" alt="AI Radar AWS" class="about-logo">
        <h2>AI Radar <span>AWS</span></h2>
        <p class="about-tagline">AWS AI/ML news &mdash; curated, researched, explained</p>
      </div>

      <div class="about-tabs">
        <button class="about-tab active" onclick="switchAboutTab('overview')">Overview</button>
        <button class="about-tab" onclick="switchAboutTab('scoring')">Scoring &amp; Geography</button>
      </div>

      <div class="about-tab-content active" id="about-tab-overview">
        <p>An automated intelligence platform that curates, researches, and analyzes AWS AI/ML/GenAI announcements daily. Every report is backed by real research — the system reads linked blog posts and documentation to provide accurate, in-depth analysis.</p>

        <h3>How Each Report Is Generated</h3>
        <ol>
          <li><strong>Collection</strong> — Daily monitoring of the AWS "What's New" RSS feed</li>
          <li><strong>Filtering</strong> — AI-powered relevance detection for AI/ML/GenAI topics</li>
          <li><strong>Taxonomy Tagging</strong> — LLM-based classification across 6 dimensions</li>
          <li><strong>Importance Scoring</strong> — Point-based system with tag bonuses (1-5 stars)</li>
          <li><strong>Research Phase</strong> — Follows links to blog posts and documentation</li>
          <li><strong>Report Generation</strong> — Claude Sonnet produces structured 6-section analysis</li>
          <li><strong>Visual Summary</strong> — Claude Opus generates Mermaid diagrams for key items</li>
          <li><strong>Publishing</strong> — Static website rebuilt and deployed via CloudFront</li>
        </ol>

        <h3>Features</h3>
        <ul>
          <li>Faceted filtering by service, type, concept, and more</li>
          <li>Multi-dimensional taxonomy with 80+ tags across 6 dimensions</li>
          <li>Geographic relevance badges (APJ / Global)</li>
          <li>Timeline visualization of announcement volume</li>
          <li>PDF export for offline reading</li>
          <li>Mermaid visual summaries for key announcements</li>
          <li>Daily automated updates — no manual curation</li>
        </ul>

        <div class="highlight-box">
          <strong>What makes this different:</strong> Each report involves a dedicated research phase where the system reads linked blog posts and AWS documentation pages. This produces analysis that goes beyond the original announcement text.
        </div>

        <h3>Technology</h3>
        <p>Built with Python, AWS Lambda, Amazon Bedrock (Claude Sonnet 4.6, Opus 4.6, Haiku 4.5), S3, CloudFront, WAF, EventBridge, and CDK.</p>

        <h3>Open Source</h3>
        <p>This project is open source. Fork it, customize it for your needs, and deploy your own instance.<br>
        <a href="https://github.com/bbonik/ai-radar-aws" target="_blank" rel="noopener noreferrer">&#x1F4E6; github.com/bbonik/ai-radar-aws</a></p>
      </div>

      <div class="about-tab-content" id="about-tab-scoring">
        <h3>How Importance Scoring Works</h3>
        <p>Each announcement receives a point score based on multiple factors. The total score maps to a 1-5 star rating:</p>

        <div class="star-scale">
          <span class="star-scale-item" style="border-left: 3px solid #9e9e9e;">1★ &lt; 2 pts</span>
          <span class="star-scale-item" style="border-left: 3px solid #2476F9;">2★ &ge; 2 pts</span>
          <span class="star-scale-item" style="border-left: 3px solid #24F93D;">3★ &ge; 3.5 pts</span>
          <span class="star-scale-item" style="border-left: 3px solid #f9a825;">4★ &ge; 5 pts</span>
          <span class="star-scale-item" style="border-left: 3px solid #f924e1;">5★ &ge; 6.5 pts</span>
        </div>

        <h3>Point Breakdown</h3>
        <table class="scoring-table">
          <thead><tr><th>Factor</th><th>Points</th><th>When</th></tr></thead>
          <tbody>
            <tr><td>Core AI service (Bedrock, AgentCore, SageMaker AI)</td><td class="pts-pos">+4</td><td>Service named in title</td></tr>
            <tr><td>Key AI service (SageMaker, Kiro, QuickSight)</td><td class="pts-pos">+2</td><td>Service named in title</td></tr>
            <tr><td>Other AI-related service</td><td class="pts-pos">+1</td><td>Default</td></tr>
            <tr><td>Blog post link</td><td class="pts-pos">+3</td><td>Link to aws.amazon.com/blogs/</td></tr>
            <tr><td>GitHub samples link</td><td class="pts-pos">+2</td><td>Link to github.com/aws*</td></tr>
            <tr><td>Documentation link</td><td class="pts-pos">+1</td><td>Link to docs.aws.amazon.com/</td></tr>
            <tr><td>New model</td><td class="pts-pos">+1.5</td><td>Tagged as "new-model"</td></tr>
            <tr><td>New service</td><td class="pts-pos">+1</td><td>Tagged as "new-service"</td></tr>
            <tr><td>New feature</td><td class="pts-pos">+0.5</td><td>Tagged as "new-feature"</td></tr>
            <tr><td>Anthropic / OpenAI provider</td><td class="pts-pos">+2</td><td>Provider explicitly mentioned</td></tr>
            <tr><td>Instance / notebook announcement</td><td class="pts-neg">-2</td><td>Hardware/capacity, not feature</td></tr>
            <tr><td>Performance / pricing / security</td><td class="pts-neg">-0.5</td><td>Incremental updates</td></tr>
            <tr><td>Region expansion to APJ</td><td class="pts-pos">+1</td><td>Expands to Asia Pacific</td></tr>
            <tr><td>Region expansion (non-APJ only)</td><td class="pts-neg">-1.5</td><td>Only expands to other regions</td></tr>
          </tbody>
        </table>

        <h3>Geographic Relevance Badges</h3>
        <p>Each announcement card shows a small badge indicating whether the feature is available in your region:</p>

        <div class="geo-legend">
          <div class="geo-legend-item"><span class="geo-badge geo-local">&#127759; APJ</span> Confirmed available in Asia Pacific</div>
          <div class="geo-legend-item"><span class="geo-badge geo-global">&#127760; Global</span> Available in all regions (includes APJ)</div>
          <div class="geo-legend-item"><span style="color:var(--aws-text-secondary);">No badge</span> Not available in APJ, or region unknown</div>
        </div>

        <div class="highlight-box">
          <strong>How geography is detected:</strong> An LLM analyzes each announcement to determine geographic availability. If the text mentions Asia Pacific regions (Tokyo, Singapore, Sydney, etc.), it gets the APJ badge. If it says "all regions" or is a new feature on a globally-available service, it gets the Global badge.
        </div>
      </div>
    </div>
  </div>

  <script src="../assets/app.js"></script>
  <script>
    mermaid.initialize({ startOnLoad: true, theme: 'neutral' });
  </script>
</body>
</html>
"""

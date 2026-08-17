"""Topic attribution: join report page views to the announcement taxonomy.

Report page URLs changed format on 2026-08-14 (design delta D1):

- old (pre-cutover):  whole link mangled to '-', truncated at 80 chars.
  Removed from the codebase at the cutover; re-implemented here as a pure
  function because pre-cutover CloudFront records still carry these slugs.
  SUNSET: the last raw data with old slugs expires ~2026-11-12 (cutover +
  90 days); after that `old_slug` is unreachable and may be deleted.
- new (current):      <link-tail>-<8-char-sha256-of-link>, from
  src/website_builder/builder.py::_slug_from_link. Copied here (not
  imported) to keep the Lambda decoupled from the website builder; a unit
  test asserts the copy matches the original.

Both are pure functions of the announcement `link` (never rewritten), so
the join index is regenerated from the catalog on every run — no mapping
table to maintain.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field

TAXONOMY_DIMENSIONS = ("services", "types", "concepts", "use_cases", "providers")

# Grouping keys are capped to keep artifacts bounded (Req 7.1).
MAX_KEY_LEN = 1024

_OLD_SLUG_MAX = 80
_NEW_SLUG_MAX_BASE = 150  # keep in sync with builder.SLUG_MAX_BASE (test-enforced)
_NEW_SLUG_HASH_LEN = 8    # keep in sync with builder.SLUG_HASH_LEN (test-enforced)

_REPORT_PATH_RE = re.compile(r"^/reports/(?P<slug>[^/?#]+)\.html$")


def old_slug(link: str) -> str:
    """Pre-2026-08-14 slug algorithm, verbatim (commit 66becea^)."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", link)
    slug = slug.strip("-")
    if len(slug) > _OLD_SLUG_MAX:
        slug = slug[:_OLD_SLUG_MAX].rstrip("-")
    return slug


def new_slug(link: str) -> str:
    """Current slug algorithm (mirror of builder._slug_from_link)."""
    tail = link.rstrip("/").rsplit("/", 1)[-1]
    base = re.sub(r"[^a-zA-Z0-9]+", "-", tail).strip("-").lower()[:_NEW_SLUG_MAX_BASE]
    digest = hashlib.sha256(link.encode("utf-8")).hexdigest()[:_NEW_SLUG_HASH_LEN]
    return f"{base}-{digest}" if base else digest


def truncate_key(key: str) -> str:
    """Cap a grouping key at MAX_KEY_LEN characters (Req 7.1)."""
    return key[:MAX_KEY_LEN]


def extract_report_slug(path: str) -> str | None:
    """'/reports/<slug>.html' -> '<slug>'; None for any other path."""
    m = _REPORT_PATH_RE.match(path)
    return m.group("slug") if m else None


@dataclass
class CatalogEntry:
    """One announcement row: its link and taxonomy tag lists (Req 13.3)."""

    link: str
    tags_by_dimension: dict[str, list[str]]


class CatalogError(Exception):
    """The Announcement_Catalog could not be parsed (Req 13.12)."""


def load_catalog(csv_text: str) -> list[CatalogEntry]:
    """Parse the announcements CSV into catalog entries.

    Only `link` and `tags` are read. A row with an unparsable `tags` value
    contributes empty tag lists (matching AnnouncementTags.deserialize's
    lenient behaviour); a structurally unreadable CSV raises CatalogError.
    """
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
    except csv.Error as exc:
        raise CatalogError(f"announcements CSV is unreadable: {exc}") from exc
    if rows and ("link" not in rows[0] or "tags" not in rows[0]):
        raise CatalogError("announcements CSV lacks required columns link/tags")

    entries = []
    for row in rows:
        link = (row.get("link") or "").strip()
        if not link:
            continue
        tags_by_dimension: dict[str, list[str]] = {d: [] for d in TAXONOMY_DIMENSIONS}
        raw = row.get("tags") or ""
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    for dim in TAXONOMY_DIMENSIONS:
                        values = parsed.get(dim, [])
                        if isinstance(values, list):
                            tags_by_dimension[dim] = [str(v) for v in values if v]
            except (json.JSONDecodeError, TypeError):
                pass  # tolerated per AnnouncementTags.deserialize
        entries.append(CatalogEntry(link=link, tags_by_dimension=tags_by_dimension))
    return entries


def build_slug_index(entries: list[CatalogEntry]) -> dict[str, list[CatalogEntry]]:
    """slug -> matching catalog entries, over BOTH slug algorithms.

    Each entry is indexed under its old-format and new-format slug so page
    views recorded either side of the 2026-08-14 cutover resolve. The same
    entry is registered once per distinct slug form.
    """
    index: dict[str, list[CatalogEntry]] = {}
    for entry in entries:
        for slug in {old_slug(entry.link), new_slug(entry.link)}:
            if slug:
                index.setdefault(slug, []).append(entry)
    return index


@dataclass
class TopicAttribution:
    """Result of joining one month's report page views to the taxonomy."""

    # (dimension, tag) -> summed views. Exact, no truncation (Req 13.5).
    metrics: dict[tuple[str, str], int] = field(default_factory=dict)
    # [{"slug": ..., "matches": n, "views": n}] for multi-match slugs (Req 13.6)
    ambiguous: list[dict] = field(default_factory=list)
    total_report_views: int = 0
    attributed_views: int = 0
    ambiguous_views: int = 0
    unattributed_views: int = 0


def attribute(slug_views: dict[str, int], index: dict[str, list[CatalogEntry]]) -> TopicAttribution:
    """Attribute per-slug view counts to (dimension, tag) pairs (Req 13.4-13.9).

    Reconciliation invariant (Req 13.8, test-enforced):
        attributed + ambiguous + unattributed == total.
    """
    result = TopicAttribution()
    for slug in sorted(slug_views):  # deterministic order
        views = slug_views[slug]
        result.total_report_views += views
        matches = index.get(slug, [])
        if len(matches) == 1:
            entry = matches[0]
            tagged = False
            for dim in TAXONOMY_DIMENSIONS:
                for tag in entry.tags_by_dimension.get(dim, []):
                    key = (dim, truncate_key(tag))
                    result.metrics[key] = result.metrics.get(key, 0) + views
                    tagged = True
            # "Attributed" means attributed to at least one Topic_Tag
            # (Req 13.8); a matched but tagless announcement (possible only
            # for legacy rows with an empty tags column) counts as
            # unattributed so the reconciliation quad still sums.
            if tagged:
                result.attributed_views += views
            else:
                result.unattributed_views += views
        elif len(matches) > 1:
            result.ambiguous_views += views
            result.ambiguous.append(
                {"slug": truncate_key(slug), "matches": len(matches), "views": views}
            )
        else:
            result.unattributed_views += views
    return result

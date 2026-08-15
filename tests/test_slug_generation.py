"""Tests for report slug generation.

The previous scheme slugified the whole URL capped at 80 chars; the fixed AWS
boilerplate prefix left only 32 chars to disambiguate, and 6 live links
collided onto 2 slugs, making 4 report pages unreachable. The new scheme is
<last-path-segment>-<8-char-sha256-of-full-link>, collision-free by
construction.

Plan: docs/audit-remediation-plan.md item 6 (decision D3).
"""

import re
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from src.website_builder.builder import SLUG_HASH_LEN, _slug_from_link

FIXTURE = Path(__file__).parent / "fixtures" / "live_links_snapshot.txt"


def _live_links() -> list[str]:
    return [l for l in FIXTURE.read_text().splitlines() if l.strip()]


class TestSlugFormat:
    def test_readable_tail_plus_hash(self):
        slug = _slug_from_link(
            "https://aws.amazon.com/about-aws/whats-new/2026/07/"
            "amazon-sagemaker-unified-studio-terraform/"
        )
        assert slug.startswith("amazon-sagemaker-unified-studio-terraform-")
        assert re.fullmatch(r"[a-z0-9-]+", slug)
        assert len(slug.rsplit("-", 1)[-1]) == SLUG_HASH_LEN

    def test_trailing_slash_does_not_change_readable_part(self):
        base = "https://aws.amazon.com/about-aws/whats-new/2026/05/some-feature"
        with_slash = _slug_from_link(base + "/")
        without = _slug_from_link(base)
        # Readable part identical; hash differs because the full link differs
        # (AWS treats these as distinct URLs, so distinct slugs are correct).
        assert with_slash.rsplit("-", 1)[0] == without.rsplit("-", 1)[0]

    def test_uppercase_is_normalised(self):
        slug = _slug_from_link(
            "https://aws.amazon.com/about-aws/whats-new/2026/06/GPT54-available/"
        )
        assert slug == slug.lower()

    def test_degenerate_link_still_yields_slug(self):
        """A URL whose tail produces no characters falls back to hash-only."""
        slug = _slug_from_link("https://例え.jp/⛅/")
        assert re.fullmatch(r"[0-9a-f]{%d}" % SLUG_HASH_LEN, slug)

    def test_filesystem_and_url_safe(self):
        for link in _live_links():
            slug = _slug_from_link(link)
            assert re.fullmatch(r"[a-z0-9-]+", slug), slug
            assert len(slug) < 200


class TestSlugUniqueness:
    def test_zero_collisions_across_live_snapshot(self):
        """Regression for the production incident: 249 live links, 249 slugs."""
        links = _live_links()
        slugs = {_slug_from_link(l) for l in links}
        assert len(slugs) == len(links)

    def test_previously_colliding_cluster_now_distinct(self):
        """The exact 4 links that shared one slug under the old scheme."""
        cluster = [
            "https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-sagemaker-unified-studio",
            "https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-sagemaker-unified-studio-git/",
            "https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-sagemaker-unified-studio-import-existing-mwaa-environments/",
            "https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-sagemaker-unified-studio-terraform/",
        ]
        assert len({_slug_from_link(l) for l in cluster}) == 4

    @given(
        st.lists(
            st.text(
                alphabet=st.characters(min_codepoint=33, max_codepoint=126),
                min_size=1,
                max_size=120,
            ).map(lambda s: f"https://aws.amazon.com/whats-new/{s}"),
            min_size=2,
            max_size=20,
            unique=True,
        )
    )
    def test_distinct_links_yield_distinct_slugs(self, links):
        """Property: for any set of distinct links, slugs are distinct."""
        slugs = [_slug_from_link(l) for l in links]
        assert len(set(slugs)) == len(links)

    @given(
        st.text(
            alphabet=st.characters(min_codepoint=33, max_codepoint=1000),
            min_size=1,
            max_size=200,
        ).map(lambda s: f"https://example.com/{s}")
    )
    def test_slug_is_deterministic_and_safe(self, link):
        """Property: same link → same slug; output always URL/key safe."""
        first = _slug_from_link(link)
        second = _slug_from_link(link)
        assert first == second
        assert re.fullmatch(r"[a-z0-9-]+", first)

"""Unit tests for src/analytics_rollup: queries, topics, and the month runner.

Validates the correctness properties in
.kiro/specs/monthly-analytics-rollup/design.md (windows, determinism,
idempotent replace, reconciliation, zero-means-zero, coverage totality,
privacy by construction, cost ceiling).
"""

import json
from datetime import datetime, timezone

import pytest
from hypothesis import given, strategies as st

from src.analytics_rollup import aggregate, queries, topics
from src.analytics_rollup.rollup import (
    MonthResult,
    QueryError,
    build_month_json,
    compute_coverage,
    is_month_completed,
    month_csv_key,
    month_json_key,
    parse_scalar,
    ranked_entries,
    render_month_csv,
    run_month,
)
from src.website_builder import builder

NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


# ─── queries: windows and SQL shape ──────────────────────────────────────────

class TestWindows:
    def test_month_window_is_half_open(self):
        assert queries.window_for_month("2026-06") == ("2026-06-01", "2026-07-01")

    def test_month_window_year_wrap(self):
        assert queries.window_for_month("2026-12") == ("2026-12-01", "2027-01-01")

    def test_month_window_february(self):
        assert queries.window_for_month("2028-02") == ("2028-02-01", "2028-03-01")

    @pytest.mark.parametrize("bad", ["2026-13", "2026-00", "202606", "2026-6",
                                     "26-06", "2026-06-01", "", "junk"])
    def test_invalid_months_rejected(self, bad):
        assert not queries.is_valid_month(bad)
        with pytest.raises(ValueError):
            queries.window_for_month(bad)

    def test_day_window_preserves_existing_semantics(self):
        start, end = queries.window_for_days(7, NOW)
        assert start == "2026-08-10"
        assert end == queries.FAR_FUTURE

    def test_month_range_ascending_inclusive(self):
        assert queries.month_range("2026-11", "2027-02") == [
            "2026-11", "2026-12", "2027-01", "2027-02"]

    def test_month_range_rejects_inverted(self):
        with pytest.raises(ValueError):
            queries.month_range("2026-07", "2026-05")


class TestQuerySql:
    def test_every_query_uses_half_open_window(self):
        for name, build in queries.METRIC_QUERIES.items():
            sql = build("2026-06-01", "2026-07-01")
            assert ("date < DATE('2026-07-01')" in sql
                    or "server_timestamp < '2026-07-01'" in sql), name

    def test_ranked_queries_have_30_row_limit_and_tiebreak(self):
        for name in queries.RANKED_METRICS:
            sql = queries.METRIC_QUERIES[name]("2026-06-01", "2026-07-01")
            assert f"LIMIT {queries.RANKED_LIMIT}" in sql
            assert "DESC" in sql and "ASC" in sql, f"{name} lacks tiebreak"

    def test_filter_metric_parity_with_original_report(self):
        """Req 2.2: 200-filter on all CF metrics; .html only on two of them."""
        for name in ("total_pageviews_cf", "unique_visitors_cf", "top_pages_cf"):
            assert "sc_status = 200" in queries.METRIC_QUERIES[name]("a", "b")
        assert "LIKE '%.html'" in queries.METRIC_QUERIES["total_pageviews_cf"]("a", "b")
        assert "LIKE '%.html'" in queries.METRIC_QUERIES["top_pages_cf"]("a", "b")
        assert "LIKE" not in queries.METRIC_QUERIES["unique_visitors_cf"]("a", "b")

    def test_slug_query_has_no_limit(self):
        """Req 13.1: topic derivation covers ALL visited report pages."""
        assert "LIMIT" not in queries.report_pageviews_by_slug_sql("a", "b")


# ─── topics: slugs, catalog, attribution ─────────────────────────────────────

class TestSlugs:
    def test_new_slug_matches_website_builder(self):
        """The local copy must track builder._slug_from_link exactly."""
        links = [
            "https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-sagemaker-unified-studio-terraform/",
            "https://aws.amazon.com/blogs/aws/claude-opus-5/",
            "https://例え.jp/⛅/",
        ]
        for link in links:
            assert topics.new_slug(link) == builder._slug_from_link(link)

    def test_slug_constants_track_builder(self):
        assert topics._NEW_SLUG_MAX_BASE == builder.SLUG_MAX_BASE
        assert topics._NEW_SLUG_HASH_LEN == builder.SLUG_HASH_LEN

    def test_old_slug_known_value(self):
        assert topics.old_slug("https://example.com/a/b") == "https-example-com-a-b"

    def test_old_slug_truncates_at_80_and_strips_trailing_dash(self):
        link = "https://x.com/" + "ab-" * 40  # mangles well past 80 chars
        slug = topics.old_slug(link)
        assert len(slug) <= 80
        assert not slug.endswith("-")

    def test_old_slug_collision_reproduced(self):
        """The live 80-char boilerplate collision (design D1 / decision 9)."""
        a = "https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-sagemaker-unified-studio-terraform/"
        b = "https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-sagemaker-unified-studio-scheduling/"
        assert topics.old_slug(a) == topics.old_slug(b)
        assert topics.new_slug(a) != topics.new_slug(b)

    def test_extract_report_slug(self):
        assert topics.extract_report_slug("/reports/foo-1a2b3c4d.html") == "foo-1a2b3c4d"
        assert topics.extract_report_slug("/index.html") is None
        assert topics.extract_report_slug("/reports/nested/x.html") is None


def _catalog_csv(rows):
    import csv as _csv
    import io as _io
    out = _io.StringIO()
    writer = _csv.DictWriter(out, fieldnames=["title", "link", "tags"])
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _tags(**kwargs):
    base = {d: [] for d in topics.TAXONOMY_DIMENSIONS}
    base.update(kwargs)
    return json.dumps(base)


class TestCatalogAndAttribution:
    def test_load_catalog_reads_link_and_tags(self):
        text = _catalog_csv([{"title": "t", "link": "https://x.com/a",
                              "tags": _tags(services=["bedrock"], concepts=["agentic-ai"])}])
        entries = topics.load_catalog(text)
        assert entries[0].link == "https://x.com/a"
        assert entries[0].tags_by_dimension["services"] == ["bedrock"]

    def test_unparsable_tags_tolerated_as_empty(self):
        text = _catalog_csv([{"title": "t", "link": "https://x.com/a", "tags": "{bad"}])
        entries = topics.load_catalog(text)
        assert all(not v for v in entries[0].tags_by_dimension.values())

    def test_missing_columns_raise(self):
        with pytest.raises(topics.CatalogError):
            topics.load_catalog("title,notlink\nx,y\n")

    def test_index_contains_both_slug_forms(self):
        entries = topics.load_catalog(_catalog_csv(
            [{"title": "t", "link": "https://x.com/a", "tags": _tags()}]))
        index = topics.build_slug_index(entries)
        assert topics.old_slug("https://x.com/a") in index
        assert topics.new_slug("https://x.com/a") in index

    def test_attribution_matched_ambiguous_unattributed(self):
        e1 = topics.CatalogEntry("https://x.com/one",
                                 {**{d: [] for d in topics.TAXONOMY_DIMENSIONS},
                                  "services": ["bedrock"], "concepts": ["rag"]})
        index = topics.build_slug_index([e1])
        amb_slug = "shared-slug"
        index[amb_slug] = [e1, e1]  # simulate a 2-way collision
        views = {topics.new_slug("https://x.com/one"): 10,
                 amb_slug: 3, "no-match": 4}
        result = topics.attribute(views, index)
        assert result.metrics[("services", "bedrock")] == 10
        assert result.metrics[("concepts", "rag")] == 10
        assert result.attributed_views == 10
        assert result.ambiguous_views == 3
        assert result.ambiguous == [{"slug": amb_slug, "matches": 2, "views": 3}]
        assert result.unattributed_views == 4
        assert result.total_report_views == 17

    def test_matched_but_tagless_counts_as_unattributed(self):
        e = topics.CatalogEntry("https://x.com/bare",
                                {d: [] for d in topics.TAXONOMY_DIMENSIONS})
        index = topics.build_slug_index([e])
        result = topics.attribute({topics.new_slug("https://x.com/bare"): 5}, index)
        assert result.attributed_views == 0
        assert result.unattributed_views == 5
        assert result.metrics == {}

    @given(st.dictionaries(st.text(min_size=1, max_size=30), st.integers(0, 1000),
                           max_size=20))
    def test_reconciliation_quad_always_sums(self, views):
        """Property 4 (Req 13.8): attributed + ambiguous + unattributed == total."""
        e = topics.CatalogEntry("https://x.com/one",
                                {**{d: [] for d in topics.TAXONOMY_DIMENSIONS},
                                 "services": ["bedrock"]})
        index = topics.build_slug_index([e])
        r = topics.attribute(views, index)
        assert (r.attributed_views + r.ambiguous_views + r.unattributed_views
                == r.total_report_views)


# ─── rollup: guards, coverage, scalars, artifacts ────────────────────────────

class TestGuards:
    def test_current_month_is_not_completed(self):
        assert not is_month_completed("2026-08", NOW)

    def test_previous_month_is_completed(self):
        assert is_month_completed("2026-07", NOW)

    def test_parse_scalar_zero_on_empty(self):
        assert parse_scalar([["header"]], "m") == 0  # Req 11.3

    def test_parse_scalar_rejects_garbage_and_negatives(self):
        with pytest.raises(QueryError):
            parse_scalar([["h"], ["abc"]], "m")
        with pytest.raises(QueryError):
            parse_scalar([["h"], ["-1"]], "m")


class TestCoverage:
    def test_complete_when_raw_data_predates_month(self):
        c = compute_coverage("2026-06", "2026-05-11", "2026-06-01", "2026-06-30")
        assert c["status"] == "complete"

    def test_partial_when_earliest_data_inside_month(self):
        c = compute_coverage("2026-05", "2026-05-11", "2026-05-11", "2026-05-31")
        assert c["status"] == "partial"
        assert c["earliest_source_date"] == "2026-05-11"

    def test_partial_when_month_has_no_records(self):
        c = compute_coverage("2026-04", "2026-05-11", None, None)
        assert c["status"] == "partial"
        assert c["earliest_source_date"] is None


class FakeRunner:
    """Replays canned responses in run_month's fixed query order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.bytes_scanned = 0
        self.queries_issued = 0

    def execute(self, sql, database=None):
        self.queries_issued += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        import io as _io
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": _io.BytesIO(self.objects[Key])}

    def get_paginator(self, name):
        objects = self.objects

        class _P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in sorted(objects)
                                    if k.startswith(Prefix)]}
        return _P()


def _happy_responses(slug=None, views=5):
    """9 scalar/ranked metric responses + slug + 2 coverage responses."""
    metric_rows = {
        "total_pageviews_cf": [["h"], ["100"]],
        "unique_visitors_cf": [["h"], ["40"]],
        "top_pages_cf": [["h", "hits"], ["/index.html", "60"]],
        "total_sessions_events": [["h"], ["20"]],
        "pageviews_events": [["h"], ["50"]],
        "report_clicks": [["h", "clicks"], ["some-slug", "7"]],
        "filter_usage": [["d", "t", "u"], ["services", "bedrock", "9"]],
        "pdf_exports": [["h"], ["2"]],
        "about_opens": [["h"], ["1"]],
    }
    ordered = [metric_rows[name] for name in queries.METRIC_QUERIES]
    slug_rows = [["path", "views"]]
    if slug:
        slug_rows.append([f"/reports/{slug}.html", str(views)])
    ordered.append(slug_rows)
    ordered.append([["g", "wmin", "wmax"], ["2026-05-11", "2026-06-01", "2026-06-30"]])
    ordered.append([["g", "wmin", "wmax"], ["2026-05-12", "2026-06-02", "2026-06-29"]])
    return ordered


def _index_for(link, **tag_kwargs):
    tags = {d: [] for d in topics.TAXONOMY_DIMENSIONS}
    tags.update(tag_kwargs)
    return topics.build_slug_index([topics.CatalogEntry(link, tags)])


class TestRunMonth:
    def test_happy_path_writes_json_then_csv(self):
        link = "https://x.com/one"
        s3 = FakeS3()
        runner = FakeRunner(_happy_responses(slug=topics.new_slug(link), views=5))
        result = run_month("2026-06", runner=runner, s3=s3, logs_bucket="b",
                           slug_index=_index_for(link, services=["bedrock"]), now=NOW)
        assert result.ok and result.coverage_status == "complete"
        assert result.keys_written == ["rollups/2026-06.json", "rollups/2026-06.csv"]
        artifact = json.loads(s3.objects["rollups/2026-06.json"])
        assert artifact["scalars"]["total_pageviews_cf"] == 100
        assert artifact["topics"]["metrics"] == [
            {"dimension": "services", "tag": "bedrock", "views": 5}]
        assert runner.queries_issued == 12  # Property 10 (Req 9.1)

    def test_ineligible_month_rejected_before_queries(self):
        runner = FakeRunner([])
        result = run_month("2026-08", runner=runner, s3=FakeS3(), logs_bucket="b",
                           slug_index={}, now=NOW)
        assert result.rejected and not result.ok
        assert runner.queries_issued == 0

    def test_invalid_month_rejected_before_queries(self):
        runner = FakeRunner([])
        result = run_month("2026-6", runner=runner, s3=FakeS3(), logs_bucket="b",
                           slug_index={}, now=NOW)
        assert result.rejected
        assert runner.queries_issued == 0

    def test_failed_query_writes_nothing(self):
        """Property 6 (Req 11.6): a failed month leaves no artifact."""
        responses = _happy_responses()
        responses[3] = QueryError("boom")
        s3 = FakeS3()
        result = run_month("2026-06", runner=FakeRunner(responses), s3=s3,
                           logs_bucket="b", slug_index={}, now=NOW)
        assert not result.ok and not result.rejected
        assert s3.objects == {}

    def test_zero_data_month_still_writes_partial_rollup(self):
        """Req 11.1 / 4.11: empty month -> zeros, empty lists, partial."""
        zero_rows = {
            name: [["h"], ["0"]] if name in queries.SCALAR_METRICS else [["h", "c"]]
            for name in queries.METRIC_QUERIES
        }
        responses = [zero_rows[name] for name in queries.METRIC_QUERIES]
        responses.append([["path", "views"]])
        responses.append([["g", "w", "w"], ["", "", ""]])
        responses.append([["g", "w", "w"], ["", "", ""]])
        s3 = FakeS3()
        result = run_month("2026-04", runner=FakeRunner(responses), s3=s3,
                           logs_bucket="b", slug_index={}, now=NOW)
        assert result.ok and result.coverage_status == "partial"
        artifact = json.loads(s3.objects["rollups/2026-04.json"])
        assert all(artifact["scalars"][m] == 0 for m in queries.SCALAR_METRICS)
        assert all(artifact["ranked"][m] == [] for m in queries.RANKED_METRICS)
        assert "No data" in s3.objects["rollups/2026-04.csv"].decode()

    def test_rerun_replaces_same_keys(self):
        """Property 3 (Req 4.3): re-run overwrites, keys are fixed."""
        s3 = FakeS3()
        for _ in range(2):
            result = run_month("2026-06", runner=FakeRunner(_happy_responses()),
                               s3=s3, logs_bucket="b", slug_index={}, now=NOW)
            assert result.ok
        assert set(s3.objects) == {"rollups/2026-06.json", "rollups/2026-06.csv"}


class TestArtifacts:
    def _artifact(self):
        attribution = topics.TopicAttribution(
            metrics={("services", "bedrock"): 12}, total_report_views=15,
            attributed_views=12, ambiguous_views=3,
            ambiguous=[{"slug": "s", "matches": 2, "views": 3}])
        return build_month_json(
            "2026-06", "2026-08-17T00:00:00Z",
            {"status": "complete", "earliest_source_date": "2026-06-01",
             "latest_source_date": "2026-06-30"},
            {m: 1 for m in queries.SCALAR_METRICS},
            {m: [] for m in queries.RANKED_METRICS},
            attribution)

    def test_csv_section_order(self):
        """Req 2.8 / 2.10: fixed section sequence, month in header."""
        text = render_month_csv(self._artifact())
        positions = [text.index(s) for s in (
            "Month: 2026-06", "=== Summary Metrics ===",
            "=== Top Pages (CloudFront) ===", "=== Top Report Clicks ===",
            "=== Filter/Tag Usage ===", "=== Topic Visits ===")]
        assert positions == sorted(positions)

    def test_no_forbidden_source_columns_in_artifacts(self):
        """Property 8 (Req 7.2): decidable by inspecting the artifact alone."""
        artifact = self._artifact()
        blob = json.dumps(artifact) + render_month_csv(artifact)
        for forbidden in ("c_ip", "x_forwarded_for", "cs_user_agent",
                          "cs_cookie", "source_ip", "user_agent", "session_id"):
            assert forbidden not in blob

    def test_scalars_are_plain_ints(self):
        artifact = self._artifact()
        for m in queries.SCALAR_METRICS:
            assert isinstance(artifact["scalars"][m], int)

    def test_keys_are_computable_from_month_alone(self):
        assert month_json_key("2026-06") == "rollups/2026-06.json"
        assert month_csv_key("2026-06") == "rollups/2026-06.csv"
        assert aggregate.rollup.ALL_TIME_KEY == "rollups/all-time.csv"

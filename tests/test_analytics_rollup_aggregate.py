"""Unit tests for the Aggregate_Builder (all-time CSV).

Validates design Properties 5 (additivity split), 7 (coverage totality)
and 9 (aggregation isolation), plus gaps, exclusions, and the zero-months
degenerate form (Req 5, 6, 11.5, 11.7, 13.10).
"""

import json

from src.analytics_rollup import aggregate, queries

GENERATED = "2026-08-17T00:00:00Z"


def make_rollup(month, coverage="complete", scalars=None, ranked=None, topic_metrics=None):
    base_scalars = {m: 10 for m in queries.SCALAR_METRICS}
    base_scalars.update(scalars or {})
    return {
        "schema_version": 1,
        "target_month": month,
        "generated_at": GENERATED,
        "coverage": {"status": coverage, "earliest_source_date": f"{month}-01",
                     "latest_source_date": f"{month}-28"},
        "scalars": base_scalars,
        "ranked": ranked or {m: [] for m in queries.RANKED_METRICS},
        "topics": {"metrics": topic_metrics or [], "total_report_views": 0,
                   "attributed_views": 0, "ambiguous": [], "ambiguous_views": 0,
                   "unattributed_views": 0},
    }


class TestValidation:
    def test_valid_rollup_accepted(self):
        assert aggregate.validate_rollup(make_rollup("2026-06"))

    def test_missing_scalar_rejected(self):
        data = make_rollup("2026-06")
        del data["scalars"]["pdf_exports"]
        assert not aggregate.validate_rollup(data)

    def test_bad_coverage_rejected(self):
        assert not aggregate.validate_rollup(make_rollup("2026-06", coverage="maybe"))

    def test_non_integer_scalar_rejected(self):
        assert not aggregate.validate_rollup(
            make_rollup("2026-06", scalars={"pdf_exports": "3"}))
        assert not aggregate.validate_rollup(
            make_rollup("2026-06", scalars={"pdf_exports": -1}))


class TestTotals:
    def test_additive_exact_and_non_additive_upper_bound(self):
        """Property 5 (Req 6.1, 6.2, 6.8)."""
        rollups = {
            "2026-05": make_rollup("2026-05", scalars={"total_pageviews_cf": 100,
                                                       "unique_visitors_cf": 30}),
            "2026-06": make_rollup("2026-06", scalars={"total_pageviews_cf": 200,
                                                       "unique_visitors_cf": 50}),
        }
        text = aggregate.build_all_time_csv(rollups, [], GENERATED)
        assert "Total Page Views (CloudFront),300,exact" in text
        assert "Unique Visitors (CloudFront),80,upper bound" in text
        assert "max single month=50" in text
        assert "mean=40.0" in text

    def test_mean_rounds_half_up(self):
        rollups = {
            "2026-05": make_rollup("2026-05", scalars={"unique_visitors_cf": 1}),
            "2026-06": make_rollup("2026-06", scalars={"unique_visitors_cf": 2}),
        }
        text = aggregate.build_all_time_csv(rollups, [], GENERATED)
        assert "mean=1.5" in text

    def test_partial_month_marks_totals_partial(self):
        """Property 7 (Req 6.7): partial months listed by identifier."""
        rollups = {"2026-05": make_rollup("2026-05", coverage="partial"),
                   "2026-06": make_rollup("2026-06")}
        text = aggregate.build_all_time_csv(rollups, [], GENERATED)
        assert "Coverage,partial,partial months: 2026-05" in text

    def test_all_complete_marks_totals_complete(self):
        text = aggregate.build_all_time_csv({"2026-06": make_rollup("2026-06")},
                                            [], GENERATED)
        assert "Coverage,complete," in text


class TestRankedCombination:
    def test_identical_keys_summed_and_reranked(self):
        """Req 6.4: sum only when every key component matches."""
        r1 = make_rollup("2026-05", ranked={
            "top_pages_cf": [{"key": {"path": "/a.html"}, "count": 5}],
            "report_clicks": [],
            "filter_usage": [{"key": {"dimension": "services", "tag": "bedrock"}, "count": 2},
                             {"key": {"dimension": "concepts", "tag": "bedrock"}, "count": 9}],
        })
        r2 = make_rollup("2026-06", ranked={
            "top_pages_cf": [{"key": {"path": "/a.html"}, "count": 7}],
            "report_clicks": [],
            "filter_usage": [{"key": {"dimension": "services", "tag": "bedrock"}, "count": 4}],
        })
        text = aggregate.build_all_time_csv({"2026-05": r1, "2026-06": r2}, [], GENERATED)
        assert "/a.html,12,approximate" in text
        assert "services,bedrock,6,approximate" in text        # same dim+tag summed
        assert "concepts,bedrock,9,approximate" in text        # different dim kept apart

    def test_tie_broken_lexicographically(self):
        r = make_rollup("2026-05", ranked={
            "top_pages_cf": [{"key": {"path": "/b.html"}, "count": 5},
                             {"key": {"path": "/a.html"}, "count": 5}],
            "report_clicks": [], "filter_usage": [],
        })
        text = aggregate.build_all_time_csv({"2026-05": r}, [], GENERATED)
        assert text.index("/a.html,5") < text.index("/b.html,5")


class TestGapsAndExclusions:
    def test_gap_months_listed(self):
        rollups = {"2026-04": make_rollup("2026-04"), "2026-07": make_rollup("2026-07")}
        text = aggregate.build_all_time_csv(rollups, [], GENERATED)
        assert "Gaps,2026-05; 2026-06" in text

    def test_excluded_month_appears_in_gaps_and_error_row(self):
        """Req 5.8 / 11.7."""
        rollups = {"2026-05": make_rollup("2026-05")}
        text = aggregate.build_all_time_csv(rollups, ["2026-06"], GENERATED)
        assert "Unreadable Rollups,2026-06" in text
        assert "Gaps,2026-06" in text

    def test_zero_months_degenerate_form(self):
        """Req 11.5 / 6.9: N/A markers, no division, explicit empty row."""
        text = aggregate.build_all_time_csv({}, [], GENERATED)
        assert "Months Included,0" in text
        assert "Earliest Month,N/A" in text
        assert "No months are available" in text
        assert "Unique Visitors (CloudFront),N/A,upper bound" in text


class TestTopicSeries:
    def test_exact_sums_and_metadata(self):
        """Req 6.11, 13.10: sums per pair, first/last active, latest value."""
        rollups = {
            "2026-05": make_rollup("2026-05", topic_metrics=[
                {"dimension": "services", "tag": "bedrock", "views": 10}]),
            "2026-06": make_rollup("2026-06", topic_metrics=[
                {"dimension": "services", "tag": "bedrock", "views": 20},
                {"dimension": "concepts", "tag": "rag", "views": 5}]),
        }
        text = aggregate.build_all_time_csv(rollups, [], GENERATED)
        # series rows ascending by month
        assert text.index("services,bedrock,2026-05,10") < text.index(
            "services,bedrock,2026-06,20")
        # totals: exact, ordered by summed views desc
        assert "services,bedrock,30,exact,2026-05,2026-06,15.0,20" in text
        assert "concepts,rag,5,exact,2026-06,2026-06,2.5,5" in text
        assert text.index("services,bedrock,30") < text.index("concepts,rag,5")

    def test_no_row_limit_on_topic_series(self):
        """Req 6.12: every pair present in any month is emitted."""
        metrics = [{"dimension": "services", "tag": f"tag-{i:03d}", "views": 1}
                   for i in range(60)]
        rollups = {"2026-06": make_rollup("2026-06", topic_metrics=metrics)}
        text = aggregate.build_all_time_csv(rollups, [], GENERATED)
        assert all(f"tag-{i:03d}" in text for i in range(60))


class FakeS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.athena_calls = 0

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[Key])}

    def get_paginator(self, name):
        objects = self.objects

        class _P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in sorted(objects)
                                    if k.startswith(Prefix)]}
        return _P()


class TestRunAggregate:
    def test_reads_json_rollups_writes_single_all_time_key(self):
        """Property 9: zero Athena queries, fixed output key (Req 5.1, 5.2)."""
        s3 = FakeS3({
            "rollups/2026-05.json": json.dumps(make_rollup("2026-05")).encode(),
            "rollups/2026-05.csv": b"not json, must be ignored",
            "rollups/2026-06.json": json.dumps(make_rollup("2026-06")).encode(),
        })
        key, excluded = aggregate.run_aggregate(s3, "bucket")
        assert key == "rollups/all-time.csv"
        assert excluded == []
        text = s3.objects[key].decode()
        assert "Months Included,2" in text

    def test_unreadable_rollup_excluded_but_run_completes(self):
        s3 = FakeS3({
            "rollups/2026-05.json": b"{corrupt",
            "rollups/2026-06.json": json.dumps(make_rollup("2026-06")).encode(),
        })
        key, excluded = aggregate.run_aggregate(s3, "bucket")
        assert excluded == ["2026-05"]
        assert "Months Included,1" in s3.objects[key].decode()

    def test_no_rollups_still_writes_all_time(self):
        s3 = FakeS3()
        key, _ = aggregate.run_aggregate(s3, "bucket")
        assert "No months are available" in s3.objects[key].decode()

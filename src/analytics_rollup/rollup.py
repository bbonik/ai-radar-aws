"""Rollup_Job core: run one Target_Month, write its Monthly_Rollup.

All-or-nothing per month (Req 11.6): any failed query or unparsable scalar
fails that month and nothing is written for it, so a stored scalar of 0
always means "zero matching source records". Fixed S3 keys make every
re-run an idempotent replace (Req 4.3).
"""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.analytics_rollup import queries, topics

ROLLUP_PREFIX = "rollups/"
ALL_TIME_KEY = "rollups/all-time.csv"
SCHEMA_VERSION = 1

# Per-invocation Athena scan-volume ceiling: warn, don't fail (Req 9.7).
SCAN_WARN_BYTES = 10 * 1024**3

# Human-readable labels, matching the existing report output (Req 2.10).
SCALAR_LABELS = {
    "total_pageviews_cf": "Total Page Views (CloudFront)",
    "unique_visitors_cf": "Unique Visitors (CloudFront)",
    "total_sessions_events": "Total Sessions (Custom Events)",
    "pageviews_events": "Page Views (Custom Events)",
    "pdf_exports": "PDF Exports",
    "about_opens": "About Modal Opens",
}

RANKED_SECTION_TITLES = {
    "top_pages_cf": "=== Top Pages (CloudFront) ===",
    "report_clicks": "=== Top Report Clicks ===",
    "filter_usage": "=== Filter/Tag Usage ===",
}
RANKED_COLUMNS = {
    "top_pages_cf": ["Page", "Views"],
    "report_clicks": ["Report Slug", "Clicks"],
    "filter_usage": ["Dimension", "Tag", "Uses"],
}


def month_json_key(target_month: str) -> str:
    return f"{ROLLUP_PREFIX}{target_month}.json"


def month_csv_key(target_month: str) -> str:
    return f"{ROLLUP_PREFIX}{target_month}.csv"


class QueryError(Exception):
    """An Athena statement failed or returned an unusable result."""


class AthenaRunner:
    """Executes Athena statements, tracking total bytes scanned (Req 9.7)."""

    def __init__(self, athena_client, output_location: str,
                 workgroup: str = queries.ATHENA_WORKGROUP, poll_seconds: float = 1.0):
        self._athena = athena_client
        self._output_location = output_location
        self._workgroup = workgroup
        self._poll_seconds = poll_seconds
        self.bytes_scanned = 0
        self.queries_issued = 0

    def execute(self, sql: str, database: str | None = None) -> list[list[str]]:
        """Run one statement to completion; return all rows (incl. header)."""
        params = {
            "QueryString": sql,
            "ResultConfiguration": {"OutputLocation": self._output_location},
            "WorkGroup": self._workgroup,
        }
        if database:
            params["QueryExecutionContext"] = {"Database": database}
        execution_id = self._athena.start_query_execution(**params)["QueryExecutionId"]
        self.queries_issued += 1

        while True:
            result = self._athena.get_query_execution(QueryExecutionId=execution_id)
            state = result["QueryExecution"]["Status"]["State"]
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            time.sleep(self._poll_seconds)

        stats = result["QueryExecution"].get("Statistics", {})
        self.bytes_scanned += stats.get("DataScannedInBytes", 0)

        if state != "SUCCEEDED":
            reason = result["QueryExecution"]["Status"].get("StateChangeReason", "Unknown")
            raise QueryError(f"Athena query {state}: {reason}")

        rows: list[list[str]] = []
        paginator = self._athena.get_paginator("get_query_results")
        for page in paginator.paginate(QueryExecutionId=execution_id):
            for row in page["ResultSet"]["Rows"]:
                rows.append([col.get("VarCharValue", "") for col in row["Data"]])
        return rows


def ensure_tables(runner: AthenaRunner, logs_bucket: str) -> None:
    """Exactly three create-if-not-exists statements, checked (Req 2.6)."""
    runner.execute(queries.create_database_sql())
    runner.execute(queries.cloudfront_table_sql(logs_bucket))
    runner.execute(queries.custom_events_table_sql(logs_bucket))


def parse_scalar(rows: list[list[str]], metric: str) -> int:
    """Non-negative integer scalar or QueryError — never a placeholder (Req 11.3, 11.6)."""
    if len(rows) <= 1:
        return 0
    value = rows[1][0]
    try:
        n = int(value)
    except (ValueError, TypeError) as exc:
        raise QueryError(f"{metric}: scalar {value!r} is not an integer") from exc
    if n < 0:
        raise QueryError(f"{metric}: scalar {n} is negative")
    return n


def ranked_entries(rows: list[list[str]], metric: str) -> list[dict]:
    """Athena rows (header + data) -> machine-readable entries (Req 2.7)."""
    entries = []
    for row in rows[1:]:
        try:
            count = int(row[-1])
        except (ValueError, TypeError) as exc:
            raise QueryError(f"{metric}: count {row[-1]!r} is not an integer") from exc
        if count < 0:
            raise QueryError(f"{metric}: count {count} is negative")
        if metric == "top_pages_cf":
            key = {"path": topics.truncate_key(row[0].split("?", 1)[0])}
        elif metric == "report_clicks":
            key = {"report_slug": topics.truncate_key(row[0])}
        else:  # filter_usage
            key = {"dimension": topics.truncate_key(row[0]),
                   "tag": topics.truncate_key(row[1])}
        entries.append({"key": key, "count": count})
    return entries


def is_month_completed(target_month: str, now: datetime) -> bool:
    """Only completed months are eligible (Req 4.6)."""
    return target_month < now.strftime("%Y-%m")


def compute_coverage(target_month: str, global_earliest: str | None,
                     win_min: str | None, win_max: str | None) -> dict:
    """Coverage_Status per Req 2.11 / 4.5 / 4.11.

    complete  <=> the month yielded >=1 raw record AND the earliest surviving
                  raw record (across both sources) is at or before the first
                  instant of the month. Otherwise partial.
    """
    month_start = f"{target_month}-01"
    if win_min is None:  # no surviving raw records in the month (Req 4.11)
        return {"status": "partial", "earliest_source_date": None, "latest_source_date": None}
    complete = global_earliest is not None and global_earliest <= month_start
    return {
        "status": "complete" if complete else "partial",
        "earliest_source_date": win_min,
        "latest_source_date": win_max,
    }


def _min_defined(*values: str | None) -> str | None:
    defined = [v for v in values if v]
    return min(defined) if defined else None


def _max_defined(*values: str | None) -> str | None:
    defined = [v for v in values if v]
    return max(defined) if defined else None


def _bounds(rows: list[list[str]]) -> tuple[str | None, str | None, str | None]:
    """(global_min, win_min, win_max) from a coverage query result."""
    if len(rows) <= 1:
        return None, None, None
    data = rows[1]
    return tuple(v if v else None for v in data[:3])  # type: ignore[return-value]


@dataclass
class MonthResult:
    """Outcome of one requested Target_Month (Req 10.3, 10.4)."""

    target_month: str
    ok: bool
    rejected: bool = False            # ineligible, distinct from failed
    coverage_status: str | None = None
    keys_written: list[str] = field(default_factory=list)
    error: str | None = None
    metrics_computed: int = 0
    csv_text: str | None = None       # for --output in month mode (Req 10.8)


def run_month(target_month: str, *, runner: AthenaRunner, s3, logs_bucket: str,
              slug_index: dict, now: datetime) -> MonthResult:
    """Produce and store the Monthly_Rollup for one Target_Month."""
    started = time.monotonic()

    if not queries.is_valid_month(target_month):
        return MonthResult(target_month, ok=False, rejected=True,
                           error=f"not a valid YYYY-MM Target_Month: {target_month!r}")
    if not is_month_completed(target_month, now):
        return MonthResult(target_month, ok=False, rejected=True,
                           error="only completed months are eligible")

    start, end = queries.window_for_month(target_month)
    try:
        # 9 metric queries
        raw: dict[str, list[list[str]]] = {}
        for name, build in queries.METRIC_QUERIES.items():
            raw[name] = runner.execute(build(start, end), queries.ATHENA_DATABASE)

        # 10th: per-slug report views; 11th-12th: coverage bounds
        slug_rows = runner.execute(
            queries.report_pageviews_by_slug_sql(start, end), queries.ATHENA_DATABASE)
        cf_bounds = _bounds(runner.execute(
            queries.coverage_bounds_cf_sql(start, end), queries.ATHENA_DATABASE))
        ev_bounds = _bounds(runner.execute(
            queries.coverage_bounds_events_sql(start, end), queries.ATHENA_DATABASE))

        scalars = {m: parse_scalar(raw[m], m) for m in queries.SCALAR_METRICS}
        ranked = {m: ranked_entries(raw[m], m) for m in queries.RANKED_METRICS}

        slug_views: dict[str, int] = {}
        for row in slug_rows[1:]:
            slug = topics.extract_report_slug(row[0])
            if slug is not None:
                try:
                    slug_views[slug] = slug_views.get(slug, 0) + int(row[1])
                except (ValueError, TypeError) as exc:
                    raise QueryError(f"report views: {row[1]!r} not an integer") from exc
        attribution = topics.attribute(slug_views, slug_index)

        coverage = compute_coverage(
            target_month,
            _min_defined(cf_bounds[0], ev_bounds[0]),
            _min_defined(cf_bounds[1], ev_bounds[1]),
            _max_defined(cf_bounds[2], ev_bounds[2]),
        )
    except QueryError as exc:
        return MonthResult(target_month, ok=False, error=str(exc))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    artifact = build_month_json(target_month, generated_at, coverage,
                                scalars, ranked, attribution)
    csv_text = render_month_csv(artifact)

    # JSON first (aggregation source of truth), then CSV.
    json_key, csv_key = month_json_key(target_month), month_csv_key(target_month)
    s3.put_object(Bucket=logs_bucket, Key=json_key,
                  Body=json.dumps(artifact, ensure_ascii=False).encode("utf-8"),
                  ContentType="application/json")
    s3.put_object(Bucket=logs_bucket, Key=csv_key,
                  Body=csv_text.encode("utf-8"), ContentType="text/csv")

    duration = round(time.monotonic() - started, 1)
    print(json.dumps({
        "component": "analytics_rollup", "target_month": target_month,
        "outcome": "success", "coverage_status": coverage["status"],
        "metrics_computed": 9, "duration_seconds": duration,
    }))
    return MonthResult(target_month, ok=True, coverage_status=coverage["status"],
                       keys_written=[json_key, csv_key], metrics_computed=9,
                       csv_text=csv_text)


def build_month_json(target_month: str, generated_at: str, coverage: dict,
                     scalars: dict, ranked: dict, attribution) -> dict:
    """Machine-readable Monthly_Rollup (Req 2.5, 2.7, 13.5, 13.8)."""
    metrics = [
        {"dimension": dim, "tag": tag, "views": views}
        for (dim, tag), views in sorted(attribution.metrics.items())
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "target_month": target_month,
        "generated_at": generated_at,
        "coverage": coverage,
        "scalars": scalars,
        "ranked": ranked,
        "topics": {
            "metrics": metrics,
            "total_report_views": attribution.total_report_views,
            "attributed_views": attribution.attributed_views,
            "ambiguous": attribution.ambiguous,
            "ambiguous_views": attribution.ambiguous_views,
            "unattributed_views": attribution.unattributed_views,
        },
    }


def _ranked_csv_rows(entries: list[dict], metric: str) -> list[list]:
    rows = []
    for e in entries:
        k = e["key"]
        if metric == "filter_usage":
            rows.append([k["dimension"], k["tag"], e["count"]])
        elif metric == "top_pages_cf":
            rows.append([k["path"], e["count"]])
        else:
            rows.append([k["report_slug"], e["count"]])
    return rows


def render_month_csv(artifact: dict) -> str:
    """Human-readable Monthly_Rollup CSV (Req 2.8, 2.10, 11.2)."""
    coverage = artifact["coverage"]
    header = [
        ["AI Radar AWS - Analytics Report"],
        [f"Month: {artifact['target_month']}"],
        [f"Generated: {artifact['generated_at']}"],
        [f"Coverage: {coverage['status']}"],
        [
            "Source dates: "
            + (f"{coverage['earliest_source_date']} to {coverage['latest_source_date']}"
               if coverage["earliest_source_date"] else "none observed")
        ],
    ]
    return render_report_csv(header, artifact["scalars"], artifact["ranked"],
                             artifact["topics"])


def render_report_csv(header_rows: list[list], scalars: dict, ranked: dict,
                      topic_data: dict) -> str:
    """Shared section renderer for month rollups and day-scoped reports."""
    out = io.StringIO()
    writer = csv.writer(out)
    for row in header_rows:
        writer.writerow(row)
    writer.writerow([])

    writer.writerow(["=== Summary Metrics ==="])
    writer.writerow(["Metric", "Value"])
    for metric in queries.SCALAR_METRICS:
        writer.writerow([SCALAR_LABELS[metric], scalars[metric]])
    writer.writerow([])

    for metric in queries.RANKED_METRICS:
        writer.writerow([RANKED_SECTION_TITLES[metric]])
        writer.writerow(RANKED_COLUMNS[metric])
        rows = _ranked_csv_rows(ranked[metric], metric)
        if rows:
            writer.writerows(rows)
        else:
            writer.writerow(["No data"])  # Req 11.2
        writer.writerow([])

    writer.writerow(["=== Topic Visits ==="])
    writer.writerow(["Dimension", "Tag", "Views"])
    metrics = topic_data["metrics"]
    if metrics:
        for m in metrics:
            writer.writerow([m["dimension"], m["tag"], m["views"]])
    else:
        writer.writerow(["No data"])
    writer.writerow(["Total report views considered", topic_data["total_report_views"]])
    writer.writerow(["Attributed views", topic_data["attributed_views"]])
    writer.writerow(["Ambiguous views", topic_data["ambiguous_views"]])
    writer.writerow(["Unattributed views", topic_data["unattributed_views"]])
    for amb in topic_data["ambiguous"]:
        writer.writerow(["Ambiguous slug", amb["slug"], amb["matches"], amb["views"]])

    return out.getvalue()

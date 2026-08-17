#!/usr/bin/env python3
"""Generate analytics reports and monthly rollups from CloudFront logs + events.

Two modes sharing one query implementation (src/analytics_rollup/):

Day-scoped (default, backward compatible):
    python scripts/analytics_report.py                # last 7 days, stdout
    python scripts/analytics_report.py --days 30
    python scripts/analytics_report.py --output report.csv

Month-scoped (writes permanent rollups under s3://<logs-bucket>/rollups/):
    python scripts/analytics_report.py --month 2026-06
    python scripts/analytics_report.py --month 2026-05 --to 2026-07   # backfill

The two modes are mutually exclusive. Month mode replaces any existing
rollup for the same month (idempotent) and regenerates rollups/all-time.csv.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3

from scripts._common import STACK_NAME, deployed_region, find_stack_bucket
from src.analytics_rollup import aggregate, queries, topics
from src.analytics_rollup.rollup import (
    AthenaRunner,
    ensure_tables,
    parse_scalar,
    ranked_entries,
    render_report_csv,
    run_month,
)

REGION = deployed_region()
CATALOG_KEY = "database/announcements.csv"
MAX_MONTHS_PER_INVOCATION = 24


def build_clients():
    s3 = boto3.client("s3", region_name=REGION)
    athena = boto3.client("athena", region_name=REGION)
    return s3, athena


def load_slug_index(s3, data_bucket):
    """Read the announcement catalog once per invocation (Req 9.9)."""
    body = s3.get_object(Bucket=data_bucket, Key=CATALOG_KEY)["Body"].read().decode("utf-8")
    return topics.build_slug_index(topics.load_catalog(body))


def run_day_mode(days, output_file):
    """Day-scoped report: original behaviour + Topic Visits + 30-row lists."""
    print("AI Radar AWS - Analytics Report Generator")
    print("=" * 50)
    print("\nRetrieving stack configuration...")
    logs_bucket = find_stack_bucket("LogsBucket", REGION)
    data_bucket = find_stack_bucket("DataBucket", REGION)
    print(f"  Logs bucket: {logs_bucket}")

    s3, athena = build_clients()
    runner = AthenaRunner(athena, f"s3://{logs_bucket}/athena-results/")

    print("Setting up Athena database and tables...")
    ensure_tables(runner, logs_bucket)

    now = datetime.now(timezone.utc)
    start, end = queries.window_for_days(days, now)

    print(f"\nRunning analytics queries (last {days} days)...")
    scalars, ranked = {}, {}
    for name, build in queries.METRIC_QUERIES.items():
        print(f"  Running: {name}...")
        rows = runner.execute(build(start, end), queries.ATHENA_DATABASE)
        if name in queries.SCALAR_METRICS:
            scalars[name] = parse_scalar(rows, name)
        else:
            ranked[name] = ranked_entries(rows, name)

    print("  Running: report views by slug (topics)...")
    slug_rows = runner.execute(
        queries.report_pageviews_by_slug_sql(start, end), queries.ATHENA_DATABASE)
    slug_views = {}
    for row in slug_rows[1:]:
        slug = topics.extract_report_slug(row[0])
        if slug is not None:
            slug_views[slug] = slug_views.get(slug, 0) + int(row[1])
    attribution = topics.attribute(slug_views, load_slug_index(s3, data_bucket))

    header = [
        ["AI Radar AWS - Analytics Report"],
        [f"Period: Last {days} days"],
        [f"Generated: {now.isoformat()}"],
    ]
    topic_data = {
        "metrics": [{"dimension": d, "tag": t, "views": v}
                    for (d, t), v in sorted(attribution.metrics.items())],
        "total_report_views": attribution.total_report_views,
        "attributed_views": attribution.attributed_views,
        "ambiguous": attribution.ambiguous,
        "ambiguous_views": attribution.ambiguous_views,
        "unattributed_views": attribution.unattributed_views,
    }
    csv_content = render_report_csv(header, scalars, ranked, topic_data)

    if output_file:
        with open(output_file, "w") as f:
            f.write(csv_content)
        print(f"\nReport saved to: {output_file}")
    else:
        print("\n" + csv_content)
    print("\nDone.")
    return 0


def run_month_mode(start_month, end_month, output_file):
    """Month-scoped rollup / backfill (Req 4, 10.3, 10.4, 10.7, 10.8)."""
    # All validation happens before any Athena statement (Req 4.9, 4.10, 9.6).
    try:
        months = queries.month_range(start_month, end_month or start_month)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 2
    if len(months) > MAX_MONTHS_PER_INVOCATION:
        print(f"Error: range covers {len(months)} months; "
              f"at most {MAX_MONTHS_PER_INVOCATION} per invocation are supported.")
        return 2
    if output_file and len(months) > 1:
        print("Error: --output requires exactly one Target_Month.")
        return 2

    print("AI Radar AWS - Monthly Rollup")
    print("=" * 50)
    logs_bucket = find_stack_bucket("LogsBucket", REGION)
    data_bucket = find_stack_bucket("DataBucket", REGION)
    print(f"  Stack: {STACK_NAME}  Logs bucket: {logs_bucket}")

    s3, athena = build_clients()
    runner = AthenaRunner(athena, f"s3://{logs_bucket}/athena-results/")

    try:
        slug_index = load_slug_index(s3, data_bucket)
    except Exception as exc:  # noqa: BLE001 — catalog unreadable fails the run (Req 13.12)
        print(f"Error: cannot read catalog {CATALOG_KEY}: {exc}")
        return 1

    ensure_tables(runner, logs_bucket)
    now = datetime.now(timezone.utc)

    results = []
    for month in months:  # ascending (Req 4.2); rejected months don't stop the rest
        print(f"\nProcessing {month}...")
        result = run_month(month, runner=runner, s3=s3, logs_bucket=logs_bucket,
                           slug_index=slug_index, now=now)
        results.append(result)
        if result.ok:
            print(f"  {month}: coverage={result.coverage_status}")
            for key in result.keys_written:
                print(f"  wrote s3://{logs_bucket}/{key}")
            if output_file and result.csv_text:
                with open(output_file, "w") as f:
                    f.write(result.csv_text)
                print(f"  wrote {output_file}")
        else:
            kind = "rejected" if result.rejected else "FAILED"
            print(f"  {month}: {kind} — {result.error}")

    # All_Time_CSV regenerates exactly once per invocation (Req 4.7, 4.8).
    all_time_key, excluded = aggregate.run_aggregate(s3, logs_bucket)
    print(f"\nRegenerated s3://{logs_bucket}/{all_time_key}")
    if excluded:
        print(f"  WARNING: excluded unreadable rollups: {', '.join(excluded)}")
    if runner.bytes_scanned > 10 * 1024**3:
        print(f"  WARNING: scanned {runner.bytes_scanned} bytes "
              f"across {runner.queries_issued} queries (over 10 GiB ceiling)")

    succeeded = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok and not r.rejected)
    rejected = sum(1 for r in results if r.rejected)
    print(f"\nSummary: {succeeded} succeeded, {failed} failed, {rejected} rejected "
          f"of {len(results)} requested.")
    return 1 if failed or rejected else 0


def main():
    parser = argparse.ArgumentParser(
        description="Generate AI Radar AWS analytics report or monthly rollup")
    parser.add_argument("--days", type=int, default=None,
                        help="Day-scoped mode: number of days to analyze (default: 7)")
    parser.add_argument("--output", type=str, default="",
                        help="Output CSV file path (default: stdout / S3 only)")
    parser.add_argument("--month", type=str, default=None,
                        help="Month-scoped mode: Target_Month as YYYY-MM")
    parser.add_argument("--to", type=str, default=None, dest="to_month",
                        help="Inclusive end of a Target_Month range (requires --month)")
    args = parser.parse_args()

    if args.month is not None and args.days is not None:
        print("Error: --days and --month are mutually exclusive.")
        sys.exit(2)
    if args.to_month is not None and args.month is None:
        print("Error: --to requires --month.")
        sys.exit(2)

    if args.month is not None:
        sys.exit(run_month_mode(args.month, args.to_month, args.output))
    sys.exit(run_day_mode(args.days if args.days is not None else 7, args.output))


if __name__ == "__main__":
    main()

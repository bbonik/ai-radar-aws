#!/usr/bin/env python3
"""Pipeline Health Report — shows daily run status from CloudWatch Logs.

Queries the pipeline Lambda's CloudWatch Logs for "Pipeline run complete"
entries, parses the summary JSON, and displays a daily health report.

Usage:
    python scripts/pipeline_health.py           # Last 7 days
    python scripts/pipeline_health.py --days 30 # Last 30 days
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone

import boto3

LOG_GROUP = "/aws/lambda/ai-radar-report-pipeline"
REGION = "us-east-1"


def main():
    parser = argparse.ArgumentParser(description="Pipeline health report")
    parser.add_argument("--days", type=int, default=7, help="Number of days (default: 7)")
    args = parser.parse_args()

    logs = boto3.client("logs", region_name=REGION)

    # Query for "Pipeline run complete" log entries
    start_time = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)
    end_time = int(datetime.now(timezone.utc).timestamp() * 1000)

    print(f"Pipeline Health Report (Last {args.days} days)")
    print("=" * 70)
    print()

    # Use filter_log_events to find pipeline completion summaries
    try:
        paginator = logs.get_paginator("filter_log_events")
        pages = paginator.paginate(
            logGroupName=LOG_GROUP,
            startTime=start_time,
            endTime=end_time,
            filterPattern='"Pipeline run complete"',
        )
    except logs.exceptions.ResourceNotFoundException:
        print("Error: Log group not found. Has the pipeline ever run?")
        sys.exit(1)

    runs = []
    for page in pages:
        for event in page.get("events", []):
            msg = event.get("message", "").strip()
            # Parse the JSON log entry
            try:
                data = json.loads(msg)
                if data.get("message") == "Pipeline run complete":
                    runs.append(data)
            except json.JSONDecodeError:
                continue

    if not runs:
        print("No pipeline runs found in this period.")
        return

    # Sort by timestamp
    runs.sort(key=lambda r: r.get("timestamp", ""))

    # Group by date
    runs_by_date = {}
    for run in runs:
        ts = run.get("timestamp", "")[:10]  # YYYY-MM-DD
        if ts not in runs_by_date:
            runs_by_date[ts] = []
        runs_by_date[ts].append(run)

    # Display
    total_runs = 0
    total_failed_runs = 0

    for date in sorted(runs_by_date.keys()):
        day_runs = runs_by_date[date]
        for run in day_runs:
            total_runs += 1
            summary = run.get("summary", {})
            fetched = summary.get("total_fetched", "?")
            deduped = summary.get("total_deduplicated", "?")
            relevant = summary.get("total_relevant", "?")
            ok = summary.get("total_processed_ok", 0)
            failed = summary.get("total_failed", 0)
            duration = summary.get("duration_seconds", "?")
            failed_items = summary.get("failed_items", [])

            status = "\033[32m✓\033[0m" if failed == 0 else "\033[31m✗\033[0m"
            if failed > 0:
                total_failed_runs += 1

            ts = run.get("timestamp", "")[:19]
            print(f"  {ts}  {status}  Fetched: {fetched} | Deduped: {deduped} | "
                  f"Relevant: {relevant} | OK: {ok} | Failed: {failed} | "
                  f"Duration: {duration}s")

            # Show failure details
            if failed_items:
                # Group by stage
                stages = {}
                for item in failed_items:
                    stage = item.get("stage", "unknown")
                    if stage not in stages:
                        stages[stage] = []
                    stages[stage].append(item)

                for stage, items in stages.items():
                    error_msg = items[0].get("error", "Unknown error")[:100]
                    print(f"           \033[31m└─ Stage: {stage} ({len(items)} failures)\033[0m")
                    print(f"              Error: {error_msg}")
                    if len(items) > 1:
                        for item in items[1:3]:  # Show up to 3 titles
                            title = item.get("title", "")[:60]
                            print(f"              - {title}")
                        if len(items) > 3:
                            print(f"              ... and {len(items) - 3} more")

    # Summary
    print()
    print("-" * 70)
    print(f"  Total runs: {total_runs} | "
          f"Successful: {total_runs - total_failed_runs} | "
          f"With failures: {total_failed_runs}")
    print()


if __name__ == "__main__":
    main()

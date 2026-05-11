#!/usr/bin/env python3
"""Generate analytics report from CloudFront logs and custom events.

Creates Athena tables (if needed), runs queries, and outputs a CSV report.

Usage:
    python scripts/analytics_report.py
    python scripts/analytics_report.py --days 30
    python scripts/analytics_report.py --output report.csv
"""
import argparse
import csv
import io
import sys
import time
from datetime import datetime, timedelta, timezone

import boto3


# ─── Configuration ────────────────────────────────────────────────────────────

ATHENA_DATABASE = "ai_radar_analytics"
ATHENA_WORKGROUP = "primary"
STACK_NAME = "AiRadarAwsStack"


def get_stack_outputs():
    """Retrieve bucket names from CloudFormation stack outputs."""
    cfn = boto3.client("cloudformation")
    try:
        response = cfn.describe_stacks(StackName=STACK_NAME)
    except cfn.exceptions.ClientError as e:
        print(f"Error: Could not find stack '{STACK_NAME}': {e}")
        sys.exit(1)

    outputs = {}
    for output in response["Stacks"][0].get("Outputs", []):
        outputs[output["OutputKey"]] = output["OutputValue"]

    return outputs


def get_bucket_names(outputs):
    """Extract bucket names from stack outputs or describe stack resources."""
    # Try to get from outputs first
    logs_bucket = outputs.get("LogsBucketName", "")
    data_bucket = ""

    # If not in outputs, look up via stack resources
    if not logs_bucket or not data_bucket:
        cfn = boto3.client("cloudformation")
        resources = cfn.list_stack_resources(StackName=STACK_NAME)
        for r in resources.get("StackResourceSummaries", []):
            if r["LogicalResourceId"] == "LogsBucket":
                logs_bucket = r["PhysicalResourceId"]
            elif r["LogicalResourceId"] == "DataBucket":
                data_bucket = r["PhysicalResourceId"]

    return data_bucket, logs_bucket


def run_athena_query(athena, query, output_location, database=None):
    """Execute an Athena query and wait for completion. Returns query execution ID."""
    params = {
        "QueryString": query,
        "ResultConfiguration": {"OutputLocation": output_location},
        "WorkGroup": ATHENA_WORKGROUP,
    }
    if database:
        params["QueryExecutionContext"] = {"Database": database}

    response = athena.start_query_execution(**params)
    execution_id = response["QueryExecutionId"]

    # Wait for query to complete
    while True:
        result = athena.get_query_execution(QueryExecutionId=execution_id)
        state = result["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)

    if state != "SUCCEEDED":
        reason = result["QueryExecution"]["Status"].get("StateChangeReason", "Unknown")
        print(f"  Query failed ({state}): {reason}")
        print(f"  Query: {query[:200]}...")
        return None

    return execution_id


def get_query_results(athena, execution_id):
    """Fetch all result rows from a completed Athena query."""
    rows = []
    paginator = athena.get_paginator("get_query_results")
    for page in paginator.paginate(QueryExecutionId=execution_id):
        result_set = page["ResultSet"]
        for row in result_set["Rows"]:
            rows.append([col.get("VarCharValue", "") for col in row["Data"]])
    return rows


def setup_database(athena, output_location, data_bucket, logs_bucket):
    """Create Athena database and tables if they don't exist."""
    print("Setting up Athena database and tables...")

    # Create database
    run_athena_query(
        athena,
        f"CREATE DATABASE IF NOT EXISTS {ATHENA_DATABASE}",
        output_location,
    )

    # Create CloudFront logs table (standard log format)
    cf_table_query = f"""
    CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.cloudfront_logs (
        `date` DATE,
        `time` STRING,
        x_edge_location STRING,
        sc_bytes BIGINT,
        c_ip STRING,
        cs_method STRING,
        cs_host STRING,
        cs_uri_stem STRING,
        sc_status INT,
        cs_referer STRING,
        cs_user_agent STRING,
        cs_uri_query STRING,
        cs_cookie STRING,
        x_edge_result_type STRING,
        x_edge_request_id STRING,
        x_host_header STRING,
        cs_protocol STRING,
        cs_bytes BIGINT,
        time_taken FLOAT,
        x_forwarded_for STRING,
        ssl_protocol STRING,
        ssl_cipher STRING,
        x_edge_response_result_type STRING,
        cs_protocol_version STRING,
        fle_status STRING,
        fle_encrypted_fields INT,
        c_port INT,
        time_to_first_byte FLOAT,
        x_edge_detailed_result_type STRING,
        sc_content_type STRING,
        sc_content_len BIGINT,
        sc_range_start BIGINT,
        sc_range_end BIGINT
    )
    ROW FORMAT DELIMITED
    FIELDS TERMINATED BY '\\t'
    LOCATION 's3://{logs_bucket}/cloudfront/'
    TBLPROPERTIES ('skip.header.line.count'='2')
    """
    run_athena_query(athena, cf_table_query, output_location)

    # Create custom events table (JSONL format)
    events_table_query = f"""
    CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.custom_events (
        event_type STRING,
        path STRING,
        report_slug STRING,
        tag STRING,
        dimension STRING,
        session_id STRING,
        `timestamp` STRING,
        server_timestamp STRING,
        source_ip STRING,
        user_agent STRING
    )
    ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
    LOCATION 's3://{data_bucket}/analytics/events/'
    """
    run_athena_query(athena, events_table_query, output_location)

    print("  Database and tables ready.")


def run_analytics_queries(athena, output_location, days):
    """Run analytics queries and return results as a dict."""
    date_filter = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    results = {}

    queries = {
        "total_pageviews_cf": f"""
            SELECT COUNT(*) as total_requests
            FROM {ATHENA_DATABASE}.cloudfront_logs
            WHERE date >= DATE('{date_filter}')
            AND cs_uri_stem LIKE '%.html'
            AND sc_status = 200
        """,
        "unique_visitors_cf": f"""
            SELECT COUNT(DISTINCT c_ip) as unique_ips
            FROM {ATHENA_DATABASE}.cloudfront_logs
            WHERE date >= DATE('{date_filter}')
            AND sc_status = 200
        """,
        "top_pages_cf": f"""
            SELECT cs_uri_stem, COUNT(*) as hits
            FROM {ATHENA_DATABASE}.cloudfront_logs
            WHERE date >= DATE('{date_filter}')
            AND cs_uri_stem LIKE '%.html'
            AND sc_status = 200
            GROUP BY cs_uri_stem
            ORDER BY hits DESC
            LIMIT 20
        """,
        "total_sessions_events": f"""
            SELECT COUNT(DISTINCT session_id) as sessions
            FROM {ATHENA_DATABASE}.custom_events
            WHERE server_timestamp >= '{date_filter}'
        """,
        "pageviews_events": f"""
            SELECT COUNT(*) as pageviews
            FROM {ATHENA_DATABASE}.custom_events
            WHERE event_type = 'pageview'
            AND server_timestamp >= '{date_filter}'
        """,
        "report_clicks": f"""
            SELECT report_slug, COUNT(*) as clicks
            FROM {ATHENA_DATABASE}.custom_events
            WHERE event_type = 'report_click'
            AND server_timestamp >= '{date_filter}'
            GROUP BY report_slug
            ORDER BY clicks DESC
            LIMIT 20
        """,
        "filter_usage": f"""
            SELECT dimension, tag, COUNT(*) as uses
            FROM {ATHENA_DATABASE}.custom_events
            WHERE event_type = 'filter_tag'
            AND server_timestamp >= '{date_filter}'
            GROUP BY dimension, tag
            ORDER BY uses DESC
            LIMIT 20
        """,
        "pdf_exports": f"""
            SELECT COUNT(*) as exports
            FROM {ATHENA_DATABASE}.custom_events
            WHERE event_type = 'pdf_export'
            AND server_timestamp >= '{date_filter}'
        """,
        "about_opens": f"""
            SELECT COUNT(*) as opens
            FROM {ATHENA_DATABASE}.custom_events
            WHERE event_type = 'about_open'
            AND server_timestamp >= '{date_filter}'
        """,
    }

    for name, query in queries.items():
        print(f"  Running: {name}...")
        execution_id = run_athena_query(athena, query, output_location, ATHENA_DATABASE)
        if execution_id:
            rows = get_query_results(athena, execution_id)
            results[name] = rows
        else:
            results[name] = []

    return results


def compile_report(results, days, output_file):
    """Compile query results into a CSV report."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["AI Radar AWS - Analytics Report"])
    writer.writerow([f"Period: Last {days} days"])
    writer.writerow([f"Generated: {datetime.now(timezone.utc).isoformat()}"])
    writer.writerow([])

    # Summary metrics
    writer.writerow(["=== Summary Metrics ==="])
    writer.writerow(["Metric", "Value"])

    def get_scalar(key, col=0):
        rows = results.get(key, [])
        if len(rows) > 1:
            return rows[1][col]  # Skip header row
        return "N/A"

    writer.writerow(["Total Page Views (CloudFront)", get_scalar("total_pageviews_cf")])
    writer.writerow(["Unique Visitors (CloudFront)", get_scalar("unique_visitors_cf")])
    writer.writerow(["Total Sessions (Custom Events)", get_scalar("total_sessions_events")])
    writer.writerow(["Page Views (Custom Events)", get_scalar("pageviews_events")])
    writer.writerow(["PDF Exports", get_scalar("pdf_exports")])
    writer.writerow(["About Modal Opens", get_scalar("about_opens")])
    writer.writerow([])

    # Top pages
    writer.writerow(["=== Top Pages (CloudFront) ==="])
    top_pages = results.get("top_pages_cf", [])
    if top_pages:
        for row in top_pages:
            writer.writerow(row)
    else:
        writer.writerow(["No data"])
    writer.writerow([])

    # Report clicks
    writer.writerow(["=== Top Report Clicks ==="])
    report_clicks = results.get("report_clicks", [])
    if report_clicks:
        for row in report_clicks:
            writer.writerow(row)
    else:
        writer.writerow(["No data"])
    writer.writerow([])

    # Filter usage
    writer.writerow(["=== Filter/Tag Usage ==="])
    filter_usage = results.get("filter_usage", [])
    if filter_usage:
        for row in filter_usage:
            writer.writerow(row)
    else:
        writer.writerow(["No data"])

    csv_content = output.getvalue()

    if output_file:
        with open(output_file, "w") as f:
            f.write(csv_content)
        print(f"\nReport saved to: {output_file}")
    else:
        print("\n" + csv_content)

    return csv_content


def main():
    parser = argparse.ArgumentParser(description="Generate AI Radar AWS analytics report")
    parser.add_argument("--days", type=int, default=7, help="Number of days to analyze (default: 7)")
    parser.add_argument("--output", type=str, default="", help="Output CSV file path (default: stdout)")
    args = parser.parse_args()

    print("AI Radar AWS - Analytics Report Generator")
    print("=" * 50)

    # Get stack outputs
    print("\nRetrieving stack configuration...")
    outputs = get_stack_outputs()
    data_bucket, logs_bucket = get_bucket_names(outputs)

    if not data_bucket or not logs_bucket:
        print("Error: Could not determine bucket names from stack.")
        sys.exit(1)

    print(f"  Data bucket: {data_bucket}")
    print(f"  Logs bucket: {logs_bucket}")

    # Athena output location
    output_location = f"s3://{data_bucket}/analytics/athena-results/"

    # Setup
    athena = boto3.client("athena")
    setup_database(athena, output_location, data_bucket, logs_bucket)

    # Run queries
    print(f"\nRunning analytics queries (last {args.days} days)...")
    results = run_analytics_queries(athena, output_location, args.days)

    # Compile report
    print("\nCompiling report...")
    compile_report(results, args.days, args.output)

    print("\nDone.")


if __name__ == "__main__":
    main()

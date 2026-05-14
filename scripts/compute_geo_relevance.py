#!/usr/bin/env python3
"""Backfill geo_relevance field for existing announcements.

Reads the CSV from S3, computes geo_relevance for each announcement
using the importance classifier's geography detection, and writes
the updated CSV back to S3.

Usage:
    python scripts/compute_geo_relevance.py
"""
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3

from src.config import Config
from src.pipeline.importance_classifier import (
    GEOGRAPHY_KEYWORDS,
    GLOBAL_AVAILABILITY_KEYWORDS,
)
from src.shared.models import ProcessedAnnouncement, RSSItem


def compute_geo_relevance_for_text(title: str, description: str, preferred: str) -> str:
    """Compute geo_relevance from announcement text."""
    if preferred == "global":
        return ""

    text = (title + " " + description).lower()

    # Check for global availability keywords first
    for keyword in GLOBAL_AVAILABILITY_KEYWORDS:
        if keyword in text:
            return "global"

    # Check if preferred geography is mentioned
    if preferred in GEOGRAPHY_KEYWORDS:
        for keyword in GEOGRAPHY_KEYWORDS[preferred]:
            if keyword in text:
                return "local"

    return ""


def main():
    config = Config()
    preferred = config.preferred_geography.lower()

    # Get bucket name from environment or CloudFormation
    data_bucket = os.environ.get("DATA_BUCKET_NAME", "")
    if not data_bucket:
        cfn = boto3.client("cloudformation")
        try:
            response = cfn.describe_stacks(StackName="AiRadarAwsStack")
            outputs = {
                o["OutputKey"]: o["OutputValue"]
                for o in response["Stacks"][0].get("Outputs", [])
            }
            data_bucket = outputs.get("DataBucketName", "")
        except Exception:
            pass

    # Fallback: look up the S3 bucket resource directly
    if not data_bucket:
        cfn = boto3.client("cloudformation")
        resources = cfn.list_stack_resources(StackName="AiRadarAwsStack")
        for r in resources.get("StackResourceSummaries", []):
            if (r["LogicalResourceId"].startswith("DataBucket")
                    and r["ResourceType"] == "AWS::S3::Bucket"):
                data_bucket = r["PhysicalResourceId"]
                break

    if not data_bucket:
        print("Error: Could not determine data bucket name.")
        sys.exit(1)

    print(f"Data bucket: {data_bucket}")
    print(f"Preferred geography: {preferred}")

    s3 = boto3.client("s3")
    csv_key = "database/announcements.csv"

    # Read existing CSV
    response = s3.get_object(Bucket=data_bucket, Key=csv_key)
    csv_content = response["Body"].read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)
    fieldnames = reader.fieldnames

    # Add geo_relevance column if not present
    if "geo_relevance" not in fieldnames:
        fieldnames = list(fieldnames) + ["geo_relevance"]

    # Compute geo_relevance for each row
    updated = 0
    for row in rows:
        title = row.get("title", "")
        description = row.get("description", "")
        old_value = row.get("geo_relevance", "")
        new_value = compute_geo_relevance_for_text(title, description, preferred)

        if new_value != old_value:
            updated += 1

        row["geo_relevance"] = new_value

    # Write updated CSV back to S3
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    s3.put_object(
        Bucket=data_bucket,
        Key=csv_key,
        Body=output.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )

    # Print summary
    local_count = sum(1 for r in rows if r["geo_relevance"] == "local")
    global_count = sum(1 for r in rows if r["geo_relevance"] == "global")
    none_count = sum(1 for r in rows if r["geo_relevance"] == "")

    print(f"\nProcessed {len(rows)} announcements:")
    print(f"  Local ({preferred.upper()}): {local_count}")
    print(f"  Global: {global_count}")
    print(f"  Not relevant: {none_count}")
    print(f"  Updated: {updated}")
    print("\nDone. Run ./rebuild-site.sh --skip-cdk to see changes on the website.")


if __name__ == "__main__":
    main()

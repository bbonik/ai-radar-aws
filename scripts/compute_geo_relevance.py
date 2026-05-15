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
    ImportanceClassifier,
)
from src.shared.models import AnnouncementTags, ProcessedAnnouncement, RSSItem


# Services known to be available in APJ (mirrors ImportanceClassifier.APJ_AVAILABLE_SERVICES)
APJ_AVAILABLE_SERVICES = ImportanceClassifier.APJ_AVAILABLE_SERVICES


def compute_geo_relevance_for_row(row: dict, preferred: str) -> str:
    """Compute geo_relevance from announcement CSV row (text + tags).

    Primary: uses LLM tagger's geo_availability from tags JSON.
    Fallback: keyword-based detection if geo_availability is missing/unknown.

    Logic:
    1. Check tagger's geo_availability field
    2. If empty/unknown, fall back to keyword-based detection
    """
    if preferred == "global":
        return ""

    # Primary: use tagger's geo_availability from tags
    tags_raw = row.get("tags", "")
    if tags_raw:
        tags = AnnouncementTags.deserialize(tags_raw)
        if tags.geo_availability and tags.geo_availability != "unknown":
            geo = tags.geo_availability
            if geo == preferred:
                return "local"
            elif geo == "global":
                return "global"
            else:
                return ""  # Specific non-preferred geography

    # Fallback: keyword-based detection
    title = row.get("title", "")
    description = row.get("description", "")
    text = (title + " " + description).lower()

    # Step 1: Check for global availability keywords
    for keyword in GLOBAL_AVAILABILITY_KEYWORDS:
        if keyword in text:
            return "global"

    # Step 2: Check if preferred geography is mentioned
    if preferred in GEOGRAPHY_KEYWORDS:
        for keyword in GEOGRAPHY_KEYWORDS[preferred]:
            if keyword in text:
                return "local"

    # Step 3: Check if any non-preferred geography is mentioned
    any_region_mentioned = False
    for geography, keywords in GEOGRAPHY_KEYWORDS.items():
        if geography == preferred:
            continue
        for keyword in keywords:
            if keyword in text:
                any_region_mentioned = True
                break
        if any_region_mentioned:
            break

    if any_region_mentioned:
        return ""  # Region-specific to somewhere else

    # Step 4: No regions detected — infer global for GA/new-feature on APJ service
    if tags_raw:
        tags = AnnouncementTags.deserialize(tags_raw)
        if ("ga-launch" in tags.types or "new-feature" in tags.types):
            if any(svc in APJ_AVAILABLE_SERVICES for svc in tags.services):
                return "global"

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
        old_value = row.get("geo_relevance", "")
        new_value = compute_geo_relevance_for_row(row, preferred)

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

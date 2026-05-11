#!/usr/bin/env python3
"""Retroactively tag existing announcements in S3.

Reads the announcements CSV, tags each one using the Tagger (Haiku 4.5),
and writes the updated CSV back to S3.

Usage:
    python scripts/retag_announcements.py

Requires:
    - AWS credentials configured
    - INFERENCE_PROFILE_C_ARN env var (or uses model ID directly)
    - DATA_BUCKET_NAME env var (or auto-detects from CloudFormation stack)
"""

import csv
import io
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import ProcessedAnnouncement, RSSItem
from src.pipeline.tagger import Tagger


def get_data_bucket() -> str:
    """Get the data bucket name from env or CloudFormation stack."""
    bucket = os.environ.get("DATA_BUCKET_NAME")
    if bucket:
        return bucket

    # Auto-detect from CloudFormation stack
    cf = boto3.client("cloudformation", region_name="us-east-1")
    try:
        resources = cf.list_stack_resources(StackName="AiRadarAwsStack")
        for r in resources["StackResourceSummaries"]:
            if r["LogicalResourceId"] == "DataBucket" or "databucket" in r["PhysicalResourceId"].lower():
                if r["ResourceType"] == "AWS::S3::Bucket":
                    return r["PhysicalResourceId"]
    except Exception:
        pass

    # Fallback: search for the bucket
    s3 = boto3.client("s3")
    for bucket_info in s3.list_buckets()["Buckets"]:
        if "airadarawsstack" in bucket_info["Name"] and "data" in bucket_info["Name"]:
            return bucket_info["Name"]

    raise RuntimeError("Could not determine data bucket name. Set DATA_BUCKET_NAME env var.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Retroactively tag announcements")
    parser.add_argument("--force", action="store_true", help="Re-tag all announcements (even already tagged ones)")
    args = parser.parse_args()

    config = Config()
    logger = StructuredLogger(lambda_name="retag-script", run_id="retag-manual")
    tagger = Tagger(config, logger)
    
    data_bucket = get_data_bucket()
    print(f"Using data bucket: {data_bucket}")

    s3 = boto3.client("s3", region_name=config.aws_region)

    # Download existing CSV
    print("Downloading announcements CSV...")
    response = s3.get_object(Bucket=data_bucket, Key="database/announcements.csv")
    csv_content = response["Body"].read().decode("utf-8")

    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)
    print(f"Found {len(rows)} announcements")

    # Tag each announcement
    tagged_count = 0
    skipped_count = 0

    for i, row in enumerate(rows):
        # Check if already tagged (skip unless --force)
        if not args.force:
            existing_tags = row.get("tags", "")
            if existing_tags and existing_tags != "" and existing_tags != "{}":
                import json
                try:
                    parsed = json.loads(existing_tags)
                    if any(parsed.get(k) for k in ["services", "types", "concepts", "use_cases", "providers"]):
                        skipped_count += 1
                        print(f"  [{i+1}/{len(rows)}] Already tagged: {row['title'][:60]}...")
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass

        # Create an RSSItem for the tagger
        item = RSSItem(
            title=row["title"],
            description=row["description"],
            pub_date=row["pub_date"],
            link=row["link"],
        )

        print(f"  [{i+1}/{len(rows)}] Tagging: {row['title'][:60]}...")
        tags = tagger.tag(item)
        row["tags"] = tags.serialize()
        tagged_count += 1

        # Small delay to avoid throttling
        time.sleep(0.5)

    print(f"\nTagged: {tagged_count}, Skipped (already tagged): {skipped_count}")

    if tagged_count == 0:
        print("No announcements needed tagging. Done.")
        return

    # Write updated CSV back to S3
    print("Uploading updated CSV to S3...")
    fieldnames = list(rows[0].keys())
    # Ensure 'tags' is in fieldnames
    if "tags" not in fieldnames:
        fieldnames.append("tags")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    s3.put_object(
        Bucket=data_bucket,
        Key="database/announcements.csv",
        Body=output.getvalue().encode("utf-8"),
        ContentType="text/csv",
        ServerSideEncryption="AES256",
    )

    print(f"✓ Updated CSV uploaded to s3://{data_bucket}/database/announcements.csv")
    print(f"\nNow rebuild the website: ./rebuild-site.sh --skip-cdk")


if __name__ == "__main__":
    main()

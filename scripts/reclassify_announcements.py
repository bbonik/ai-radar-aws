#!/usr/bin/env python3
"""Reclassify existing announcements using the updated scoring system.

Reads the announcements CSV, recomputes importance scores using the current
classifier (including tag-based bonuses), and writes the updated CSV back to S3.

Usage:
    python scripts/reclassify_announcements.py
"""

import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import AnnouncementTags, RSSItem
from src.pipeline.importance_classifier import ImportanceClassifier


def get_data_bucket() -> str:
    """Get the data bucket name from env or CloudFormation stack."""
    bucket = os.environ.get("DATA_BUCKET_NAME")
    if bucket:
        return bucket

    cf = boto3.client("cloudformation", region_name="us-east-1")
    try:
        resources = cf.list_stack_resources(StackName="AiRadarAwsStack")
        for r in resources["StackResourceSummaries"]:
            if r["ResourceType"] == "AWS::S3::Bucket" and "databucket" in r["PhysicalResourceId"].lower():
                return r["PhysicalResourceId"]
    except Exception:
        pass

    s3 = boto3.client("s3")
    for bucket_info in s3.list_buckets()["Buckets"]:
        if "airadarawsstack" in bucket_info["Name"] and "data" in bucket_info["Name"]:
            return bucket_info["Name"]

    raise RuntimeError("Could not determine data bucket name. Set DATA_BUCKET_NAME env var.")


def main():
    config = Config()
    logger = StructuredLogger(lambda_name="reclassify-script", run_id="reclassify-manual")
    classifier = ImportanceClassifier(config, logger)

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

    # Reclassify each announcement
    changed_count = 0

    for i, row in enumerate(rows):
        # Build an RSSItem for the classifier
        item = RSSItem(
            title=row["title"],
            description=row["description"],
            pub_date=row["pub_date"],
            link=row["link"],
        )

        # Parse existing tags
        tags = AnnouncementTags.deserialize(row.get("tags", ""))

        # Reclassify with tags
        new_star, new_score = classifier.classify(item, tags)
        old_star = int(row["importance_level"])
        old_score = float(row["importance_score"])

        if new_star != old_star or abs(new_score - old_score) > 0.01:
            print(f"  [{i+1}/{len(rows)}] {row['title'][:60]}...")
            print(f"    {old_star}★ ({old_score:.1f}) → {new_star}★ ({new_score:.1f})")
            row["importance_level"] = str(new_star)
            row["importance_score"] = str(new_score)
            changed_count += 1
        else:
            pass  # No change

        # Clean blogpost_links: strip trailing punctuation + filter service homepages
        raw_links = row.get("blogpost_links", "")
        if raw_links:
            links = raw_links.split("|")
            cleaned = []
            for url in links:
                # Strip trailing punctuation
                while url and url[-1] in ".),;:!?\"'":
                    url = url[:-1]
                if not url:
                    continue
                # Filter out service homepages
                import re
                if re.match(r"https?://aws\.amazon\.com/[a-z0-9-]+/?$", url):
                    continue
                cleaned.append(url)
            new_links = "|".join(cleaned)
            if new_links != raw_links:
                row["blogpost_links"] = new_links
                if new_star == old_star:  # Only count if not already counted above
                    changed_count += 1

    print(f"\nReclassified: {changed_count} announcements changed")

    if changed_count == 0:
        print("No changes needed. Done.")
        return

    # Write updated CSV back to S3
    print("Uploading updated CSV to S3...")
    fieldnames = list(rows[0].keys())

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

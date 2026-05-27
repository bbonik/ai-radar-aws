#!/usr/bin/env python3
"""Backup S3 data to a local compressed zip file.

Downloads the announcements CSV (source of truth) and optionally the
full website files, then compresses into a timestamped zip.

Usage:
    python scripts/backup.py                     # Data CSV only (fast)
    python scripts/backup.py --full              # Data + website files
    python scripts/backup.py --output ~/backups  # Custom output directory
"""
import argparse
import os
import sys
import zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3

STACK_NAME = "AiRadarAwsStack"


def get_bucket_names():
    """Get data and website bucket names from CloudFormation."""
    cfn = boto3.client("cloudformation")
    resources = cfn.list_stack_resources(StackName=STACK_NAME)

    data_bucket = ""
    website_bucket = ""
    for r in resources.get("StackResourceSummaries", []):
        if r["ResourceType"] == "AWS::S3::Bucket":
            if r["LogicalResourceId"].startswith("DataBucket"):
                data_bucket = r["PhysicalResourceId"]
            elif r["LogicalResourceId"].startswith("WebsiteBucket"):
                website_bucket = r["PhysicalResourceId"]

    return data_bucket, website_bucket


def download_bucket_contents(s3, bucket, prefix=""):
    """Download all objects from a bucket/prefix. Returns list of (key, bytes)."""
    files = []
    paginator = s3.get_paginator("list_objects_v2")
    kwargs = {"Bucket": bucket}
    if prefix:
        kwargs["Prefix"] = prefix

    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            response = s3.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read()
            files.append((key, content))

    return files


def main():
    parser = argparse.ArgumentParser(description="Backup AI Radar AWS data to local zip")
    parser.add_argument("--full", action="store_true", help="Include website files (not just data)")
    parser.add_argument("--output", type=str, default=".", help="Output directory (default: current)")
    args = parser.parse_args()

    print("AI Radar AWS — Backup")
    print("=" * 50)

    # Get bucket names
    data_bucket, website_bucket = get_bucket_names()
    if not data_bucket:
        print("Error: Could not find data bucket.")
        sys.exit(1)

    print(f"  Data bucket: {data_bucket}")
    if args.full:
        print(f"  Website bucket: {website_bucket}")

    s3 = boto3.client("s3")

    # Create output directory
    os.makedirs(args.output, exist_ok=True)

    # Timestamp for filename
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    zip_name = f"ai-radar-backup-{timestamp}.zip"
    zip_path = os.path.join(args.output, zip_name)

    print(f"\nDownloading from S3...")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Always download data bucket contents
        print("  Downloading data files...")
        data_files = download_bucket_contents(s3, data_bucket)
        for key, content in data_files:
            zf.writestr(f"data/{key}", content)
            print(f"    + data/{key} ({len(content):,} bytes)")

        # Optionally download website files
        if args.full:
            if not website_bucket:
                print("  Warning: Could not find website bucket, skipping.")
            else:
                print("  Downloading website files...")
                website_files = download_bucket_contents(s3, website_bucket)
                for key, content in website_files:
                    zf.writestr(f"website/{key}", content)
                print(f"    + {len(website_files)} website files")

    # Report
    zip_size = os.path.getsize(zip_path)
    print(f"\n✓ Backup saved: {zip_path}")
    print(f"  Size: {zip_size:,} bytes ({zip_size / 1024:.1f} KB)")
    print(f"  Contents: {len(data_files)} data files" + (f" + {len(website_files)} website files" if args.full else ""))


if __name__ == "__main__":
    main()

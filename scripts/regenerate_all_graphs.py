#!/usr/bin/env python3
"""Clear all existing visual summaries and regenerate them with the current style.

This script:
1. Downloads the CSV from S3
2. Clears all mermaid_graph values
3. Regenerates graphs for all 2+ star announcements using the current prompt
4. Uploads the updated CSV back to S3

Usage:
    python scripts/regenerate_all_graphs.py
"""

import csv
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3

from src.config import Config
from src.shared.logger import StructuredLogger
from src.shared.models import Report, RSSItem
from src.pipeline.graph_generator import GraphGenerator


def get_data_bucket() -> str:
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
    for b in s3.list_buckets()["Buckets"]:
        if "airadarawsstack" in b["Name"] and "data" in b["Name"]:
            return b["Name"]
    raise RuntimeError("Set DATA_BUCKET_NAME env var.")


def main():
    config = Config()
    logger = StructuredLogger(lambda_name="regen-graphs", run_id="regen-manual")
    generator = GraphGenerator(config=config, logger=logger)

    data_bucket = get_data_bucket()
    print(f"Using data bucket: {data_bucket}")

    s3 = boto3.client("s3", region_name=config.aws_region)

    # Download CSV
    print("Downloading announcements CSV...")
    response = s3.get_object(Bucket=data_bucket, Key="database/announcements.csv")
    csv_content = response["Body"].read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)
    print(f"Found {len(rows)} announcements")

    # Clear all existing graphs
    cleared = sum(1 for r in rows if r.get("mermaid_graph", "").strip())
    for row in rows:
        row["mermaid_graph"] = ""
    print(f"Cleared {cleared} existing graphs")

    # Regenerate for 2+ star announcements
    generated = 0
    skipped = 0
    failed = 0

    for i, row in enumerate(rows):
        importance = int(row.get("importance_level", "1"))
        if importance < 2:
            skipped += 1
            continue

        title = row["title"]
        print(f"  [{i+1}/{len(rows)}] {title[:60]}...", end=" ")

        item = RSSItem(
            title=row["title"],
            description=row["description"],
            pub_date=row["pub_date"],
            link=row["link"],
        )
        report = Report(
            whats_new=row.get("whats_new", ""),
            how_it_works=row.get("how_it_works", ""),
            why_important=row.get("why_important", ""),
            how_different=row.get("how_different", ""),
            when_to_prefer=row.get("when_to_prefer", ""),
            availability=row.get("availability", ""),
        )

        graph = generator.generate(item=item, report=report, importance_level=importance)
        if graph:
            row["mermaid_graph"] = graph
            generated += 1
            print(f"✓ ({len(graph)} chars)")
        else:
            failed += 1
            print("✗ (failed)")

        time.sleep(1)

    print(f"\nResults: {generated} generated, {failed} failed, {skipped} skipped (1-star)")

    # Upload updated CSV
    print("Uploading updated CSV...")
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
    print(f"✓ Done. Now rebuild: ./rebuild-site.sh --skip-cdk")


if __name__ == "__main__":
    main()

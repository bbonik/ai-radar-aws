#!/usr/bin/env python3
"""Generate visual summaries (Mermaid graphs) for announcements missing them.

Reads the CSV, finds announcements with importance_level >= 2 that have no
mermaid_graph, generates one using Bedrock Opus, and writes back to S3.

Usage:
    python scripts/generate_missing_graphs.py
"""

import csv
import io
import json
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
    logger = StructuredLogger(lambda_name="generate-graphs", run_id="graphs-manual")
    generator = GraphGenerator(config=config, logger=logger)

    data_bucket = get_data_bucket()
    print(f"Using data bucket: {data_bucket}")

    s3 = boto3.client("s3", region_name=config.aws_region)

    print("Downloading announcements CSV...")
    response = s3.get_object(Bucket=data_bucket, Key="database/announcements.csv")
    csv_content = response["Body"].read().decode("utf-8")

    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)
    print(f"Found {len(rows)} announcements")

    generated = 0
    skipped = 0

    for i, row in enumerate(rows):
        importance = int(row.get("importance_level", "1"))
        has_graph = row.get("mermaid_graph", "").strip()

        # Skip if already has a graph or is 1-star
        if has_graph or importance < 2:
            skipped += 1
            continue

        title = row["title"]
        print(f"  [{i+1}/{len(rows)}] Generating graph: {title[:60]}...")

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
            print(f"    ✓ Generated ({len(graph)} chars)")
        else:
            print(f"    ✗ Failed (None returned)")

        time.sleep(1)  # Avoid throttling

    print(f"\nGenerated: {generated}, Skipped: {skipped}")

    if generated == 0:
        print("No graphs needed. Done.")
        return

    # Write back
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

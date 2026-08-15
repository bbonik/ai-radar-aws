#!/usr/bin/env python3
"""One-time migration: drop the retired ``aws_service`` column from the CSV.

The ``aws_service`` field was superseded by the taxonomy ``tags.services`` and
is no longer read or displayed anywhere. This script rewrites the
source-of-truth CSV in S3 with that column removed. It is idempotent — running
it again when the column is already absent is a no-op.

Always take a backup first:
    python scripts/backup.py
    python scripts/drop_aws_service_column.py
"""
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3

csv.field_size_limit(sys.maxsize)

from scripts._common import STACK_NAME, deployed_region

REGION = deployed_region()
CSV_KEY = "database/announcements.csv"
COLUMN = "aws_service"


def _discover_data_bucket() -> str:
    bucket = os.environ.get("DATA_BUCKET_NAME", "")
    if bucket:
        return bucket
    cfn = boto3.client("cloudformation", region_name=REGION)
    resources = cfn.list_stack_resources(StackName=STACK_NAME)
    for r in resources.get("StackResourceSummaries", []):
        if (
            r["LogicalResourceId"].startswith("DataBucket")
            and r["ResourceType"] == "AWS::S3::Bucket"
        ):
            return r["PhysicalResourceId"]
    return ""


def main():
    data_bucket = _discover_data_bucket()
    if not data_bucket:
        print("Error: Could not determine data bucket name.")
        sys.exit(1)

    print(f"Data bucket: {data_bucket}")
    s3 = boto3.client("s3", region_name=REGION)

    response = s3.get_object(Bucket=data_bucket, Key=CSV_KEY)
    content = response["Body"].read().decode("utf-8")

    reader = csv.DictReader(io.StringIO(content))
    fieldnames = reader.fieldnames or []

    if COLUMN not in fieldnames:
        print(f"Column '{COLUMN}' is already absent. Nothing to do.")
        return

    new_fieldnames = [c for c in fieldnames if c != COLUMN]
    rows = list(reader)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=new_fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        row.pop(COLUMN, None)
        writer.writerow(row)

    s3.put_object(
        Bucket=data_bucket,
        Key=CSV_KEY,
        Body=output.getvalue().encode("utf-8"),
        ContentType="text/csv",
        ServerSideEncryption="AES256",
    )

    print(f"Removed column '{COLUMN}'.")
    print(f"  Rows: {len(rows)}")
    print(f"  Columns: {len(fieldnames)} -> {len(new_fieldnames)}")
    print("\nDone. Run ./rebuild-site.sh --skip-cdk to refresh the website.")


if __name__ == "__main__":
    main()

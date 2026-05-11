#!/usr/bin/env python3
"""Backfill card_summary for existing announcements in S3.

Reads the announcements CSV, generates a card_summary for each row missing one
using Bedrock Haiku 4.5, and writes the updated CSV back to S3.

Usage:
    python scripts/generate_card_summaries.py

Requires:
    - AWS credentials configured
    - INFERENCE_PROFILE_C_ARN env var (or uses model ID directly)
    - DATA_BUCKET_NAME env var (or auto-detects from CloudFormation stack)
"""

import csv
import io
import json
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3

from src.config import Config


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


def generate_card_summary(bedrock_client, model_id: str, title: str, whats_new: str) -> str:
    """Generate a card summary using Bedrock Haiku 4.5."""
    prompt = (
        "Given this announcement title and What's New section, write a single sentence "
        "(max 150 characters) that captures the essence. Do NOT repeat the title. "
        "Focus on the 'so what' — why should someone click to read more?\n\n"
        f"Title: {title}\n\n"
        f"What's New: {whats_new}\n\n"
        "Return ONLY the summary sentence, nothing else."
    )

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 256,
        "temperature": 0.3,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    })

    response = bedrock_client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=body,
    )

    response_body = json.loads(response["body"].read())
    content = response_body.get("content", [])
    if content and isinstance(content, list):
        text_parts = [
            block.get("text", "")
            for block in content
            if block.get("type") == "text"
        ]
        summary = " ".join(text_parts).strip()
        # Enforce 150 char limit
        if len(summary) > 150:
            summary = summary[:147] + "..."
        return summary

    return ""


def main():
    config = Config()
    data_bucket = get_data_bucket()
    print(f"Using data bucket: {data_bucket}")

    s3 = boto3.client("s3", region_name=config.aws_region)
    bedrock_client = boto3.client("bedrock-runtime", region_name=config.aws_region)

    # Use inference profile ARN from env, or fall back to model ID
    model_id = os.environ.get(
        "INFERENCE_PROFILE_C_ARN",
        config.llm_c_model_id,
    )

    # Download existing CSV
    print("Downloading announcements CSV...")
    response = s3.get_object(Bucket=data_bucket, Key="database/announcements.csv")
    csv_content = response["Body"].read().decode("utf-8")

    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)
    print(f"Found {len(rows)} announcements")

    # Generate card_summary for each row missing one
    generated_count = 0
    skipped_count = 0

    for i, row in enumerate(rows):
        existing_summary = row.get("card_summary", "")
        if existing_summary and existing_summary.strip():
            skipped_count += 1
            print(f"  [{i+1}/{len(rows)}] Already has summary: {row['title'][:60]}...")
            continue

        title = row.get("title", "")
        whats_new = row.get("whats_new", "")

        if not title or not whats_new:
            skipped_count += 1
            print(f"  [{i+1}/{len(rows)}] Missing title/whats_new, skipping: {row.get('title', 'N/A')[:60]}...")
            continue

        print(f"  [{i+1}/{len(rows)}] Generating summary: {title[:60]}...")
        try:
            summary = generate_card_summary(bedrock_client, model_id, title, whats_new)
            row["card_summary"] = summary
            generated_count += 1
            print(f"    -> {summary}")
        except Exception as exc:
            print(f"    ERROR: {exc}")
            row["card_summary"] = ""

        # Small delay to avoid throttling
        time.sleep(0.5)

    print(f"\nGenerated: {generated_count}, Skipped (already filled): {skipped_count}")

    if generated_count == 0:
        print("No announcements needed card summaries. Done.")
        return

    # Write updated CSV back to S3
    print("Uploading updated CSV to S3...")
    fieldnames = list(rows[0].keys())
    # Ensure 'card_summary' is in fieldnames
    if "card_summary" not in fieldnames:
        fieldnames.append("card_summary")

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

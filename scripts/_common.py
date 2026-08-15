"""Shared helpers for the utility scripts in this directory.

Single-sources what was previously duplicated (and drifted) across the
scripts: the deployed region comes from src.config.Config, never a hardcoded
literal, so changing the region in one place keeps every tool working
(docs/audit-remediation-plan.md item 18). Stack and resource names are
defined by this repository and are therefore legitimately constants — but
they live here once rather than as string literals in ten files.
"""

import json
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Names defined by this repository (see infrastructure/stack.py)
STACK_NAME = "AiRadarAwsStack"
PIPELINE_FUNCTION_NAME = "ai-radar-report-pipeline"
WEBSITE_BUILDER_FUNCTION_NAME = "ai-radar-website-builder"
PIPELINE_LOG_GROUP = f"/aws/lambda/{PIPELINE_FUNCTION_NAME}"


def deployed_region() -> str:
    """The region the stack deploys to, from the single source of truth."""
    import sys

    sys.path.insert(0, str(_PROJECT_ROOT))
    from src.config import Config

    return Config().aws_region


def find_stack_bucket(kind: str, region: str | None = None) -> str:
    """Resolve a stack bucket's physical name by logical-ID prefix.

    kind: "DataBucket", "WebsiteBucket", or "LogsBucket".
    Honours the corresponding *_BUCKET_NAME env var override first, matching
    the behaviour the individual scripts previously implemented ad hoc.
    """
    import boto3

    env_override = os.environ.get(f"{kind.replace('Bucket', '').upper()}_BUCKET_NAME")
    if env_override:
        return env_override

    cfn = boto3.client("cloudformation", region_name=region or deployed_region())
    resources = cfn.describe_stack_resources(StackName=STACK_NAME)["StackResources"]
    for r in resources:
        if (
            r["ResourceType"] == "AWS::S3::Bucket"
            and r["LogicalResourceId"].startswith(kind)
        ):
            return r["PhysicalResourceId"]
    raise RuntimeError(
        f"No {kind} found in stack {STACK_NAME}. "
        f"Set {kind.replace('Bucket', '').upper()}_BUCKET_NAME to override."
    )


def load_context_env() -> None:
    """Export per-deployment runtime overrides from cdk.context.json, if present.

    The deployed Lambdas receive PREFERRED_GEOGRAPHY as an environment variable
    injected by the CDK stack from the gitignored cdk.context.json. Scripts run
    on a laptop do not, so this loads the same value from the same file — keeping
    laptop and Lambda behaviour identical from one source of truth.

    A missing file is the supported fresh-clone state (generic defaults apply),
    not an error. A malformed file raises, deliberately: silently ignoring it
    would mean silently running with the wrong configuration.

    An already-set environment variable is never overwritten, so an explicit
    export still wins for one-off experiments.
    """
    context_path = _PROJECT_ROOT / "cdk.context.json"
    if not context_path.exists():
        return
    context = json.loads(context_path.read_text(encoding="utf-8"))
    geo = context.get("preferred_geography")
    if geo and "PREFERRED_GEOGRAPHY" not in os.environ:
        os.environ["PREFERRED_GEOGRAPHY"] = str(geo)

"""Shared helpers for the utility scripts in this directory."""

import json
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


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

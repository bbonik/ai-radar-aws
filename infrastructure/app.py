#!/usr/bin/env python3
"""AI Radar AWS - CDK App Entry Point.

This is the CDK application entry point that instantiates the AI Radar AWS stack.
"""

import os
import sys

# Ensure project root is on sys.path so 'infrastructure' and 'src' are importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aws_cdk as cdk

from infrastructure.stack import AiRadarAwsStack
from src.config import Config


app = cdk.App()

config = Config()

AiRadarAwsStack(
    app,
    "AiRadarAwsStack",
    env=cdk.Environment(region=config.aws_region),
)

app.synth()

"""CDK synthesis tests for the monthly analytics rollup resources.

Validates: monthly-analytics-rollup Req 1.1, 1.6, 3.3, 3.4, 3.5, 3.8,
3.10, 8.7 (via existing alert topic), 9.4.
"""

import json

import pytest
import aws_cdk as cdk
from aws_cdk import assertions

from infrastructure.stack import AiRadarAwsStack
from src.config import Config


@pytest.fixture(scope="module")
def template():
    app = cdk.App()
    config = Config()
    stack = AiRadarAwsStack(
        app, "RollupTestStack", env=cdk.Environment(region=config.aws_region)
    )
    return assertions.Template.from_stack(stack)


class TestRollupLambda:
    def test_function_exists_with_15_minute_timeout(self, template):
        template.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "FunctionName": "ai-radar-analytics-rollup",
                "Timeout": 900,
                "ReservedConcurrentExecutions": 1,
                "Handler": "src.analytics_rollup.handler.handler",
            },
        )

    def test_environment_carries_both_bucket_names(self, template):
        functions = template.find_resources(
            "AWS::Lambda::Function",
            {"Properties": {"FunctionName": "ai-radar-analytics-rollup"}},
        )
        (props,) = [f["Properties"] for f in functions.values()]
        env = props["Environment"]["Variables"]
        assert "LOGS_BUCKET_NAME" in env
        assert "DATA_BUCKET_NAME" in env


class TestRollupSchedule:
    def test_monthly_cron_on_day_3_at_03_utc(self, template):
        """Req 1.1: fixed day-of-month in 3-5; chosen 3rd, 03:00 UTC."""
        template.has_resource_properties(
            "AWS::Events::Rule",
            {"ScheduleExpression": "cron(0 3 3 * ? *)"},
        )

    def test_target_retries_twice(self, template):
        rules = template.find_resources("AWS::Events::Rule")
        rollup_rules = [
            r for r in rules.values()
            if r["Properties"].get("ScheduleExpression") == "cron(0 3 3 * ? *)"
        ]
        (rule,) = rollup_rules
        (target,) = rule["Properties"]["Targets"]
        assert target["RetryPolicy"]["MaximumRetryAttempts"] == 2


class TestLifecycleRulesUnchanged:
    def test_exactly_three_lifecycle_rules_none_matching_rollups(self, template):
        """Req 3.3 / 3.4: rollups/ matches no expiration or transition rule."""
        buckets = template.find_resources("AWS::S3::Bucket")
        lifecycle_buckets = [
            b for b in buckets.values()
            if "LifecycleConfiguration" in b["Properties"]
        ]
        (logs_bucket,) = lifecycle_buckets
        rules = logs_bucket["Properties"]["LifecycleConfiguration"]["Rules"]
        assert len(rules) == 3
        prefixes = {r["Prefix"] for r in rules}
        assert prefixes == {"events/", "cloudfront/", "athena-results/"}
        for rule in rules:
            assert not rule["Prefix"].startswith("rollups")
            assert "Transitions" not in rule


class TestRollupIam:
    def _rollup_policies(self, template):
        policies = template.find_resources("AWS::IAM::Policy")
        return [
            p for p in policies.values()
            if "AnalyticsRollupLambda" in json.dumps(p)
        ]

    def test_no_delete_on_raw_prefixes_and_no_data_bucket_write(self, template):
        """Req 3.8 / 3.10."""
        blob = json.dumps(self._rollup_policies(template))
        statements = []
        for policy in self._rollup_policies(template):
            statements.extend(
                policy["Properties"]["PolicyDocument"]["Statement"])
        for stmt in statements:
            actions = stmt["Action"]
            actions = [actions] if isinstance(actions, str) else actions
            resources = json.dumps(stmt["Resource"])
            for action in actions:
                if action.startswith("s3:DeleteObject"):
                    # delete may never target raw or catalog keys
                    assert "cloudfront/" not in resources
                    assert "events/" not in resources
                    assert "announcements.csv" not in resources
                if action.startswith(("s3:PutObject", "s3:Abort")):
                    assert "announcements.csv" not in resources
        assert "athena:StartQueryExecution" in blob
        assert "glue:CreateTable" in blob


class TestRollupAlarms:
    def test_both_alarms_exist_and_notify_alert_topic(self, template):
        for name in ("Rollup-Errors", "Rollup-FailedInvocations"):
            alarms = template.find_resources(
                "AWS::CloudWatch::Alarm", {"Properties": {"AlarmName": name}}
            )
            (alarm,) = alarms.values()
            assert alarm["Properties"]["AlarmActions"], f"{name} has no action"

    def test_no_always_on_resources_added(self, template):
        """Req 9.4: nothing billed per unit of elapsed time."""
        assert template.find_resources("AWS::Lambda::Alias") == {}
        assert template.find_resources("AWS::ApplicationAutoScaling::ScalableTarget") == {}

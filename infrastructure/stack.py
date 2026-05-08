"""AI Radar AWS - CDK Stack.

Defines the full infrastructure for the AI Radar AWS platform:
- Lambda 1 (Report Pipeline): fetches, filters, classifies, researches, generates reports
- Lambda 2 (Website Builder): builds static site from CSV data
- S3 Data Bucket: stores announcement CSV and error records
- S3 Website Bucket: hosts the generated static website
- EventBridge Rule: triggers Lambda 1 on a daily schedule
- Bedrock Application Inference Profiles: cost-tracking for LLM A and LLM B
- CloudFront Distribution with OAC for secure website delivery
- AWS WAF Web ACL for DDoS and common attack protection
- Security response headers (CSP, X-Content-Type-Options, etc.)
- IAM permissions: least-privilege access for both Lambdas

Requirements: 14.1, 14.2, 14.3, 14.5, 5.2, 5.3, 6.2, 6.3, 13.1, 13.2, 13.3, 13.5, 13.6
"""

from aws_cdk import (
    Aws,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cloudwatch,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_wafv2 as wafv2,
    CfnResource,
)
from constructs import Construct

from src.config import Config


class AiRadarAwsStack(Stack):
    """CDK Stack for the AI Radar AWS platform."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        config = Config()

        # ─── S3 Data Bucket ───────────────────────────────────────────────
        # Stores announcement CSV and error records
        self.data_bucket = s3.Bucket(
            self,
            "DataBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,  # AES-256
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ─── S3 Website Bucket ────────────────────────────────────────────
        # Hosts the generated static website files
        self.website_bucket = s3.Bucket(
            self,
            "WebsiteBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,  # AES-256
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ─── Bedrock Application Inference Profiles ───────────────────────
        # LLM A - Report Generator (Claude Sonnet)
        # Model IDs like "us.anthropic.claude-sonnet-4-..." are cross-region
        # inference profile IDs, so CopyFrom uses the inference-profile ARN format
        self.inference_profile_a = CfnResource(
            self,
            "InferenceProfileA",
            type="AWS::Bedrock::ApplicationInferenceProfile",
            properties={
                "InferenceProfileName": config.llm_a_inference_profile_name,
                "ModelSource": {
                    "CopyFrom": f"arn:aws:bedrock:{config.aws_region}::inference-profile/{config.llm_a_model_id}",
                },
                "Tags": [
                    {"Key": "Project", "Value": "ai-radar-aws"},
                    {"Key": "Purpose", "Value": "report-generation"},
                    {"Key": "Model", "Value": "claude-sonnet"},
                ],
            },
        )

        # LLM B - Graph Generator (Claude Opus)
        self.inference_profile_b = CfnResource(
            self,
            "InferenceProfileB",
            type="AWS::Bedrock::ApplicationInferenceProfile",
            properties={
                "InferenceProfileName": config.llm_b_inference_profile_name,
                "ModelSource": {
                    "CopyFrom": f"arn:aws:bedrock:{config.aws_region}::inference-profile/{config.llm_b_model_id}",
                },
                "Tags": [
                    {"Key": "Project", "Value": "ai-radar-aws"},
                    {"Key": "Purpose", "Value": "graph-generation"},
                    {"Key": "Model", "Value": "claude-opus"},
                ],
            },
        )

        # ─── Lambda 2: Website Builder ────────────────────────────────────
        # Defined first so Lambda 1 can reference its function name/ARN
        self.website_builder_lambda = lambda_.Function(
            self,
            "WebsiteBuilderLambda",
            function_name="ai-radar-website-builder",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="src.website_builder.handler.handler",
            code=lambda_.Code.from_asset(
                ".",
                exclude=[
                    ".git/*",
                    ".hypothesis/*",
                    ".kiro/*",
                    ".pytest_cache/*",
                    "tests/*",
                    "infrastructure/*",
                    "cdk.out/*",
                    "node_modules/*",
                    "__pycache__/*",
                    "*.pyc",
                    ".venv/*",
                ],
            ),
            timeout=Duration.minutes(10),
            memory_size=512,
            environment={
                "DATA_BUCKET_NAME": self.data_bucket.bucket_name,
                "WEBSITE_BUCKET_NAME": self.website_bucket.bucket_name,
                # CLOUDFRONT_DISTRIBUTION_ID set below after distribution is created
            },
        )

        # ─── AWS WAF Web ACL (us-east-1 scope for CloudFront) ─────────────
        # WAF must be in us-east-1 for CloudFront associations
        self.waf_web_acl = wafv2.CfnWebACL(
            self,
            "WebsiteWafAcl",
            scope="CLOUDFRONT",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="AiRadarWebsiteWaf",
                sampled_requests_enabled=True,
            ),
            rules=[
                # Rate limiting: 1000 requests per 5 minutes per IP
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitRule",
                    priority=1,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=1000,
                            aggregate_key_type="IP",
                        ),
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AiRadarRateLimit",
                        sampled_requests_enabled=True,
                    ),
                ),
                # AWS Managed Common Rule Set
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesCommonRuleSet",
                    priority=2,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                        ),
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AiRadarCommonRules",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )

        # ─── CloudFront Response Headers Policy ───────────────────────────
        # Security headers: CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy
        self.response_headers_policy = cloudfront.ResponseHeadersPolicy(
            self,
            "SecurityHeadersPolicy",
            response_headers_policy_name="AiRadarSecurityHeaders",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=(
                        "default-src 'self'; "
                        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
                        "style-src 'self' 'unsafe-inline'; "
                        "img-src 'self' data:; "
                        "connect-src 'self'"
                    ),
                    override=True,
                ),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(
                    override=True,
                ),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY,
                    override=True,
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
            ),
        )

        # ─── CloudFront Distribution with OAC ─────────────────────────────
        # S3 bucket accessible only via CloudFront
        self.distribution = cloudfront.Distribution(
            self,
            "WebsiteDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(
                    self.website_bucket,
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=self.response_headers_policy,
            ),
            default_root_object="index.html",
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            web_acl_id=self.waf_web_acl.attr_arn,
        )

        # Set the CloudFront distribution ID on the website builder lambda
        self.website_builder_lambda.add_environment(
            "CLOUDFRONT_DISTRIBUTION_ID",
            self.distribution.distribution_id,
        )

        # ─── Lambda 1: Report Pipeline ────────────────────────────────────
        self.report_pipeline_lambda = lambda_.Function(
            self,
            "ReportPipelineLambda",
            function_name="ai-radar-report-pipeline",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="src.pipeline.handler.handler",
            code=lambda_.Code.from_asset(
                ".",
                exclude=[
                    ".git/*",
                    ".hypothesis/*",
                    ".kiro/*",
                    ".pytest_cache/*",
                    "tests/*",
                    "infrastructure/*",
                    "cdk.out/*",
                    "node_modules/*",
                    "__pycache__/*",
                    "*.pyc",
                    ".venv/*",
                ],
            ),
            timeout=Duration.minutes(15),
            memory_size=512,
            environment={
                "DATA_BUCKET_NAME": self.data_bucket.bucket_name,
                "WEBSITE_BUILDER_FUNCTION_NAME": self.website_builder_lambda.function_name,
                "INFERENCE_PROFILE_A_ARN": self.inference_profile_a.get_att(
                    "InferenceProfileArn"
                ).to_string(),
                "INFERENCE_PROFILE_B_ARN": self.inference_profile_b.get_att(
                    "InferenceProfileArn"
                ).to_string(),
            },
        )

        # ─── EventBridge Rule ─────────────────────────────────────────────
        # Triggers Lambda 1 at the configured daily schedule
        self.schedule_rule = events.Rule(
            self,
            "DailyScheduleRule",
            schedule=events.Schedule.cron(
                hour=str(config.schedule_hour),
                minute=str(config.schedule_minute),
            ),
        )
        self.schedule_rule.add_target(
            targets.LambdaFunction(self.report_pipeline_lambda)
        )

        # ─── IAM Permissions ──────────────────────────────────────────────

        # Lambda 1: read/write data bucket
        self.data_bucket.grant_read_write(self.report_pipeline_lambda)

        # Lambda 1: invoke Lambda 2 (async invocation)
        self.website_builder_lambda.grant_invoke(self.report_pipeline_lambda)

        # Lambda 1: invoke Bedrock models via inference profiles
        self.report_pipeline_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    self.inference_profile_a.get_att("InferenceProfileArn").to_string(),
                    self.inference_profile_b.get_att("InferenceProfileArn").to_string(),
                ],
            )
        )

        # Lambda 2: read data bucket
        self.data_bucket.grant_read(self.website_builder_lambda)

        # Lambda 2: write website bucket
        self.website_bucket.grant_read_write(self.website_builder_lambda)

        # Lambda 2: create CloudFront invalidation
        self.website_builder_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "cloudfront:CreateInvalidation",
                ],
                resources=[
                    f"arn:aws:cloudfront::{Aws.ACCOUNT_ID}:distribution/{self.distribution.distribution_id}",
                ],
            )
        )

        # ─── CloudWatch Alarms ────────────────────────────────────────────
        # All alarms use GREATER_THAN_OR_EQUAL_TO_THRESHOLD and evaluate
        # over 1 period (1 invocation = 1 data point since these run daily).

        # Lambda 1 - Errors: invocation failed
        self.lambda1_errors_alarm = cloudwatch.Alarm(
            self,
            "Lambda1ErrorsAlarm",
            alarm_name="Lambda1-Errors",
            metric=self.report_pipeline_lambda.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="Lambda 1 invocation failed",
        )

        # Lambda 1 - Timeout: approaching 15-min limit
        self.lambda1_timeout_alarm = cloudwatch.Alarm(
            self,
            "Lambda1TimeoutAlarm",
            alarm_name="Lambda1-Timeout",
            metric=self.report_pipeline_lambda.metric_duration(),
            threshold=840000,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="Lambda 1 approaching 15-min limit",
        )

        # Lambda 1 - Duration: taking unusually long
        self.lambda1_duration_alarm = cloudwatch.Alarm(
            self,
            "Lambda1DurationAlarm",
            alarm_name="Lambda1-Duration",
            metric=self.report_pipeline_lambda.metric_duration(),
            threshold=720000,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="Lambda 1 taking unusually long",
        )

        # Lambda 2 - Errors: website build failed
        self.lambda2_errors_alarm = cloudwatch.Alarm(
            self,
            "Lambda2ErrorsAlarm",
            alarm_name="Lambda2-Errors",
            metric=self.website_builder_lambda.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="Lambda 2 (website build) failed",
        )

        # Lambda 2 - Timeout: approaching 10-min limit
        self.lambda2_timeout_alarm = cloudwatch.Alarm(
            self,
            "Lambda2TimeoutAlarm",
            alarm_name="Lambda2-Timeout",
            metric=self.website_builder_lambda.metric_duration(),
            threshold=540000,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="Lambda 2 approaching 10-min limit",
        )

        # ─── Stack Outputs ────────────────────────────────────────────────
        CfnOutput(
            self,
            "WebsiteUrl",
            value=f"https://{self.distribution.distribution_domain_name}",
            description="AI Radar AWS website URL",
        )
        CfnOutput(
            self,
            "CloudFrontDistributionId",
            value=self.distribution.distribution_id,
            description="CloudFront distribution ID",
        )

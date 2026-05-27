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
    aws_apigatewayv2 as apigwv2,
    aws_budgets as budgets,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cloudwatch,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
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

        # ─── S3 Logs Bucket (CloudFront Access Logs) ──────────────────────
        # CloudFront standard logging requires ACL-enabled bucket
        self.logs_bucket = s3.Bucket(
            self,
            "LogsBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            object_ownership=s3.ObjectOwnership.OBJECT_WRITER,
            lifecycle_rules=[
                # P1: Auto-delete analytics events after 90 days
                s3.LifecycleRule(
                    id="ExpireAnalyticsEvents",
                    prefix="events/",
                    expiration=Duration.days(90),
                ),
                # Auto-delete CloudFront logs after 90 days
                s3.LifecycleRule(
                    id="ExpireCloudFrontLogs",
                    prefix="cloudfront/",
                    expiration=Duration.days(90),
                ),
                # Auto-delete Athena query results after 7 days
                s3.LifecycleRule(
                    id="ExpireAthenaResults",
                    prefix="athena-results/",
                    expiration=Duration.days(7),
                ),
            ],
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

        # LLM C - Tagger (Claude Haiku 4.5)
        self.inference_profile_c = CfnResource(
            self,
            "InferenceProfileC",
            type="AWS::Bedrock::ApplicationInferenceProfile",
            properties={
                "InferenceProfileName": config.llm_c_inference_profile_name,
                "ModelSource": {
                    "CopyFrom": f"arn:aws:bedrock:{config.aws_region}::inference-profile/{config.llm_c_model_id}",
                },
                "Tags": [
                    {"Key": "Project", "Value": "ai-radar-aws"},
                    {"Key": "Purpose", "Value": "tagging"},
                    {"Key": "Model", "Value": "claude-haiku-4-5"},
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
                    "cdk.out/**",
                    "node_modules/*",
                    "__pycache__/*",
                    "docs/*",
                    "scripts/*",
                    "*.pyc",
                    ".venv/*",
                ],
            ),
            timeout=Duration.minutes(10),
            memory_size=2048,
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
                        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
                        "style-src 'self' 'unsafe-inline'; "
                        "img-src 'self' data:; "
                        "connect-src 'self' https://*.execute-api.us-east-1.amazonaws.com"
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
        # Optional custom domain via CDK context (set in cdk.json):
        #   custom_domain: "your-site.example.com"
        #   certificate_arn: "arn:aws:acm:us-east-1:...:certificate/..."
        #   hosted_zone_id: "Z0123456789..."
        custom_domain = self.node.try_get_context("custom_domain")
        certificate_arn = self.node.try_get_context("certificate_arn")
        hosted_zone_id = self.node.try_get_context("hosted_zone_id")

        # Build distribution kwargs (conditionally add domain/certificate)
        distribution_kwargs = {
            "default_behavior": cloudfront.BehaviorOptions(
                origin=origins.S3Origin(self.website_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=self.response_headers_policy,
            ),
            "default_root_object": "index.html",
            "minimum_protocol_version": cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            "web_acl_id": self.waf_web_acl.attr_arn,
            "enable_logging": True,
            "log_bucket": self.logs_bucket,
            "log_file_prefix": "cloudfront/",
        }

        if custom_domain and certificate_arn:
            certificate = acm.Certificate.from_certificate_arn(
                self, "CustomDomainCert", certificate_arn
            )
            distribution_kwargs["domain_names"] = [custom_domain]
            distribution_kwargs["certificate"] = certificate

        self.distribution = cloudfront.Distribution(
            self, "WebsiteDistribution", **distribution_kwargs
        )

        # Optional: Route 53 alias record for custom domain
        if custom_domain and hosted_zone_id:
            hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
                self, "CustomDomainZone",
                hosted_zone_id=hosted_zone_id,
                zone_name=".".join(custom_domain.split(".")[1:]),  # e.g., "vonikakv.people.aws.dev"
            )
            route53.ARecord(
                self, "CustomDomainRecord",
                zone=hosted_zone,
                record_name=custom_domain,
                target=route53.RecordTarget.from_alias(
                    route53_targets.CloudFrontTarget(self.distribution)
                ),
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
                    "cdk.out/**",
                    "node_modules/*",
                    "__pycache__/*",
                    "docs/*",
                    "scripts/*",
                    "*.pyc",
                    ".venv/*",
                ],
            ),
            timeout=Duration.minutes(15),
            memory_size=2048,
            environment={
                "DATA_BUCKET_NAME": self.data_bucket.bucket_name,
                "WEBSITE_BUILDER_FUNCTION_NAME": self.website_builder_lambda.function_name,
                "INFERENCE_PROFILE_A_ARN": self.inference_profile_a.get_att(
                    "InferenceProfileArn"
                ).to_string(),
                "INFERENCE_PROFILE_B_ARN": self.inference_profile_b.get_att(
                    "InferenceProfileArn"
                ).to_string(),
                "INFERENCE_PROFILE_C_ARN": self.inference_profile_c.get_att(
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
        # Bedrock requires permissions on both the application inference profile
        # AND the underlying foundation models/system inference profiles
        self.report_pipeline_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    # Application inference profiles (created by this stack)
                    self.inference_profile_a.get_att("InferenceProfileArn").to_string(),
                    self.inference_profile_b.get_att("InferenceProfileArn").to_string(),
                    self.inference_profile_c.get_att("InferenceProfileArn").to_string(),
                    # System-defined cross-region inference profiles
                    f"arn:aws:bedrock:*::inference-profile/{config.llm_a_model_id}",
                    f"arn:aws:bedrock:*::inference-profile/{config.llm_b_model_id}",
                    f"arn:aws:bedrock:*::inference-profile/{config.llm_c_model_id}",
                    # Underlying foundation models (Bedrock resolves to these)
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-4-6-v1",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
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

        # P2: CloudFront request volume alarm (unusual traffic spike)
        self.cloudfront_requests_alarm = cloudwatch.Alarm(
            self,
            "CloudFrontRequestsAlarm",
            alarm_name="CloudFront-HighRequestVolume",
            metric=cloudwatch.Metric(
                namespace="AWS/CloudFront",
                metric_name="Requests",
                dimensions_map={
                    "DistributionId": self.distribution.distribution_id,
                    "Region": "Global",
                },
                statistic="Sum",
                period=Duration.hours(1),
            ),
            threshold=10000,  # 10K requests per hour is unusual for this site
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="CloudFront receiving unusually high request volume (possible DDoS)",
        )

        # ─── Analytics Lambda (Event Collector) ────────────────────────────
        self.analytics_lambda = lambda_.Function(
            self,
            "AnalyticsLambda",
            function_name="ai-radar-analytics",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="src.analytics.handler.handler",
            code=lambda_.Code.from_asset(
                ".",
                exclude=[
                    ".git/*",
                    ".hypothesis/*",
                    ".kiro/*",
                    ".pytest_cache/*",
                    "tests/*",
                    "infrastructure/*",
                    "cdk.out/**",
                    "node_modules/*",
                    "__pycache__/*",
                    "docs/*",
                    "scripts/*",
                    "*.pyc",
                    ".venv/*",
                ],
            ),
            timeout=Duration.seconds(10),
            memory_size=128,
            environment={
                "LOGS_BUCKET_NAME": self.logs_bucket.bucket_name,
            },
        )

        # Analytics Lambda: write to logs bucket (events/ prefix)
        self.logs_bucket.grant_write(self.analytics_lambda)

        # ─── HTTP API Gateway (Analytics Events) ──────────────────────────
        self.analytics_api = apigwv2.CfnApi(
            self,
            "AnalyticsApi",
            name="ai-radar-analytics-api",
            protocol_type="HTTP",
            cors_configuration=apigwv2.CfnApi.CorsProperty(
                allow_origins=[f"https://{self.distribution.distribution_domain_name}"]
                + ([f"https://{custom_domain}"] if custom_domain else []),
                allow_methods=["POST", "OPTIONS"],
                allow_headers=["Content-Type"],
            ),
        )

        # Auto-deploy stage with throttling (P1: rate limiting)
        self.analytics_stage = apigwv2.CfnStage(
            self,
            "AnalyticsApiStage",
            api_id=self.analytics_api.ref,
            stage_name="$default",
            auto_deploy=True,
            default_route_settings=apigwv2.CfnStage.RouteSettingsProperty(
                throttling_burst_limit=100,
                throttling_rate_limit=50,
            ),
        )

        # Lambda integration
        self.analytics_integration = apigwv2.CfnIntegration(
            self,
            "AnalyticsIntegration",
            api_id=self.analytics_api.ref,
            integration_type="AWS_PROXY",
            integration_uri=self.analytics_lambda.function_arn,
            payload_format_version="2.0",
        )

        # POST /events route
        self.analytics_route = apigwv2.CfnRoute(
            self,
            "AnalyticsRoute",
            api_id=self.analytics_api.ref,
            route_key="POST /events",
            target=f"integrations/{self.analytics_integration.ref}",
        )

        # Grant API Gateway permission to invoke the analytics Lambda
        self.analytics_lambda.add_permission(
            "ApiGatewayInvoke",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=f"arn:aws:execute-api:{Aws.REGION}:{Aws.ACCOUNT_ID}:{self.analytics_api.ref}/*/*",
        )

        # Construct the API URL
        analytics_api_url = f"https://{self.analytics_api.ref}.execute-api.{Aws.REGION}.amazonaws.com"

        # Set analytics API URL on website builder Lambda
        self.website_builder_lambda.add_environment(
            "ANALYTICS_API_URL",
            analytics_api_url,
        )

        # ─── AWS Budget (P2: Cost Anomaly Detection) ──────────────────────
        # Alert if daily spend exceeds $20 (catches DDoS cost spikes)
        self.daily_budget = budgets.CfnBudget(
            self,
            "DailySpendBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="AiRadar-DailySpend",
                budget_type="COST",
                time_unit="DAILY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=20,
                    unit="USD",
                ),
            ),
        )

        # ─── Stack Outputs ────────────────────────────────────────────────
        CfnOutput(
            self,
            "WebsiteUrl",
            value=f"https://{custom_domain}" if custom_domain else f"https://{self.distribution.distribution_domain_name}",
            description="AI Radar AWS website URL",
        )
        CfnOutput(
            self,
            "CloudFrontDistributionId",
            value=self.distribution.distribution_id,
            description="CloudFront distribution ID",
        )
        CfnOutput(
            self,
            "AnalyticsApiUrl",
            value=analytics_api_url,
            description="Analytics event collection API URL",
        )
        CfnOutput(
            self,
            "LogsBucketName",
            value=self.logs_bucket.bucket_name,
            description="CloudFront access logs bucket name",
        )

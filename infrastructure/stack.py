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
    aws_cloudwatch_actions as cw_actions,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
    aws_s3 as s3,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subscriptions,
    aws_wafv2 as wafv2,
    CfnResource,
)
from constructs import Construct

from src.config import Config

# Shared exclude list for Lambda asset bundling (single source — was duplicated
# three times, which is how entries drifted). Two hard-learned rules:
#   1. "cdk.out/**" does NOT match dot-directories like cdk.out/.cache, whose
#      multi-GB zip caches recursively snowballed past Node's 2 GiB limit and
#      broke deploys. Exclude the directory itself, not its contents.
#   2. Never ship local artefacts or secrets: archives, backups, env files,
#      keys, local context, or CSV data (runtime reads data from S3, never
#      from the bundle). Plan: docs/audit-remediation-plan.md item 10.
LAMBDA_ASSET_EXCLUDES = [
    # VCS / caches / tooling
    ".git",
    ".hypothesis",
    ".kiro",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "cdk.out",
    "node_modules",
    "__pycache__",
    "*.pyc",
    ".DS_Store",
    # Not needed at runtime
    "tests",
    "infrastructure",
    "docs",
    "scripts",
    ".vscode",
    "tmp",
    "temp",
    # Local artefacts and secrets — never ship
    "*.zip",
    "backups",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.pptx",
    "*.csv",
    "cdk.context.json",
    "cdk.context.json.example",
]


class AiRadarAwsStack(Stack):
    """CDK Stack for the AI Radar AWS platform."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        config = Config()

        # ─── S3 Data Bucket ───────────────────────────────────────────────
        # Stores announcement CSV and error records — the only stateful,
        # non-reproducible data in the system (every row contains paid-for
        # LLM output). Versioned so a bad overwrite is recoverable, and
        # RETAINed so `cdk destroy` cannot delete it as a side effect;
        # deploy.sh --destroy offers explicit, separately-confirmed removal.
        self.data_bucket = s3.Bucket(
            self,
            "DataBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,  # AES-256
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
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
            code=lambda_.Code.from_asset(".", exclude=LAMBDA_ASSET_EXCLUDES),
            timeout=Duration.minutes(10),
            memory_size=1024,
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

        # ─── Analytics HTTP API (created early: its URL is pinned in CSP) ─
        # The API resource itself is declared here so the response headers
        # policy below can reference its exact hostname. Its CORS config,
        # integration, route and stage are attached later in the analytics
        # section (CORS references the distribution domain, which references
        # this policy — declaring CORS here would be a dependency cycle).
        self.analytics_api = apigwv2.CfnApi(
            self,
            "AnalyticsApi",
            name="ai-radar-analytics-api",
            protocol_type="HTTP",
        )
        analytics_api_host = (
            f"{self.analytics_api.ref}.execute-api.{Aws.REGION}.amazonaws.com"
        )

        # ─── CloudFront Response Headers Policy ───────────────────────────
        # Security headers: CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy
        #
        # CSP notes (docs/audit-remediation-plan.md item 11):
        # - connect-src pins THIS deployment's API Gateway hostname; the old
        #   wildcard *.execute-api.us-east-1.amazonaws.com authorised any AWS
        #   customer's API as an exfiltration destination.
        # - 'unsafe-inline'/'unsafe-eval' are required by the current setup:
        #   Mermaid and Chart.js are initialised via inline <script> blocks
        #   and Mermaid evaluates dynamically. Removing them would need the
        #   inline init extracted to a served .js file plus hash/nonce-based
        #   policy — deliberate, revisit if the page gains any user input.
        # - cdnjs.cloudflare.com was removed: it served html2pdf.js, which
        #   commit aeb6682 replaced with browser-native print.
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
                        f"connect-src 'self' https://{analytics_api_host}"
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
                zone_name=".".join(custom_domain.split(".")[1:]),  # parent zone, e.g. "example.com" for "news.example.com"
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
            code=lambda_.Code.from_asset(".", exclude=LAMBDA_ASSET_EXCLUDES),
            timeout=Duration.minutes(15),
            memory_size=1024,
            # Exactly one run at a time: the pipeline does read-modify-write
            # on the CSV, so a manual run overlapping the scheduled one would
            # silently lose the other's writes (last writer wins). A second
            # invocation now fails fast with a throttle instead.
            # Plan: docs/audit-remediation-plan.md item 4.
            reserved_concurrent_executions=1,
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

        # Per-deployment runtime override (from gitignored cdk.context.json).
        # Absent context → Config's generic default applies. See README:
        # "Configuring Your Own Deployment".
        preferred_geography = self.node.try_get_context("preferred_geography")
        if preferred_geography:
            self.report_pipeline_lambda.add_environment(
                "PREFERRED_GEOGRAPHY", str(preferred_geography)
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

        # ─── Alerting: SNS topic wired to every alarm ─────────────────────
        # The topic and alarm wiring are ALWAYS created — a fresh clone gets
        # alarms genuinely wired to a topic needing one manual subscription.
        # The email subscription is added only when alert_email is set in the
        # gitignored cdk.context.json (deploy.sh warns when it is not).
        # Plan: docs/audit-remediation-plan.md item 2.
        self.alert_topic = sns.Topic(
            self,
            "AlertTopic",
            topic_name="ai-radar-alerts",
            display_name="AI Radar AWS Alerts",
        )

        alert_email = self.node.try_get_context("alert_email")
        if alert_email:
            self.alert_topic.add_subscription(
                sns_subscriptions.EmailSubscription(str(alert_email))
            )

        for alarm in (
            self.lambda1_errors_alarm,
            self.lambda1_timeout_alarm,
            self.lambda1_duration_alarm,
            self.lambda2_errors_alarm,
            self.lambda2_timeout_alarm,
            self.cloudfront_requests_alarm,
        ):
            alarm.add_alarm_action(cw_actions.SnsAction(self.alert_topic))

        # ─── Analytics Lambda (Event Collector) ────────────────────────────
        self.analytics_lambda = lambda_.Function(
            self,
            "AnalyticsLambda",
            function_name="ai-radar-analytics",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="src.analytics.handler.handler",
            code=lambda_.Code.from_asset(".", exclude=LAMBDA_ASSET_EXCLUDES),
            timeout=Duration.seconds(10),
            memory_size=128,
            environment={
                "LOGS_BUCKET_NAME": self.logs_bucket.bucket_name,
                # Echoed as Access-Control-Allow-Origin (item 12); "*" when
                # no custom domain is configured.
                "ALLOWED_ORIGIN": (
                    f"https://{custom_domain}" if custom_domain else "*"
                ),
            },
        )

        # Analytics Lambda: write to logs bucket (events/ prefix)
        self.logs_bucket.grant_write(self.analytics_lambda)

        # ─── HTTP API Gateway (Analytics Events) ──────────────────────────
        # The CfnApi resource is declared earlier (its hostname is pinned in
        # the CSP connect-src). CORS must NOT reference the distribution's
        # domain: policy→API→distribution→policy is a CloudFormation cycle.
        # With a custom domain configured, that static value is the allowed
        # origin (the canonical site URL). Without one, fall back to "*" —
        # CORS is browser-side courtesy on an endpoint that is publicly
        # writable by design; the real limits are stage throttling (item 12)
        # and the handler's validation.
        self.analytics_api.cors_configuration = apigwv2.CfnApi.CorsProperty(
            allow_origins=(
                [f"https://{custom_domain}"] if custom_domain else ["*"]
            ),
            allow_methods=["POST", "OPTIONS"],
            allow_headers=["Content-Type"],
        )

        # Auto-deploy stage with throttling (P1: rate limiting)
        self.analytics_stage = apigwv2.CfnStage(
            self,
            "AnalyticsApiStage",
            api_id=self.analytics_api.ref,
            stage_name="$default",
            auto_deploy=True,
            # Throttling is the primary abuse control on this endpoint (WAF
            # deferred — docs/audit-remediation-plan.md item 12, D5). 5 rps
            # sustained is far above legitimate traffic for this site; the
            # budget alarm (item 2) is the cost backstop and the 90-day
            # lifecycle bounds storage.
            default_route_settings=apigwv2.CfnStage.RouteSettingsProperty(
                throttling_burst_limit=20,
                throttling_rate_limit=5,
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
        # Alert if daily spend exceeds $20 (catches DDoS cost spikes).
        # Without a NotificationsWithSubscribers block, AWS Budgets sends
        # NOTHING — so the notification is attached whenever alert_email is
        # configured. Plan: docs/audit-remediation-plan.md item 2.
        budget_notifications = None
        if alert_email:
            budget_notifications = [
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="ACTUAL",
                        threshold=100,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="EMAIL",
                            address=str(alert_email),
                        )
                    ],
                )
            ]

        # No explicit budget_name: NotificationsWithSubscribers is create-only,
        # so any change replaces the budget — and a fixed name makes the
        # replacement's create collide with the existing budget ("same name but
        # a different internalId"). A generated name avoids that class forever.
        self.daily_budget = budgets.CfnBudget(
            self,
            "DailySpendBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST",
                time_unit="DAILY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=20,
                    unit="USD",
                ),
            ),
            notifications_with_subscribers=budget_notifications,
        )

        # ─── Analytics Rollup Lambda (Monthly History) ────────────────────
        # Aggregates each completed calendar month's analytics into permanent
        # artifacts under s3://<logs-bucket>/rollups/ before the 90-day
        # lifecycle rules delete the raw logs. Runs on the 3rd at 03:00 UTC:
        # ~51h settle delay for CloudFront log delivery, and the whole target
        # month stays inside the 90-day raw window with ~25 days of slack.
        # Spec: .kiro/specs/monthly-analytics-rollup/design.md
        self.rollup_lambda = lambda_.Function(
            self,
            "AnalyticsRollupLambda",
            function_name="ai-radar-analytics-rollup",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="src.analytics_rollup.handler.handler",
            code=lambda_.Code.from_asset(".", exclude=LAMBDA_ASSET_EXCLUDES),
            timeout=Duration.minutes(15),
            memory_size=256,
            # A queued duplicate run is an idempotent replace, but two
            # concurrent runs writing the same keys would be pointless churn.
            reserved_concurrent_executions=1,
            environment={
                "LOGS_BUCKET_NAME": self.logs_bucket.bucket_name,
                "DATA_BUCKET_NAME": self.data_bucket.bucket_name,
            },
        )

        self.rollup_schedule_rule = events.Rule(
            self,
            "MonthlyRollupRule",
            schedule=events.Schedule.cron(
                minute="0", hour="3", day="3", month="*", year="*"
            ),
        )
        self.rollup_schedule_rule.add_target(
            # Two async retries on error/timeout; safe because re-runs are
            # idempotent replaces (retry spacing is platform-managed).
            targets.LambdaFunction(self.rollup_lambda, retry_attempts=2)
        )

        # Least privilege: read raw prefixes, read+put (no delete) on
        # rollups/ and athena-results/, read-only on the catalog CSV.
        self.logs_bucket.grant_read(self.rollup_lambda, "cloudfront/*")
        self.logs_bucket.grant_read(self.rollup_lambda, "events/*")
        self.logs_bucket.grant_read(self.rollup_lambda, "rollups/*")
        self.logs_bucket.grant_put(self.rollup_lambda, "rollups/*")
        self.logs_bucket.grant_read(self.rollup_lambda, "athena-results/*")
        self.logs_bucket.grant_put(self.rollup_lambda, "athena-results/*")
        self.data_bucket.grant_read(self.rollup_lambda, "database/announcements.csv")

        # Athena on the primary workgroup + Glue catalog objects needed by
        # the three CREATE ... IF NOT EXISTS statements.
        self.rollup_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                ],
                resources=[
                    f"arn:aws:athena:{Aws.REGION}:{Aws.ACCOUNT_ID}:workgroup/primary",
                ],
            )
        )
        self.rollup_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "glue:CreateDatabase",
                    "glue:GetDatabase",
                    "glue:CreateTable",
                    "glue:GetTable",
                    "glue:GetPartitions",
                ],
                resources=[
                    f"arn:aws:glue:{Aws.REGION}:{Aws.ACCOUNT_ID}:catalog",
                    f"arn:aws:glue:{Aws.REGION}:{Aws.ACCOUNT_ID}:database/ai_radar_analytics",
                    f"arn:aws:glue:{Aws.REGION}:{Aws.ACCOUNT_ID}:table/ai_radar_analytics/*",
                ],
            )
        )

        # Rollup alarms → the existing alert topic. A literal "no invocation
        # in 31 days" heartbeat alarm is not expressible (CloudWatch caps an
        # alarm's evaluation range at one day), so failed-run coverage is:
        # Lambda Errors (fired but failed, incl. all retries) + EventBridge
        # FailedInvocations (rule fired but could not invoke the target).
        # Design amendment A3 records this decision.
        self.rollup_errors_alarm = cloudwatch.Alarm(
            self,
            "RollupErrorsAlarm",
            alarm_name="Rollup-Errors",
            metric=self.rollup_lambda.metric_errors(),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="Monthly analytics rollup failed",
        )
        self.rollup_failed_invocations_alarm = cloudwatch.Alarm(
            self,
            "RollupFailedInvocationsAlarm",
            alarm_name="Rollup-FailedInvocations",
            metric=cloudwatch.Metric(
                namespace="AWS/Events",
                metric_name="FailedInvocations",
                dimensions_map={"RuleName": self.rollup_schedule_rule.rule_name},
                statistic="Sum",
                period=Duration.hours(1),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            alarm_description="Monthly rollup schedule could not invoke its target",
        )
        for alarm in (self.rollup_errors_alarm, self.rollup_failed_invocations_alarm):
            alarm.add_alarm_action(cw_actions.SnsAction(self.alert_topic))

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

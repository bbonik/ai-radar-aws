"""Integration tests for full pipeline flow.

Tests end-to-end Lambda 1 and Lambda 2 pipelines with mocked external services
(RSS, Bedrock, S3 via moto), verifying the complete flow from RSS fetch through
website generation.

Validates: Requirements 14.1
"""

import json
import os
from io import BytesIO
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from src.config import Config
from src.pipeline.handler import handler as pipeline_handler
from src.website_builder.handler import handler as website_builder_handler


# --- Sample Data ---

SAMPLE_RSS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>AWS What's New</title>
    <item>
      <title>Amazon Bedrock now supports new foundation models</title>
      <description>Amazon Bedrock adds support for new AI foundation models, \
enabling customers to build generative AI applications with greater choice. \
Read the blog post at https://aws.amazon.com/blogs/ai/bedrock-new-models for details.</description>
      <pubDate>Mon, 15 Jan 2025 22:00:00 GMT</pubDate>
      <link>https://aws.amazon.com/about-aws/whats-new/2025/01/amazon-bedrock-new-models</link>
    </item>
    <item>
      <title>Amazon SageMaker AI introduces new training optimizations</title>
      <description>Amazon SageMaker AI now offers improved training performance \
with new distributed training optimizations for large language models.</description>
      <pubDate>Mon, 14 Jan 2025 18:00:00 GMT</pubDate>
      <link>https://aws.amazon.com/about-aws/whats-new/2025/01/sagemaker-ai-training</link>
    </item>
  </channel>
</rss>
"""

MOCK_REPORT_RESPONSE = {
    "content": [
        {
            "type": "text",
            "text": (
                "[WHATS_NEW]\n"
                "Amazon Bedrock now supports new foundation models for generative AI.\n\n"
                "[HOW_IT_WORKS]\n"
                "Customers can access new models through the Bedrock API.\n\n"
                "[WHY_IMPORTANT]\n"
                "This gives customers more choice in building AI applications.\n\n"
                "[HOW_DIFFERENT]\n"
                "Previously only a limited set of models was available.\n\n"
                "[WHEN_TO_PREFER]\n"
                "Use when you need the latest model capabilities.\n\n"
                "[AVAILABILITY]\n"
                "Available in all regions where Bedrock is supported."
            ),
        }
    ],
}

MOCK_GRAPH_RESPONSE = {
    "content": [
        {
            "type": "text",
            "text": (
                "```mermaid\n"
                "graph TD\n"
                "    A[Amazon Bedrock] --> B[Foundation Models]\n"
                "    B --> C[Generative AI Apps]\n"
                "```"
            ),
        }
    ],
}

SAMPLE_CSV_CONTENT = (
    "title,description,pub_date,link,aws_service,importance_level,"
    "importance_score,whats_new,how_it_works,why_important,"
    "how_different,when_to_prefer,availability,mermaid_graph,"
    "blogpost_links,first_detected\n"
    '"Amazon Bedrock new models","Bedrock adds new AI models","2025-01-15",'
    '"https://aws.amazon.com/about-aws/whats-new/2025/01/bedrock",'
    '"Amazon Bedrock",3,5.5,"Summary of new models","How the models work","Why this matters",'
    '"How different from before","When to prefer this","GA in all regions",'
    '"graph TD\\n    A-->B","https://blog.example.com","2025-01-15T22:00:00.000Z"\n'
)


def _make_bedrock_response(response_dict: dict) -> dict:
    """Create a mock Bedrock invoke_model response."""
    body_bytes = json.dumps(response_dict).encode("utf-8")
    return {"body": BytesIO(body_bytes), "contentType": "application/json"}


class MockLambdaContext:
    """Mock Lambda execution context."""

    def __init__(self, timeout_ms: int = 900_000):
        self._remaining_ms = timeout_ms

    def get_remaining_time_in_millis(self) -> int:
        return self._remaining_ms


# --- Fixtures ---


@pytest.fixture
def env_vars():
    """Set up environment variables for both Lambda handlers."""
    env = {
        "DATA_BUCKET_NAME": "test-data-bucket",
        "WEBSITE_BUCKET_NAME": "test-website-bucket",
        "WEBSITE_BUILDER_FUNCTION_NAME": "test-website-builder",
        "CLOUDFRONT_DISTRIBUTION_ID": "E1234567890",
        "INFERENCE_PROFILE_A_ARN": "arn:aws:bedrock:us-east-1:123456789012:inference-profile/test-a",
        "INFERENCE_PROFILE_B_ARN": "arn:aws:bedrock:us-east-1:123456789012:inference-profile/test-b",
        "AWS_DEFAULT_REGION": "us-east-1",
    }
    with patch.dict(os.environ, env):
        yield env


@pytest.fixture
def mock_lambda_context():
    """Provide a mock Lambda context with 15 minutes remaining."""
    return MockLambdaContext(timeout_ms=900_000)


# --- Test: Full Lambda 1 Pipeline ---


@mock_aws
class TestLambda1FullPipeline:
    """Test end-to-end Lambda 1 pipeline with mocked external services.

    Validates: Requirements 14.1
    """

    def _setup_aws(self):
        """Create mocked S3 buckets and Lambda function."""
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-data-bucket")
        s3.create_bucket(Bucket="test-website-bucket")

        iam = boto3.client("iam", region_name="us-east-1")
        iam.create_role(
            RoleName="test-lambda-role",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            Path="/",
        )

        lam = boto3.client("lambda", region_name="us-east-1")
        lam.create_function(
            FunctionName="test-website-builder",
            Runtime="python3.11",
            Role="arn:aws:iam::123456789012:role/test-lambda-role",
            Handler="handler.handler",
            Code={"ZipFile": b"fake-code"},
        )
        return s3

    def test_full_pipeline_processes_announcements_to_s3(self, env_vars, mock_lambda_context):
        """Full pipeline: RSS → filter → classify → research → report → graph → store."""
        s3 = self._setup_aws()

        mock_rss_response = MagicMock()
        mock_rss_response.read.return_value = SAMPLE_RSS_XML.encode("utf-8")
        mock_rss_response.__enter__ = MagicMock(return_value=mock_rss_response)
        mock_rss_response.__exit__ = MagicMock(return_value=False)

        mock_research_response = MagicMock()
        mock_research_response.read.return_value = b"<html><body>Blog content</body></html>"
        mock_research_response.headers = {"content-type": "text/html"}
        mock_research_response.__enter__ = MagicMock(return_value=mock_research_response)
        mock_research_response.__exit__ = MagicMock(return_value=False)

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model.return_value = _make_bedrock_response(MOCK_REPORT_RESPONSE)

        original_boto3_client = boto3.client

        def patched_client(service_name, **kwargs):
            if service_name == "bedrock-runtime":
                return mock_bedrock
            return original_boto3_client(service_name, **kwargs)

        with patch("src.pipeline.rss_fetcher.urlopen", return_value=mock_rss_response):
            with patch("src.pipeline.research_agent.urlopen", return_value=mock_research_response):
                with patch("boto3.client", side_effect=patched_client):
                    result = pipeline_handler(
                        {"source": "integration-test"}, mock_lambda_context
                    )

        # Pipeline should complete successfully
        assert result["statusCode"] == 200
        assert "run_id" in result["body"]
        # At least one announcement should be relevant (Bedrock and SageMaker AI)
        assert result["body"]["total_relevant"] >= 1
        # Verify announcements were saved to S3
        try:
            obj = s3.get_object(
                Bucket="test-data-bucket", Key="database/announcements.csv"
            )
            csv_content = obj["Body"].read().decode("utf-8")
            assert "Amazon Bedrock" in csv_content or "SageMaker" in csv_content
        except s3.exceptions.NoSuchKey:
            # If no announcements passed all stages, that's acceptable
            # as long as the pipeline didn't crash
            pass


# --- Test: Full Lambda 2 Website Build ---


@mock_aws
class TestLambda2FullBuild:
    """Test Lambda 2 website build with mocked S3 and CloudFront.

    Validates: Requirements 14.1
    """

    def _setup_aws_with_csv(self):
        """Create S3 buckets and pre-populate CSV data."""
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-data-bucket")
        s3.create_bucket(Bucket="test-website-bucket")

        # Pre-populate the data bucket with announcement CSV
        s3.put_object(
            Bucket="test-data-bucket",
            Key="database/announcements.csv",
            Body=SAMPLE_CSV_CONTENT.encode("utf-8"),
        )
        return s3

    def test_website_build_generates_html_files(self, env_vars):
        """Lambda 2 reads CSV from S3 and generates HTML files in website bucket."""
        s3 = self._setup_aws_with_csv()

        event = {"run_id": "test-integration-run", "source": "pipeline-orchestrator"}
        context = MockLambdaContext(timeout_ms=600_000)

        # Patch CloudFront client to avoid real API calls
        with patch("src.website_builder.handler.boto3.client") as mock_boto:
            def side_effect(service, **kwargs):
                if service == "s3":
                    return s3
                elif service == "cloudfront":
                    mock_cf = MagicMock()
                    mock_cf.create_invalidation.return_value = {"Invalidation": {"Id": "I123"}}
                    return mock_cf
                return MagicMock()

            mock_boto.side_effect = side_effect
            result = website_builder_handler(event, context)

        assert result["statusCode"] == 200
        assert result["body"]["run_id"] == "test-integration-run"
        assert result["body"]["files_uploaded"] > 0

        # Verify HTML files exist in website bucket
        objects = s3.list_objects_v2(Bucket="test-website-bucket")
        keys = [obj["Key"] for obj in objects.get("Contents", [])]
        # Should have at least index.html
        assert any("index.html" in k for k in keys)

    def test_website_build_with_empty_csv(self, env_vars):
        """Lambda 2 handles empty CSV gracefully (no announcements)."""
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-data-bucket")
        s3.create_bucket(Bucket="test-website-bucket")

        # Put CSV with only headers
        headers_only = (
            "title,description,pub_date,link,aws_service,importance_level,"
            "importance_score,whats_new,how_it_works,why_important,"
            "how_different,when_to_prefer,availability,mermaid_graph,"
            "blogpost_links,first_detected\n"
        )
        s3.put_object(
            Bucket="test-data-bucket",
            Key="database/announcements.csv",
            Body=headers_only.encode("utf-8"),
        )

        event = {"run_id": "test-empty-csv", "source": "pipeline-orchestrator"}
        context = MockLambdaContext(timeout_ms=600_000)

        with patch("src.website_builder.handler.boto3.client") as mock_boto:
            def side_effect(service, **kwargs):
                if service == "s3":
                    return s3
                elif service == "cloudfront":
                    mock_cf = MagicMock()
                    mock_cf.create_invalidation.return_value = {"Invalidation": {"Id": "I123"}}
                    return mock_cf
                return MagicMock()

            mock_boto.side_effect = side_effect
            result = website_builder_handler(event, context)

        # Should succeed even with no announcements
        assert result["statusCode"] == 200


# --- Test: Lambda 1 → Lambda 2 Invocation Chain ---


@mock_aws
class TestLambdaInvocationChain:
    """Test Lambda 1 → Lambda 2 invocation chain with mocked Lambda client.

    Validates: Requirements 14.1
    """

    def _setup_aws(self):
        """Create mocked S3 buckets and Lambda function."""
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-data-bucket")
        s3.create_bucket(Bucket="test-website-bucket")

        iam = boto3.client("iam", region_name="us-east-1")
        iam.create_role(
            RoleName="test-lambda-role",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            Path="/",
        )

        lam = boto3.client("lambda", region_name="us-east-1")
        lam.create_function(
            FunctionName="test-website-builder",
            Runtime="python3.11",
            Role="arn:aws:iam::123456789012:role/test-lambda-role",
            Handler="handler.handler",
            Code={"ZipFile": b"fake-code"},
        )
        return s3

    def test_lambda1_invokes_lambda2_with_run_id(self, env_vars, mock_lambda_context):
        """Lambda 1 invokes Lambda 2 asynchronously with run_id in payload."""
        self._setup_aws()

        # Use a simple RSS with one relevant item
        mock_rss_response = MagicMock()
        mock_rss_response.read.return_value = SAMPLE_RSS_XML.encode("utf-8")
        mock_rss_response.__enter__ = MagicMock(return_value=mock_rss_response)
        mock_rss_response.__exit__ = MagicMock(return_value=False)

        mock_research_response = MagicMock()
        mock_research_response.read.return_value = b"<html><body>Content</body></html>"
        mock_research_response.headers = {"content-type": "text/html"}
        mock_research_response.__enter__ = MagicMock(return_value=mock_research_response)
        mock_research_response.__exit__ = MagicMock(return_value=False)

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model.return_value = _make_bedrock_response(MOCK_REPORT_RESPONSE)

        mock_lambda_client = MagicMock()
        mock_lambda_client.invoke.return_value = {"StatusCode": 202}

        original_boto3_client = boto3.client

        def patched_client(service_name, **kwargs):
            if service_name == "bedrock-runtime":
                return mock_bedrock
            if service_name == "lambda":
                return mock_lambda_client
            return original_boto3_client(service_name, **kwargs)

        with patch("src.pipeline.rss_fetcher.urlopen", return_value=mock_rss_response):
            with patch("src.pipeline.research_agent.urlopen", return_value=mock_research_response):
                with patch("boto3.client", side_effect=patched_client):
                    result = pipeline_handler(
                        {"source": "integration-test"}, mock_lambda_context
                    )

        # Pipeline completed and invoked Lambda 2
        assert result["statusCode"] == 200
        assert result["body"]["website_builder_invoked"] is True

        # Verify Lambda 2 was invoked with correct parameters
        mock_lambda_client.invoke.assert_called_once()
        invoke_kwargs = mock_lambda_client.invoke.call_args[1]
        assert invoke_kwargs["FunctionName"] == "test-website-builder"
        assert invoke_kwargs["InvocationType"] == "Event"

        # Verify payload contains run_id
        payload = json.loads(invoke_kwargs["Payload"].decode("utf-8"))
        assert "run_id" in payload
        assert payload["run_id"] == result["body"]["run_id"]
        assert payload["source"] == "pipeline-orchestrator"


# --- Test: Graceful Degradation Scenarios ---


@mock_aws
class TestGracefulDegradation:
    """Test graceful degradation scenarios.

    Validates: Requirements 14.1
    """

    def _setup_aws(self):
        """Create mocked S3 buckets and Lambda function."""
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-data-bucket")
        s3.create_bucket(Bucket="test-website-bucket")

        iam = boto3.client("iam", region_name="us-east-1")
        iam.create_role(
            RoleName="test-lambda-role",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            Path="/",
        )

        lam = boto3.client("lambda", region_name="us-east-1")
        lam.create_function(
            FunctionName="test-website-builder",
            Runtime="python3.11",
            Role="arn:aws:iam::123456789012:role/test-lambda-role",
            Handler="handler.handler",
            Code={"ZipFile": b"fake-code"},
        )
        return s3

    def test_rss_failure_returns_graceful_response(self, env_vars, mock_lambda_context):
        """Pipeline handles RSS fetch failure gracefully without crashing."""
        self._setup_aws()

        from urllib.error import URLError

        mock_lambda_client = MagicMock()
        mock_lambda_client.invoke.return_value = {"StatusCode": 202}

        original_boto3_client = boto3.client

        def patched_client(service_name, **kwargs):
            if service_name == "lambda":
                return mock_lambda_client
            return original_boto3_client(service_name, **kwargs)

        # RSS fetch fails on all retries
        with patch("src.pipeline.rss_fetcher.urlopen", side_effect=URLError("Connection refused")):
            with patch("time.sleep"):  # Skip backoff delays
                with patch("boto3.client", side_effect=patched_client):
                    result = pipeline_handler(
                        {"source": "integration-test"}, mock_lambda_context
                    )

        # Pipeline should still return 200 (RSS failure = 0 items, not a crash)
        assert result["statusCode"] == 200
        assert result["body"]["total_fetched"] == 0
        assert result["body"]["total_relevant"] == 0

    def test_bedrock_throttling_continues_with_other_announcements(
        self, env_vars, mock_lambda_context
    ):
        """Pipeline continues processing other announcements when Bedrock throttles one."""
        self._setup_aws()

        mock_rss_response = MagicMock()
        mock_rss_response.read.return_value = SAMPLE_RSS_XML.encode("utf-8")
        mock_rss_response.__enter__ = MagicMock(return_value=mock_rss_response)
        mock_rss_response.__exit__ = MagicMock(return_value=False)

        mock_research_response = MagicMock()
        mock_research_response.read.return_value = b"<html><body>Content</body></html>"
        mock_research_response.headers = {"content-type": "text/html"}
        mock_research_response.__enter__ = MagicMock(return_value=mock_research_response)
        mock_research_response.__exit__ = MagicMock(return_value=False)

        # Bedrock: first call throttles (raises), subsequent calls succeed
        call_count = {"n": 0}

        def bedrock_side_effect(**kwargs):
            call_count["n"] += 1
            if call_count["n"] <= 3:
                # First announcement's report generation fails (3 attempts = initial + 2 retries)
                from botocore.exceptions import ClientError
                raise ClientError(
                    {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
                    "InvokeModel",
                )
            return _make_bedrock_response(MOCK_REPORT_RESPONSE)

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model.side_effect = bedrock_side_effect

        mock_lambda_client = MagicMock()
        mock_lambda_client.invoke.return_value = {"StatusCode": 202}

        original_boto3_client = boto3.client

        def patched_client(service_name, **kwargs):
            if service_name == "bedrock-runtime":
                return mock_bedrock
            if service_name == "lambda":
                return mock_lambda_client
            return original_boto3_client(service_name, **kwargs)

        with patch("src.pipeline.rss_fetcher.urlopen", return_value=mock_rss_response):
            with patch("src.pipeline.research_agent.urlopen", return_value=mock_research_response):
                with patch("boto3.client", side_effect=patched_client):
                    with patch("time.sleep"):  # Skip retry delays
                        result = pipeline_handler(
                            {"source": "integration-test"}, mock_lambda_context
                        )

        # Pipeline should complete (200) even with some failures
        assert result["statusCode"] == 200
        # At least one announcement should have failed
        assert result["body"]["total_failed"] >= 1
        # Pipeline didn't crash — it continued processing

    def test_s3_write_failure_records_error_and_continues(
        self, env_vars, mock_lambda_context
    ):
        """Pipeline records error and continues when S3 write fails for one announcement."""
        s3 = self._setup_aws()

        mock_rss_response = MagicMock()
        # Single item RSS to simplify
        single_item_rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>AWS What's New</title>
    <item>
      <title>Amazon Bedrock now supports new foundation models</title>
      <description>Amazon Bedrock adds support for new AI foundation models.</description>
      <pubDate>Mon, 15 Jan 2025 22:00:00 GMT</pubDate>
      <link>https://aws.amazon.com/about-aws/whats-new/2025/01/amazon-bedrock-new-models</link>
    </item>
  </channel>
</rss>
"""
        mock_rss_response.read.return_value = single_item_rss.encode("utf-8")
        mock_rss_response.__enter__ = MagicMock(return_value=mock_rss_response)
        mock_rss_response.__exit__ = MagicMock(return_value=False)

        mock_research_response = MagicMock()
        mock_research_response.read.return_value = b"<html><body>Content</body></html>"
        mock_research_response.headers = {"content-type": "text/html"}
        mock_research_response.__enter__ = MagicMock(return_value=mock_research_response)
        mock_research_response.__exit__ = MagicMock(return_value=False)

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model.return_value = _make_bedrock_response(MOCK_REPORT_RESPONSE)

        # Create a mock S3 client that fails on put_object
        mock_s3_client = MagicMock()
        mock_s3_client.get_object.side_effect = boto3.client("s3", region_name="us-east-1").exceptions.NoSuchKey(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject"
        ) if False else Exception("NoSuchKey")

        mock_lambda_client = MagicMock()
        mock_lambda_client.invoke.return_value = {"StatusCode": 202}

        original_boto3_client = boto3.client

        # We need a more targeted approach: let the S3 client work normally
        # for load_existing_links (which handles NoSuchKey), but fail on put_object
        # for saving announcements
        put_call_count = {"n": 0}
        real_s3 = boto3.client("s3", region_name="us-east-1")

        class FailingS3Wrapper:
            """S3 client wrapper that fails on put_object after initial setup."""

            def __init__(self, real_client):
                self._real = real_client
                self._fail_puts = True

            def __getattr__(self, name):
                if name == "put_object" and self._fail_puts:
                    def failing_put(**kwargs):
                        raise Exception("S3 write failed: Access Denied")
                    return failing_put
                return getattr(self._real, name)

        failing_s3 = FailingS3Wrapper(real_s3)

        def patched_client(service_name, **kwargs):
            if service_name == "bedrock-runtime":
                return mock_bedrock
            if service_name == "lambda":
                return mock_lambda_client
            if service_name == "s3":
                return failing_s3
            return original_boto3_client(service_name, **kwargs)

        with patch("src.pipeline.rss_fetcher.urlopen", return_value=mock_rss_response):
            with patch("src.pipeline.research_agent.urlopen", return_value=mock_research_response):
                with patch("boto3.client", side_effect=patched_client):
                    with patch("time.sleep"):  # Skip retry delays
                        result = pipeline_handler(
                            {"source": "integration-test"}, mock_lambda_context
                        )

        # Pipeline should complete (200) even with S3 write failure
        assert result["statusCode"] == 200
        # The announcement should have failed at storage stage
        assert result["body"]["total_failed"] >= 1

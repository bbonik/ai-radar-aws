"""Unit tests for the Website Builder Lambda 2 handler."""

import json
from unittest.mock import MagicMock, patch, call

import pytest

from src.website_builder.handler import (
    handler,
    _staged_upload,
    _invalidate_cloudfront,
    _cleanup_staging,
    _get_content_type,
)
from src.shared.logger import StructuredLogger


class TestHandler:
    """Tests for the Lambda 2 handler function."""

    def test_handler_uses_run_id_from_event(self):
        """Handler should use run_id from Lambda 1 payload."""
        event = {"run_id": "test-run-123", "source": "pipeline-orchestrator"}

        with patch.dict("os.environ", {
            "DATA_BUCKET_NAME": "data-bucket",
            "WEBSITE_BUCKET_NAME": "website-bucket",
            "CLOUDFRONT_DISTRIBUTION_ID": "E123456",
        }):
            with patch("src.website_builder.handler.boto3") as mock_boto3:
                with patch("src.website_builder.handler.WebsiteBuilder") as mock_builder_cls:
                    mock_builder = MagicMock()
                    mock_builder.build_and_get_files.return_value = {
                        "index.html": "<html></html>",
                    }
                    mock_builder_cls.return_value = mock_builder

                    mock_s3 = MagicMock()
                    mock_cf = MagicMock()
                    mock_boto3.client.side_effect = lambda svc, **kwargs: (
                        mock_s3 if svc == "s3" else mock_cf
                    )

                    result = handler(event, MagicMock())

        assert result["statusCode"] == 200
        assert result["body"]["run_id"] == "test-run-123"

    def test_handler_generates_run_id_if_missing(self):
        """Handler should generate a UUID if run_id not in event."""
        event = {"source": "manual-test"}

        with patch.dict("os.environ", {
            "DATA_BUCKET_NAME": "data-bucket",
            "WEBSITE_BUCKET_NAME": "website-bucket",
        }):
            with patch("src.website_builder.handler.boto3") as mock_boto3:
                with patch("src.website_builder.handler.WebsiteBuilder") as mock_builder_cls:
                    mock_builder = MagicMock()
                    mock_builder.build_and_get_files.return_value = {}
                    mock_builder_cls.return_value = mock_builder

                    mock_s3 = MagicMock()
                    mock_cf = MagicMock()
                    mock_boto3.client.side_effect = lambda svc, **kwargs: (
                        mock_s3 if svc == "s3" else mock_cf
                    )

                    result = handler(event, MagicMock())

        assert result["statusCode"] == 200
        # run_id should be a UUID string
        assert len(result["body"]["run_id"]) > 0

    def test_handler_returns_500_when_env_vars_missing(self):
        """Handler should return 500 if required env vars are missing."""
        event = {"run_id": "test-123"}

        with patch.dict("os.environ", {}, clear=True):
            result = handler(event, MagicMock())

        assert result["statusCode"] == 500
        assert "Missing required environment variables" in result["body"]["error"]

    def test_handler_returns_200_with_zero_files_when_no_announcements(self):
        """Handler should return 200 with 0 files when builder produces nothing."""
        event = {"run_id": "test-123", "source": "pipeline-orchestrator"}

        with patch.dict("os.environ", {
            "DATA_BUCKET_NAME": "data-bucket",
            "WEBSITE_BUCKET_NAME": "website-bucket",
            "CLOUDFRONT_DISTRIBUTION_ID": "E123456",
        }):
            with patch("src.website_builder.handler.boto3") as mock_boto3:
                with patch("src.website_builder.handler.WebsiteBuilder") as mock_builder_cls:
                    mock_builder = MagicMock()
                    mock_builder.build_and_get_files.return_value = {}
                    mock_builder_cls.return_value = mock_builder

                    mock_s3 = MagicMock()
                    mock_cf = MagicMock()
                    mock_boto3.client.side_effect = lambda svc, **kwargs: (
                        mock_s3 if svc == "s3" else mock_cf
                    )

                    result = handler(event, MagicMock())

        assert result["statusCode"] == 200
        assert result["body"]["files_uploaded"] == 0

    def test_handler_preserves_site_on_build_failure(self):
        """On build failure, handler returns 500 and existing site is preserved."""
        event = {"run_id": "test-123", "source": "pipeline-orchestrator"}

        with patch.dict("os.environ", {
            "DATA_BUCKET_NAME": "data-bucket",
            "WEBSITE_BUCKET_NAME": "website-bucket",
            "CLOUDFRONT_DISTRIBUTION_ID": "E123456",
        }):
            with patch("src.website_builder.handler.boto3") as mock_boto3:
                with patch("src.website_builder.handler.WebsiteBuilder") as mock_builder_cls:
                    mock_builder = MagicMock()
                    mock_builder.build_and_get_files.side_effect = RuntimeError("Build failed")
                    mock_builder_cls.return_value = mock_builder

                    mock_s3 = MagicMock()
                    mock_cf = MagicMock()
                    mock_boto3.client.side_effect = lambda svc, **kwargs: (
                        mock_s3 if svc == "s3" else mock_cf
                    )

                    result = handler(event, MagicMock())

        assert result["statusCode"] == 500
        assert "Build failed" in result["body"]["error"]
        # S3 put_object should NOT have been called (no partial uploads)
        mock_s3.put_object.assert_not_called()

    def test_handler_skips_invalidation_when_no_distribution_id(self):
        """Handler should skip CloudFront invalidation if distribution ID not set."""
        event = {"run_id": "test-123", "source": "pipeline-orchestrator"}

        with patch.dict("os.environ", {
            "DATA_BUCKET_NAME": "data-bucket",
            "WEBSITE_BUCKET_NAME": "website-bucket",
            # No CLOUDFRONT_DISTRIBUTION_ID
        }):
            with patch("src.website_builder.handler.boto3") as mock_boto3:
                with patch("src.website_builder.handler.WebsiteBuilder") as mock_builder_cls:
                    mock_builder = MagicMock()
                    mock_builder.build_and_get_files.return_value = {
                        "index.html": "<html></html>",
                    }
                    mock_builder_cls.return_value = mock_builder

                    mock_s3 = MagicMock()
                    mock_cf = MagicMock()
                    mock_boto3.client.side_effect = lambda svc, **kwargs: (
                        mock_s3 if svc == "s3" else mock_cf
                    )

                    result = handler(event, MagicMock())

        assert result["statusCode"] == 200
        mock_cf.create_invalidation.assert_not_called()


class TestStagedUpload:
    """Tests for the staged upload mechanism."""

    def test_staged_upload_puts_files_to_staging_prefix(self):
        """Files should first be uploaded to _staging/ prefix."""
        mock_s3 = MagicMock()
        logger = StructuredLogger(lambda_name="test", run_id="test-123")
        files = {"index.html": "<html>test</html>", "assets/style.css": "body {}"}

        _staged_upload(mock_s3, logger, "website-bucket", files)

        # Verify staging uploads
        staging_calls = [
            c for c in mock_s3.put_object.call_args_list
            if "_staging/" in c.kwargs.get("Key", c[1].get("Key", ""))
        ]
        assert len(staging_calls) == 2

    def test_staged_upload_copies_to_final_location(self):
        """After staging, files should be copied to final paths."""
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "_staging/index.html"}]}
        ]
        logger = StructuredLogger(lambda_name="test", run_id="test-123")
        files = {"index.html": "<html>test</html>"}

        _staged_upload(mock_s3, logger, "website-bucket", files)

        # Verify copy was called
        mock_s3.copy_object.assert_called_once()
        copy_call = mock_s3.copy_object.call_args
        assert copy_call.kwargs["Key"] == "index.html"
        assert copy_call.kwargs["CopySource"] == {
            "Bucket": "website-bucket",
            "Key": "_staging/index.html",
        }

    def test_staged_upload_aborts_on_staging_failure(self):
        """If staging upload fails, exception propagates and site is preserved."""
        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = Exception("S3 error")
        mock_s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": []}
        ]
        logger = StructuredLogger(lambda_name="test", run_id="test-123")
        files = {"index.html": "<html>test</html>"}

        with pytest.raises(Exception, match="S3 error"):
            _staged_upload(mock_s3, logger, "website-bucket", files)

        # copy_object should NOT have been called
        mock_s3.copy_object.assert_not_called()

    def test_staged_upload_sets_correct_content_types(self):
        """Staged upload should set correct Content-Type for each file."""
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [
                {"Key": "_staging/index.html"},
                {"Key": "_staging/assets/style.css"},
                {"Key": "_staging/assets/app.js"},
            ]}
        ]
        logger = StructuredLogger(lambda_name="test", run_id="test-123")
        files = {
            "index.html": "<html></html>",
            "assets/style.css": "body {}",
            "assets/app.js": "console.log('hi')",
        }

        _staged_upload(mock_s3, logger, "website-bucket", files)

        # Check content types in put_object calls
        put_calls = mock_s3.put_object.call_args_list
        content_types = {
            c.kwargs["Key"].replace("_staging/", ""): c.kwargs["ContentType"]
            for c in put_calls
        }
        assert content_types["index.html"] == "text/html; charset=utf-8"
        assert content_types["assets/style.css"] == "text/css; charset=utf-8"
        assert content_types["assets/app.js"] == "application/javascript; charset=utf-8"

    def test_staged_upload_uses_server_side_encryption(self):
        """All uploads should use AES256 server-side encryption."""
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "_staging/index.html"}]}
        ]
        logger = StructuredLogger(lambda_name="test", run_id="test-123")
        files = {"index.html": "<html></html>"}

        _staged_upload(mock_s3, logger, "website-bucket", files)

        # Verify encryption on put_object
        put_call = mock_s3.put_object.call_args
        assert put_call.kwargs["ServerSideEncryption"] == "AES256"

        # Verify encryption on copy_object
        copy_call = mock_s3.copy_object.call_args
        assert copy_call.kwargs["ServerSideEncryption"] == "AES256"


class TestCloudFrontInvalidation:
    """Tests for CloudFront invalidation."""

    def test_invalidation_creates_wildcard_path(self):
        """Invalidation should use /* path."""
        mock_cf = MagicMock()
        logger = StructuredLogger(lambda_name="test", run_id="test-123")

        _invalidate_cloudfront(mock_cf, logger, "E123456")

        call_args = mock_cf.create_invalidation.call_args
        paths = call_args.kwargs["InvalidationBatch"]["Paths"]
        assert paths["Items"] == ["/*"]
        assert paths["Quantity"] == 1

    def test_invalidation_retries_on_failure(self):
        """Invalidation should retry up to 2 times on failure."""
        mock_cf = MagicMock()
        mock_cf.create_invalidation.side_effect = [
            Exception("Throttled"),
            Exception("Throttled"),
            None,  # Success on third attempt
        ]
        logger = StructuredLogger(lambda_name="test", run_id="test-123")

        with patch("time.sleep"):
            _invalidate_cloudfront(mock_cf, logger, "E123456")

        assert mock_cf.create_invalidation.call_count == 3

    def test_invalidation_raises_after_max_retries(self):
        """Invalidation should raise after exhausting retries."""
        mock_cf = MagicMock()
        mock_cf.create_invalidation.side_effect = Exception("Persistent error")
        logger = StructuredLogger(lambda_name="test", run_id="test-123")

        with patch("time.sleep"):
            with pytest.raises(Exception, match="Persistent error"):
                _invalidate_cloudfront(mock_cf, logger, "E123456")

        assert mock_cf.create_invalidation.call_count == 3


class TestCleanupStaging:
    """Tests for staging cleanup."""

    def test_cleanup_deletes_staged_objects(self):
        """Cleanup should delete all objects under staging prefix."""
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [
                {"Key": "_staging/index.html"},
                {"Key": "_staging/assets/style.css"},
            ]}
        ]
        logger = StructuredLogger(lambda_name="test", run_id="test-123")

        _cleanup_staging(mock_s3, "website-bucket", "_staging/", logger)

        mock_s3.delete_objects.assert_called_once()
        delete_call = mock_s3.delete_objects.call_args
        assert len(delete_call.kwargs["Delete"]["Objects"]) == 2

    def test_cleanup_handles_empty_staging(self):
        """Cleanup should handle case where no staged objects exist."""
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": []}
        ]
        logger = StructuredLogger(lambda_name="test", run_id="test-123")

        # Should not raise
        _cleanup_staging(mock_s3, "website-bucket", "_staging/", logger)

    def test_cleanup_does_not_raise_on_error(self):
        """Cleanup is best-effort and should not raise exceptions."""
        mock_s3 = MagicMock()
        mock_s3.get_paginator.side_effect = Exception("Access denied")
        logger = StructuredLogger(lambda_name="test", run_id="test-123")

        # Should not raise
        _cleanup_staging(mock_s3, "website-bucket", "_staging/", logger)


class TestGetContentType:
    """Tests for content type detection."""

    def test_html_content_type(self):
        assert _get_content_type("index.html") == "text/html; charset=utf-8"

    def test_css_content_type(self):
        assert _get_content_type("assets/style.css") == "text/css; charset=utf-8"

    def test_js_content_type(self):
        assert _get_content_type("assets/app.js") == "application/javascript; charset=utf-8"

    def test_json_content_type(self):
        assert _get_content_type("data.json") == "application/json; charset=utf-8"

    def test_unknown_extension_returns_octet_stream(self):
        assert _get_content_type("file.xyz") == "application/octet-stream"

    def test_no_extension_returns_octet_stream(self):
        assert _get_content_type("README") == "application/octet-stream"

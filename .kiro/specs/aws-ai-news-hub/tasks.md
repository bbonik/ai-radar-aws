# Implementation Plan: AWS AI News Hub

## Overview

Transform the existing aws-news-extractor repository into the "AI Radar AWS" platform — a two-Lambda architecture that fetches AWS AI news, generates LLM-powered reports with Mermaid diagrams, and publishes a static website via CloudFront. Implementation uses Python 3.11, Amazon Bedrock (Claude Sonnet + Opus), and CDK for infrastructure.

## Tasks

- [x] 1. Repurpose existing repository for AI Radar AWS project
  - [x] 1.1 Remove old email-specific code and restructure directory layout
    - Remove SES email sending logic and email formatting from `cdk_project/lambda/news_monitor.py`
    - Remove SES-related IAM permissions and environment variables from `cdk_project/stack.py`
    - Remove email configuration (SENDER_EMAIL, RECIPIENT_EMAIL) from `cdk_project/config.py`
    - Restructure into new layout: `src/` for Lambda source code, `src/pipeline/` for Lambda 1 modules, `src/website_builder/` for Lambda 2 modules, `infrastructure/` for CDK stack
    - Keep useful patterns: RSS fetching logic, relevance filtering regex patterns, S3 CSV read/write patterns, CDK structure
    - _Requirements: 14.1, 14.4_

  - [x] 1.2 Update project documentation and environment setup
    - Update `README.md` to describe "AI Radar AWS" project: purpose, architecture overview, setup instructions, deployment steps
    - Update `.gitignore` to exclude: `.venv/`, `__pycache__/`, `*.pyc`, `.env`, `cdk.out/`, `node_modules/`, `.hypothesis/`
    - Create `requirements.txt` for Lambda dependencies (boto3, feedparser or urllib only)
    - Create `requirements-dev.txt` for development dependencies (hypothesis, pytest, moto, black, mypy)
    - Set up Python virtual environment instructions in README
    - _Requirements: 14.4, 14.6_

- [x] 2. Implement Configuration Module
  - [x] 2.1 Create the centralized configuration file
    - Create `src/config.py` with the `Config` class containing all tunable parameters
    - Include: AWS region, schedule settings, LLM A/B model IDs, inference profile names, temperature, max tokens
    - Include: importance scoring weights (service_points_high/medium/base, blogpost_points, word_count_scale, thresholds)
    - Include: prompt templates for Report Generator and Graph Generator
    - Include: research timeout, RSS URL, fetch timeout, max retries, Lambda 2 function name
    - Ensure no sensitive values (API keys, credentials) are stored in the file
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 3.7_

  - [x] 2.2 Write unit tests for configuration loading
    - Test that all required fields have default values
    - Test that configuration values are accessible and correctly typed
    - _Requirements: 15.7_

- [x] 3. Implement Structured Logging
  - [x] 3.1 Create the StructuredLogger class
    - Create `src/shared/logger.py` with `StructuredLogger` class
    - Every log entry includes: run_id (UUID correlation ID), lambda_name, timestamp (ISO 8601), level
    - Implement `info()`, `warning()`, `error()` methods accepting message and arbitrary kwargs
    - Output JSON-formatted log entries to stdout for CloudWatch ingestion
    - _Requirements: 14.1_

  - [x] 3.2 Write unit tests for StructuredLogger
    - Test JSON output format contains all required fields
    - Test that kwargs are included in log entries
    - Test correlation ID consistency across log calls
    - _Requirements: 14.1_

- [x] 4. Implement Data Models
  - [x] 4.1 Create all data model classes
    - Create `src/shared/models.py` with dataclass definitions
    - Implement: `RSSItem`, `PageContent`, `ResearchContext`, `Report`, `ProcessedAnnouncement`, `AnnouncementError`, `PipelineRunSummary`
    - Include CSV serialization/deserialization methods on `ProcessedAnnouncement`
    - Handle pipe-separated blogpost_links serialization
    - _Requirements: 7.1, 7.2_

  - [x] 4.2 Write property test for CSV round-trip consistency
    - **Property 10: CSV serialization round-trip**
    - Generate arbitrary ProcessedAnnouncement objects with Hypothesis
    - Verify serialize → deserialize produces equivalent object
    - **Validates: Requirements 7.1**

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement RSS Fetcher
  - [x] 6.1 Create the RSS Fetcher module
    - Create `src/pipeline/rss_fetcher.py` with `RSSFetcher` class
    - Fetch from configured RSS URL with 30s timeout
    - Implement retry logic: up to 3 retries with exponential backoff (1s, 2s, 4s)
    - Parse RSS XML using `xml.etree.ElementTree` — extract title, description, pubDate, link from each `<item>`
    - Return list of `RSSItem` dataclass instances
    - Log errors with structured logger on failure
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 6.2 Write property test for RSS parsing
    - **Property 1: RSS parsing extracts all fields**
    - Generate valid RSS XML items with Hypothesis, verify all four fields populated
    - **Validates: Requirements 1.2**

  - [x] 6.3 Write unit tests for RSS Fetcher retry behavior
    - Test exponential backoff timing with mocked HTTP failures
    - Test successful fetch returns correct RSSItem list
    - Test empty list returned after all retries exhausted
    - _Requirements: 1.3, 1.4_

- [x] 7. Implement Relevance Filter
  - [x] 7.1 Create the Relevance Filter module
    - Create `src/pipeline/relevance_filter.py` with `RelevanceFilter` class
    - Port and enhance existing regex patterns from `news_monitor.py`
    - Match against title + first 200 characters of description only
    - Use word-boundary matching (`\b`) to prevent false positives
    - Apply exclusion patterns first (e.g., "Amazon Connect" agent references)
    - Item is relevant if ≥1 inclusion pattern matches AND 0 exclusion patterns match
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 7.2 Write property tests for Relevance Filter
    - **Property 2: Relevance filter correctly classifies items**
    - **Property 3: Word-boundary matching prevents false positives**
    - **Property 4: Exclusion patterns override inclusion**
    - Generate items with known keyword placement using Hypothesis
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**

- [x] 8. Implement Importance Classifier
  - [x] 8.1 Create the Importance Classifier module
    - Create `src/pipeline/importance_classifier.py` with `ImportanceClassifier` class
    - Compute score = service_tier_points + blogpost_points (if links present) + (word_count × word_count_scale)
    - Service tier lookup: map service names to point values from config (high/medium/base)
    - Blogpost detection: check for external links in description
    - Star mapping: score < threshold_2_star → 1★, threshold_2_star ≤ score < threshold_3_star → 2★, score ≥ threshold_3_star → 3★
    - Return (star_level, raw_score) tuple
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

  - [x] 8.2 Write property tests for Importance Classifier
    - **Property 5: Importance score is additive sum of factors**
    - **Property 6: Star level determined by threshold comparison**
    - Generate announcements with known service tiers, link presence, and word counts
    - Verify score arithmetic and threshold-based star assignment
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

- [x] 9. Implement Research Agent
  - [x] 9.1 Create the Research Agent module
    - Create `src/pipeline/research_agent.py` with `ResearchAgent` class
    - Extract URLs from announcement description and link field
    - Fetch each URL with timeout, extract main text content (strip nav/headers/footers/ads)
    - Track remaining Lambda execution time via `context.get_remaining_time_in_millis()`
    - Skip research if remaining time < (research_timeout_per_announcement × 1000 + safety_margin)
    - Return `ResearchContext` with gathered content or skipped flag
    - Log which announcements had research skipped
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.7, 4.8_

  - [x] 9.2 Write property test for Research Agent time tracking
    - **Property 7: Research agent respects remaining execution time**
    - Mock Lambda context with various remaining times
    - Verify skip decision based on configured timeout + safety margin
    - **Validates: Requirements 4.7, 4.8**

- [x] 10. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement Report Generator
  - [x] 11.1 Create the Report Generator module
    - Create `src/pipeline/report_generator.py` with `ReportGenerator` class
    - Construct prompt from config template + announcement data + research context
    - Call Bedrock `invoke_model` using application inference profile ARN for LLM A (Claude Sonnet)
    - Use global cross-region inference profile as model source
    - Retry up to 2× on failure with 1s delay
    - Parse LLM response into structured `Report` object (6 sections)
    - On persistent failure, raise exception for pipeline error handling
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 11.2 Write unit tests for Report Generator
    - **Property 8: Report parsing produces all sections** (example-based with mocked Bedrock response)
    - Test retry behavior with mocked API failures
    - Test prompt construction includes announcement data and research context
    - **Validates: Requirements 5.1, 5.5**

- [x] 12. Implement Graph Generator
  - [x] 12.1 Create the Graph Generator module
    - Create `src/pipeline/graph_generator.py` with `GraphGenerator` class
    - Skip if importance_level == 1 (return None without invoking LLM)
    - Construct prompt from config template + announcement + report context
    - Call Bedrock `invoke_model` using application inference profile ARN for LLM B (Claude Opus)
    - Use global cross-region inference profile as model source
    - Retry up to 2× on failure with 1s delay; return None on persistent failure
    - Return Mermaid diagram string
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [x] 12.2 Write property test for Graph Generator conditional logic
    - **Property 9: Graph generation conditional on importance level**
    - Verify LLM is invoked only for importance_level ≥ 2
    - Verify None returned for importance_level == 1 without LLM call
    - **Validates: Requirements 6.1, 6.5**

- [x] 13. Implement Storage Manager
  - [x] 13.1 Create the Storage Manager module
    - Create `src/pipeline/storage_manager.py` with `StorageManager` class
    - CSV stored at `s3://{data_bucket}/database/announcements.csv`
    - Error records at `s3://{data_bucket}/errors/failed_announcements.csv`
    - Implement `load_existing_links()` — returns set of known announcement links for deduplication
    - Implement `save_announcement()` — appends new row to CSV (never overwrites)
    - Implement `save_error_record()` — appends to error CSV
    - Use announcement link as unique key for deduplication
    - S3 uploads use `ServerSideEncryption='AES256'`
    - Retry S3 writes up to 3× with exponential backoff
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4_

  - [x] 13.2 Write property test for deduplication logic
    - **Property 11: Deduplication by announcement link**
    - Generate sets of existing links and new items
    - Verify items with existing links are skipped, new links proceed
    - **Validates: Requirements 7.2, 7.3, 8.2, 8.3**

- [x] 14. Implement Pipeline Orchestrator (Lambda 1 handler)
  - [x] 14.1 Create the Pipeline Orchestrator and Lambda 1 entry point
    - Create `src/pipeline/orchestrator.py` with `PipelineOrchestrator` class
    - Coordinate all pipeline stages sequentially: fetch → deduplicate → filter → classify → research → report → graph → store
    - Track per-announcement success/failure
    - Record failed announcements to error file with stage, error type, error message
    - Continue processing other announcements on individual failure
    - Generate and log `PipelineRunSummary` at completion
    - Invoke Lambda 2 asynchronously at the end (fire-and-forget)
    - Use correlation ID (UUID) for all log entries in this run
    - Create `src/pipeline/handler.py` as Lambda 1 entry point calling orchestrator
    - _Requirements: 4.6, 4.7, 14.1_

  - [x] 14.2 Write unit tests for Pipeline Orchestrator
    - Test that individual announcement failure does not halt pipeline
    - Test pipeline run summary generation with correct counts
    - Test Lambda 2 async invocation is called after processing
    - Test error records are saved for failed announcements
    - _Requirements: 4.6, 14.1_

- [x] 15. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 16. Implement Website Builder (Lambda 2)
  - [x] 16.1 Create the Website Builder module
    - Create `src/website_builder/builder.py` with `WebsiteBuilder` class
    - Read all announcements from CSV in S3 data bucket
    - Generate static HTML/CSS/JS using Python string templates
    - Produce: `index.html` (listing + composable filters + timeline), individual report pages, shared CSS/JS assets
    - Include Mermaid.js rendering for diagrams
    - Include client-side filtering (time period, service, importance ranking)
    - Include timeline visualization using a lightweight JS charting library (Chart.js)
    - Include client-side PDF export functionality
    - Apply "AI Radar AWS" branding with AWS-inspired color scheme
    - Responsive design for desktop, tablet, and mobile
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 11.1, 11.2, 11.3, 11.4, 12.1, 12.2, 12.3, 12.4, 16.1, 16.2, 16.3, 16.4, 16.5, 17.1, 17.2_

  - [x] 16.2 Create the Website Builder Lambda 2 handler and S3/CloudFront integration
    - Create `src/website_builder/handler.py` as Lambda 2 entry point
    - Upload generated files to S3 website bucket
    - Create CloudFront invalidation for `/*`
    - On failure, preserve existing site files (no partial uploads — stage in temp, then copy)
    - Accept run_id from Lambda 1 invocation payload for correlated logging
    - _Requirements: 17.3, 17.4, 17.5_

  - [x] 16.3 Write property tests for Website Builder output
    - **Property 12: Report HTML contains all required content**
    - **Property 13: Composable filter produces correct results**
    - **Property 14: Filter state independence**
    - **Property 15: Timeline data aggregation**
    - **Property 17: XSS sanitization**
    - Generate announcements with Hypothesis, verify HTML output contains all required elements
    - Verify filter logic produces correct subsets
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 10.4, 10.5, 10.7, 11.1, 11.2, 13.4**

  - [x] 16.4 Write property test for PDF export content
    - **Property 16: PDF contains complete report content**
    - Verify generated PDF includes all six report sections and header metadata
    - **Validates: Requirements 12.1, 12.3**

- [x] 17. Implement CDK Infrastructure Stack
  - [x] 17.1 Create the CDK stack with two-Lambda architecture
    - Create `infrastructure/stack.py` with the full CDK stack
    - Lambda 1 (Report Pipeline): Python 3.11, 15-min timeout, 512MB memory, `src/pipeline/` code
    - Lambda 2 (Website Builder): Python 3.11, 10-min timeout, 512MB memory, `src/website_builder/` code
    - EventBridge rule triggering Lambda 1 at configured schedule
    - S3 Data Bucket (encrypted, block public access, auto-delete on stack removal)
    - S3 Website Bucket (encrypted, block public access, auto-delete on stack removal)
    - Grant Lambda 1: read/write data bucket, invoke Lambda 2, invoke Bedrock
    - Grant Lambda 2: read data bucket, write website bucket, create CloudFront invalidation
    - Create Bedrock application inference profiles for LLM A and LLM B with cost-tracking tags
    - Environment variables for both Lambdas (bucket names, function names, inference profile ARNs)
    - Create `infrastructure/app.py` as CDK app entry point
    - Update `cdk.json` to point to new app entry
    - _Requirements: 14.1, 14.2, 14.3, 14.5, 5.2, 5.3, 6.2, 6.3_

  - [x] 17.2 Add CloudFront, WAF, and security configuration
    - CloudFront distribution with S3 website bucket as origin
    - Origin Access Control (OAC) — S3 bucket accessible only via CloudFront
    - AWS WAF Web ACL attached to CloudFront (rate limiting, common rule set)
    - Response Headers Policy: Content-Security-Policy, X-Content-Type-Options, X-Frame-Options, Referrer-Policy
    - TLS 1.2 minimum on CloudFront
    - S3 bucket policy allowing only CloudFront OAC access
    - _Requirements: 13.1, 13.2, 13.3, 13.5, 13.6_

  - [x] 17.3 Add CloudWatch Alarms
    - Lambda1-Errors: Errors ≥ 1 in 1 evaluation period
    - Lambda1-Timeout: Duration ≥ 840000 ms (14 min)
    - Lambda1-Duration: Duration ≥ 720000 ms (12 min)
    - Lambda2-Errors: Errors ≥ 1 in 1 evaluation period
    - Lambda2-Timeout: Duration ≥ 540000 ms (9 min)
    - _Requirements: 14.1_

  - [x] 17.4 Write CDK synthesis and integration tests
    - Test stack synthesizes without errors
    - Test expected resources are created (both Lambdas, both S3 buckets, CloudFront, WAF, EventBridge, alarms)
    - Test Lambda timeouts are correct (15 min, 10 min)
    - Test S3 encryption enabled
    - Test CloudFront enforces HTTPS with TLS 1.2+
    - Test security headers present
    - Test WAF attached to CloudFront
    - Test Lambda 1 has permission to invoke Lambda 2
    - _Requirements: 14.1, 14.2, 13.1, 13.2, 13.3, 13.5, 13.6_

- [x] 18. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 19. Integration wiring and end-to-end validation
  - [x] 19.1 Wire all components together and verify end-to-end flow
    - Ensure Lambda 1 handler imports and initializes all pipeline components correctly
    - Ensure Lambda 2 handler imports and initializes website builder correctly
    - Verify environment variable propagation from CDK to Lambda handlers
    - Verify correlation ID flows from Lambda 1 to Lambda 2 via invocation payload
    - Create `scripts/test_local.py` for local pipeline testing with mocked AWS services
    - _Requirements: 14.1, 14.2_

  - [x] 19.2 Write integration tests for full pipeline flow
    - Test end-to-end Lambda 1 pipeline with mocked external services (RSS, Bedrock, S3)
    - Test Lambda 2 website build with mocked S3 and CloudFront
    - Test Lambda 1 → Lambda 2 invocation chain (mocked Lambda client)
    - Test graceful degradation scenarios (RSS failure, Bedrock throttling, S3 write failure)
    - _Requirements: 14.1_

- [x] 20. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The first task (repo restructuring) preserves useful patterns from the existing codebase while removing email-specific code
- Python 3.11 is the target runtime for both Lambdas
- All Bedrock calls use application inference profiles with global cross-region inference profiles as the model source
- No secrets are stored in the repository — all credentials come from IAM roles at runtime

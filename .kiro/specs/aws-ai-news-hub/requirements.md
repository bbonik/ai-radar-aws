# Requirements Document

## Introduction

AWS AI News Hub (working name: "AI Radar AWS" — radar = always scanning, detecting, monitoring the AWS AI landscape) is a public-facing website that automatically curates, classifies, and presents all AWS AI/ML/GenAI announcements. It transforms the existing aws-news-extractor pipeline (EventBridge → Lambda → S3/SES) into a full content platform with LLM-generated reports, visual graphs, and multiple browsing views. The system runs a daily automated pipeline that fetches the AWS "What's New" RSS feed, filters for AI relevance, classifies importance, researches linked content, generates rich reports using Amazon Bedrock, and publishes results to a static website hosted on S3/CloudFront.

## Glossary

- **Pipeline**: The daily automated process that fetches, filters, classifies, researches, and generates reports for AWS announcements
- **RSS_Fetcher**: The component that retrieves items from the AWS "What's New" RSS feed
- **Relevance_Filter**: The component that determines whether an announcement is related to AI/ML/GenAI using keyword-based regex patterns
- **Importance_Classifier**: The component that assigns a 1-star, 2-star, or 3-star importance rating to each relevant announcement using a point-based scoring system
- **Importance_Score**: The numeric score computed by the Importance_Classifier by summing weighted contributions from service type, blogpost link presence, and word count; the final star rating is determined by score thresholds
- **Research_Agent**: The component that follows links in announcements (blogposts, documentation) to gather additional context
- **Report_Generator**: The component that uses LLM A (Claude Sonnet via Bedrock) to produce structured reports for each announcement
- **Graph_Generator**: The component that uses LLM B (Claude Opus via Bedrock) to produce Mermaid diagrams for 2-star and 3-star announcements
- **Storage_Manager**: The component that persists announcement data and generated reports to S3 as CSV files
- **Website_Builder**: The component that generates the static website from stored announcement data and reports
- **Website**: The public-facing static site served via CloudFront that displays curated AWS AI news
- **Announcement**: A single item from the AWS "What's New" RSS feed that has passed relevance filtering
- **Report**: The structured LLM-generated content for a single announcement (summary, how it works, importance, comparison, availability, and optional Mermaid graph)
- **Importance_Level**: A classification of 1-star (low), 2-star (medium), or 3-star (high) assigned to each announcement
- **Inference_Profile**: A Bedrock application inference profile used for cost tracking with tags
- **Configuration_File**: A centralized file containing all configurable parameters (region, LLM models, prompts, inference parameters)

## Requirements

### Requirement 1: RSS Feed Fetching

**User Story:** As a site operator, I want the system to automatically fetch the AWS "What's New" RSS feed daily, so that new announcements are captured without manual intervention.

#### Acceptance Criteria

1. WHEN the daily scheduled trigger fires, THE RSS_Fetcher SHALL retrieve all items from the AWS "What's New" RSS feed at `https://aws.amazon.com/about-aws/whats-new/recent/feed/`
2. WHEN the RSS feed is successfully retrieved, THE RSS_Fetcher SHALL extract the title, description, publication date, and link for each item
3. IF the RSS feed is unreachable or returns an error, THEN THE RSS_Fetcher SHALL log the error and retry up to 3 times with exponential backoff
4. THE RSS_Fetcher SHALL complete feed retrieval within 30 seconds per attempt

### Requirement 2: AI/ML Relevance Filtering

**User Story:** As a site operator, I want the system to filter announcements for AI/ML/GenAI relevance, so that only pertinent content appears on the website.

#### Acceptance Criteria

1. WHEN the RSS_Fetcher provides a list of feed items, THE Relevance_Filter SHALL evaluate each item against the defined AI/ML/GenAI keyword patterns
2. THE Relevance_Filter SHALL match keywords against the item title and the first 200 characters of the item description
3. THE Relevance_Filter SHALL use regex-based word-boundary matching to avoid false positives (e.g., matching "AI" but not "SAID")
4. THE Relevance_Filter SHALL exclude announcements matching exclusion patterns (e.g., "Amazon Connect" agent references)
5. WHEN an item matches at least one AI/ML/GenAI pattern and no exclusion pattern, THE Relevance_Filter SHALL mark the item as relevant

### Requirement 3: Importance Classification

**User Story:** As a site visitor, I want announcements classified by importance using a point-based scoring system, so that I can quickly identify the most significant news based on multiple contributing factors.

#### Acceptance Criteria

1. WHEN an announcement passes relevance filtering, THE Importance_Classifier SHALL compute an Importance_Score by summing weighted point contributions from all scoring factors
2. THE Importance_Classifier SHALL assign service type points as follows: high-interest services (Amazon Bedrock, Amazon Bedrock AgentCore, Amazon SageMaker AI, Amazon QuickSight) contribute the highest point value; medium-interest services (SageMaker, SageMaker Unified Studio, Kiro) contribute a moderate point value; all other relevant services contribute a base point value
3. WHEN an announcement contains one or more links to external blogposts, THE Importance_Classifier SHALL add blogpost presence points to the Importance_Score
4. THE Importance_Classifier SHALL add a word count contribution to the Importance_Score that scales linearly with the announcement word count, using a configurable scaling factor so that word count contributes proportionally without overshadowing other factors
5. THE Importance_Classifier SHALL determine the final Importance_Level by comparing the total Importance_Score against two configurable thresholds: scores below the first threshold yield 1-star, scores at or above the first threshold but below the second yield 2-star, and scores at or above the second threshold yield 3-star
6. THE Importance_Classifier SHALL assign exactly one Importance_Level (1-star, 2-star, or 3-star) to each announcement based on the computed Importance_Score
7. THE Configuration_File SHALL define the scoring weights (service type points for each tier, blogpost presence points, word count scaling factor) and the two score thresholds for star classification
8. THE Importance_Classifier SHALL allow all factors to work synergistically such that a high-interest service announcement with low word count and no blogpost links can score lower than a base-service announcement with high word count and multiple blogpost links

### Requirement 4: Research Phase

**User Story:** As a site visitor, I want reports enriched with context from linked resources, so that I get a comprehensive understanding of each announcement.

#### Acceptance Criteria

1. WHEN an announcement contains links to blogposts or documentation pages, THE Research_Agent SHALL retrieve the content from those links
2. THE Research_Agent SHALL extract the main textual content from retrieved pages, excluding navigation, headers, footers, and advertisements
3. IF a linked page is unreachable or returns an error, THEN THE Research_Agent SHALL log the error and proceed with available content
4. THE Research_Agent SHALL be allowed up to 5 minutes per announcement to complete all link retrieval and content extraction, accounting for announcements that reference multiple blogposts and documentation pages
5. THE Research_Agent SHALL pass the gathered context to the Report_Generator along with the original announcement data
6. THE Pipeline SHALL set the Lambda execution timeout to a maximum of 15 minutes to accommodate research-heavy runs with multiple announcements
7. THE Research_Agent SHALL process announcements sequentially and track remaining Lambda execution time to avoid exceeding the Lambda timeout
8. IF the remaining Lambda execution time is insufficient to research the next announcement, THEN THE Research_Agent SHALL skip research for remaining announcements and log which announcements were not researched

### Requirement 5: Report Generation via LLM

**User Story:** As a site visitor, I want each announcement to have a structured, insightful report, so that I can understand the significance and practical implications of each announcement.

#### Acceptance Criteria

1. WHEN the Research_Agent provides announcement data and gathered context, THE Report_Generator SHALL produce a structured report containing: a concise "What's New" summary paragraph, a "How It Works" explanation, a "Why It's Important" section, a "How It's Different" comparison to previous capabilities, a "When to Prefer It" guidance section, and an "Availability" section (GA/Preview status and regions)
2. THE Report_Generator SHALL invoke LLM A (Claude Sonnet model) via Amazon Bedrock using a global cross-region inference profile
3. THE Report_Generator SHALL use a Bedrock application inference profile with cost-tracking tags
4. THE Report_Generator SHALL read the LLM model identifier, prompt templates, and inference parameters from the Configuration_File
5. IF the Bedrock API call fails, THEN THE Report_Generator SHALL retry up to 2 times and log the error with the announcement identifier

### Requirement 6: Mermaid Graph Generation

**User Story:** As a site visitor, I want visual diagrams for important announcements, so that I can see how services fit into the AWS AI/ML ecosystem at a glance.

#### Acceptance Criteria

1. WHEN an announcement has an Importance_Level of 2-star or 3-star, THE Graph_Generator SHALL produce a Mermaid diagram showing how the announced service or feature fits into the AWS AI/ML/GenAI ecosystem
2. THE Graph_Generator SHALL invoke LLM B (Claude Opus model) via Amazon Bedrock using a global cross-region inference profile
3. THE Graph_Generator SHALL use a Bedrock application inference profile with cost-tracking tags
4. THE Graph_Generator SHALL read the LLM model identifier, prompt templates, and inference parameters from the Configuration_File
5. WHEN an announcement has an Importance_Level of 1-star, THE Graph_Generator SHALL skip Mermaid diagram generation for that announcement
6. IF the Bedrock API call fails, THEN THE Graph_Generator SHALL retry up to 2 times, log the error, and proceed without a diagram for that announcement

### Requirement 7: Data Storage

**User Story:** As a site operator, I want all announcement data and reports persisted reliably, so that the website can be regenerated and historical data is preserved.

#### Acceptance Criteria

1. WHEN the Report_Generator and Graph_Generator complete processing for an announcement, THE Storage_Manager SHALL persist the announcement metadata, generated report sections, Mermaid graph (if applicable), importance level, announcement date, and original links to a CSV file in S3
2. THE Storage_Manager SHALL use the announcement link as a unique identifier for deduplication
3. WHEN an announcement link already exists in storage, THE Storage_Manager SHALL skip that announcement without overwriting existing data
4. THE Storage_Manager SHALL encrypt stored files using S3 server-side encryption (AES-256)
5. IF the S3 write operation fails, THEN THE Storage_Manager SHALL retry up to 3 times and log the error

### Requirement 8: Deduplication

**User Story:** As a site operator, I want the system to avoid processing announcements that have already been captured, so that resources are not wasted and duplicates do not appear.

#### Acceptance Criteria

1. WHEN the Pipeline begins processing, THE Storage_Manager SHALL load all previously stored announcement links from S3
2. THE Pipeline SHALL compare each new RSS feed item link against the set of previously stored links
3. WHEN an RSS feed item link matches a previously stored link, THE Pipeline SHALL skip all downstream processing (classification, research, report generation) for that item
4. THE Storage_Manager SHALL complete the deduplication check within 10 seconds for up to 10,000 stored announcements

### Requirement 9: Public Website - Report View

**User Story:** As a site visitor, I want to read the full generated report for any announcement, so that I can understand the details and implications of each piece of news.

#### Acceptance Criteria

1. THE Website SHALL display the full report for each announcement including: "What's New" summary, "How It Works", "Why It's Important", "How It's Different", "When to Prefer It", and "Availability" sections
2. WHEN an announcement has a Mermaid diagram, THE Website SHALL render the diagram visually within the report view
3. THE Website SHALL display the announcement title, publication date, importance level (as star icons), and original AWS service name on each report page
4. THE Website SHALL provide a link from each report to the original AWS announcement and any referenced blogposts or documentation

### Requirement 10: Public Website - Composable Filtering and Ranking

**User Story:** As a site visitor, I want to combine time filters, service filters, and importance ranking simultaneously, so that I can efficiently find the most relevant news across multiple dimensions.

#### Acceptance Criteria

1. THE Website SHALL provide time-period filters that allow visitors to filter announcements by day, week, or month
2. THE Website SHALL provide a service filter that allows visitors to filter announcements by one or more AWS service names
3. THE Website SHALL provide an importance ranking option that orders announcements by Importance_Level (3-star first, then 2-star, then 1-star)
4. THE Website SHALL allow visitors to apply time-period filters, service filters, and importance ranking simultaneously as composable operations (e.g., "last week + filtered by Bedrock + ranked by importance")
5. WHEN multiple filters are applied, THE Website SHALL display only announcements that satisfy all active filter criteria, ordered by the selected ranking
6. THE Website SHALL display all announcements with their titles, dates, and importance levels within the filtered and ranked result set
7. THE Website SHALL allow visitors to add or remove individual filters without resetting other active filters

### Requirement 11: Public Website - Timeline Visualization

**User Story:** As a site visitor, I want a timeline graph showing announcement frequency and importance over time, so that I can identify trends and clusters of activity.

#### Acceptance Criteria

1. THE Website SHALL display a timeline graph showing the number of announcements per day
2. THE Website SHALL color-code or segment the timeline bars by Importance_Level
3. THE Website SHALL allow visitors to identify clusters of high-importance announcements visually
4. THE Website SHALL render the timeline graph using a client-side JavaScript charting library that requires no server-side processing

### Requirement 12: Public Website - PDF Export

**User Story:** As a site visitor, I want to download individual announcement reports as PDF files, so that I can save and share them offline.

#### Acceptance Criteria

1. WHEN a visitor requests a PDF export from a report view, THE Website SHALL generate a PDF containing the full report content including all text sections
2. WHEN the report includes a Mermaid diagram, THE Website SHALL include a rendered image of the diagram in the PDF
3. THE Website SHALL include the announcement title, date, importance level, and source links in the PDF header
4. THE Website SHALL generate the PDF client-side without requiring server-side processing

### Requirement 13: Website Security

**User Story:** As a site operator, I want the public website protected against common web attacks, so that the site remains available and trustworthy.

#### Acceptance Criteria

1. THE Website SHALL serve all content over HTTPS with TLS 1.2 or higher
2. THE Website SHALL set Content-Security-Policy headers that restrict script sources to same-origin and trusted CDN domains
3. THE Website SHALL set X-Content-Type-Options, X-Frame-Options, and Referrer-Policy security headers
4. THE Website SHALL sanitize all rendered content to prevent cross-site scripting (XSS) attacks
5. THE Website SHALL be served via Amazon CloudFront with AWS WAF enabled to mitigate DDoS and common attack patterns
6. THE Website SHALL block public access to the S3 origin bucket, allowing access only through the CloudFront distribution via Origin Access Control

### Requirement 14: Infrastructure and Deployment

**User Story:** As a site operator, I want the entire system deployed using CDK with minimal operational overhead, so that I can maintain and update the system easily.

#### Acceptance Criteria

1. THE Pipeline SHALL use AWS managed and serverless services: EventBridge for scheduling, Lambda for compute, S3 for storage, and CloudFront for content delivery
2. THE Pipeline SHALL be deployable using a single CDK deployment command after configuration
3. THE Configuration_File SHALL contain all configurable parameters: AWS region, LLM model identifiers for LLM A and LLM B, prompt templates, inference parameters, and schedule settings
4. THE Pipeline SHALL use a separate Python virtual environment from the existing project
5. THE Pipeline SHALL use Python 3.11 as the runtime language
6. THE Pipeline SHALL store no sensitive values (API keys, credentials) in the repository

### Requirement 15: Configuration Management

**User Story:** As a site operator, I want all tunable parameters in a single configuration file, so that I can adjust behavior without modifying code.

#### Acceptance Criteria

1. THE Configuration_File SHALL define the AWS region for deployment
2. THE Configuration_File SHALL define the model identifier for LLM A (Report_Generator) with a default of the latest Claude Sonnet model
3. THE Configuration_File SHALL define the model identifier for LLM B (Graph_Generator) with a default of the latest Claude Opus model
4. THE Configuration_File SHALL define prompt templates used by the Report_Generator and Graph_Generator
5. THE Configuration_File SHALL define inference parameters (temperature, max tokens) for each LLM
6. THE Configuration_File SHALL define the daily schedule (hour and minute in UTC) for Pipeline execution
7. WHEN a configuration value is changed, THE Pipeline SHALL use the updated value on the next execution without requiring redeployment of Lambda code

### Requirement 16: Website Branding and Aesthetics

**User Story:** As a site visitor, I want the website branded as "AI Radar AWS" with a professional, visually appealing design, so that it feels trustworthy and conveys the always-scanning, always-monitoring nature of the platform.

#### Acceptance Criteria

1. THE Website SHALL display the site name "AI Radar AWS" prominently in the header and browser tab title
2. THE Website SHALL use a consistent color scheme inspired by AWS branding (orange accents on dark/light backgrounds)
3. THE Website SHALL use responsive design that renders correctly on desktop, tablet, and mobile viewports
4. THE Website SHALL render Mermaid diagrams with styling consistent with the overall site theme
5. THE Website SHALL load the initial page view within 3 seconds on a standard broadband connection

### Requirement 17: Static Site Generation

**User Story:** As a site operator, I want the website generated as static HTML/CSS/JS files, so that it can be hosted on S3 without a running server.

#### Acceptance Criteria

1. WHEN the Pipeline completes processing new announcements, THE Website_Builder SHALL regenerate the static website files from the current stored data
2. THE Website_Builder SHALL produce HTML, CSS, and JavaScript files that require no server-side execution
3. THE Website_Builder SHALL upload the generated files to the S3 website hosting bucket
4. THE Website_Builder SHALL invalidate the CloudFront cache after uploading new files so visitors see updated content
5. IF the static site generation fails, THEN THE Website_Builder SHALL log the error and preserve the previously published site files

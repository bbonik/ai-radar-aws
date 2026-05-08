# AI Radar AWS

Automated AWS AI/ML/GenAI news curation platform. Fetches the AWS "What's New" RSS feed daily, filters for AI relevance, classifies importance, generates LLM-powered reports with Mermaid diagrams via Amazon Bedrock, and publishes a static website via CloudFront.

## Architecture

The system uses a **two-Lambda architecture** for resilience and separation of concerns:

```
┌──────────────┐     ┌─────────────────────────────────────────────────────┐
│  EventBridge │────▶│  Lambda 1: Report Generation Pipeline (15 min)      │
│  (Daily)     │     │  RSS → Dedup → Filter → Classify → Research →      │
└──────────────┘     │  Report (Bedrock Sonnet) → Graph (Bedrock Opus) →   │
                     │  Store CSV to S3                                     │
                     └──────────────────────────┬──────────────────────────┘
                                                │ async invoke
                     ┌──────────────────────────▼──────────────────────────┐
                     │  Lambda 2: Website Builder Pipeline (10 min)         │
                     │  Read CSV → Generate HTML/CSS/JS → Upload S3 →      │
                     │  Invalidate CloudFront                               │
                     └─────────────────────────────────────────────────────┘
```

**Key services:** Python 3.11, Amazon Bedrock (Claude Sonnet + Opus), CDK, S3, CloudFront, WAF, EventBridge

## Project Structure

```
aws-news-extractor/
├── src/
│   ├── config.py                # Centralized configuration
│   ├── pipeline/                # Lambda 1 modules
│   │   ├── handler.py           # Lambda 1 entry point
│   │   ├── orchestrator.py      # Pipeline coordination
│   │   ├── rss_fetcher.py       # RSS feed retrieval
│   │   ├── relevance_filter.py  # AI/ML keyword filtering
│   │   ├── importance_classifier.py  # Point-based scoring
│   │   ├── research_agent.py    # Link content extraction
│   │   ├── report_generator.py  # Bedrock Sonnet reports
│   │   ├── graph_generator.py   # Bedrock Opus Mermaid diagrams
│   │   └── storage_manager.py   # S3 CSV persistence
│   ├── website_builder/         # Lambda 2 modules
│   │   ├── handler.py           # Lambda 2 entry point
│   │   └── builder.py           # Static site generation
│   └── shared/                  # Shared modules
│       ├── logger.py            # Structured JSON logging
│       └── models.py            # Data models (dataclasses)
├── infrastructure/              # CDK stack
│   ├── app.py                   # CDK app entry point
│   └── stack.py                 # Full infrastructure definition
├── tests/                       # Tests (pytest + hypothesis)
├── cdk_project/                 # Legacy CDK project (reference)
├── requirements.txt             # Lambda runtime dependencies
├── requirements-dev.txt         # Development dependencies
└── cdk.json                     # CDK configuration
```

## Setup

### Prerequisites

- Python 3.11+
- AWS CLI configured with appropriate credentials
- AWS CDK v2 (`npm install -g aws-cdk`)
- An AWS account with Bedrock model access enabled (Claude Sonnet + Opus)

### Create Virtual Environment

```bash
cd aws-news-extractor

# Create virtual environment
python3.11 -m venv .venv

# Activate virtual environment
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install development dependencies
pip install -r requirements-dev.txt

# Install Lambda runtime dependencies
pip install -r requirements.txt
```

### Install CDK Dependencies

```bash
npm install -g aws-cdk
pip install aws-cdk-lib constructs
```

## Development

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with property-based tests (hypothesis)
pytest tests/ -v --hypothesis-show-statistics

# Run specific test file
pytest tests/test_config.py -v
```

### Code Quality

```bash
# Format code
black src/ tests/

# Type checking
mypy src/
```

## Deployment

### One-command deploy

```bash
./deploy.sh
```

That's it. The script handles everything: installs dependencies, runs tests, bootstraps CDK, and deploys.

**Options:**
```bash
./deploy.sh --profile my-aws-profile   # Use a specific AWS profile
./deploy.sh --destroy                  # Tear down the stack
```

### Manual deploy (if you prefer)

```bash
# Bootstrap CDK (first time only)
cdk bootstrap

# Synthesize CloudFormation template
cdk synth

# Deploy the stack
cdk deploy
```

The single `cdk deploy` command provisions:
- Two Lambda functions (Report Pipeline + Website Builder)
- EventBridge schedule rule
- S3 data bucket (encrypted, private)
- S3 website bucket (encrypted, private)
- CloudFront distribution with OAC
- WAF Web ACL with rate limiting
- CloudWatch alarms
- Bedrock application inference profiles
- All IAM roles and permissions

## Configuration

All tunable parameters live in `src/config.py`:
- AWS region and schedule settings
- LLM model IDs and inference parameters
- Importance scoring weights and thresholds
- Prompt templates
- Timeouts and retry settings

No secrets are stored in the repository. All credentials come from IAM roles at runtime.

## How It Works

1. **EventBridge** triggers Lambda 1 daily at the configured UTC time
2. **RSS Fetcher** retrieves the AWS "What's New" feed
3. **Deduplication** skips previously processed announcements
4. **Relevance Filter** applies regex patterns for AI/ML/GenAI keywords
5. **Importance Classifier** computes a point score → 1/2/3 stars
6. **Research Agent** follows blogpost/doc links for additional context
7. **Report Generator** calls Bedrock (Claude Sonnet) for structured reports
8. **Graph Generator** calls Bedrock (Claude Opus) for Mermaid diagrams (2-3 star only)
9. **Storage Manager** appends results to CSV in S3
10. **Lambda 2** is invoked asynchronously to rebuild the static website
11. **CloudFront** serves the updated site with WAF protection

## License

MIT License

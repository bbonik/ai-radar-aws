# AI Radar AWS

Automated AWS AI/ML/GenAI news curation platform. Fetches the AWS "What's New" RSS feed daily, filters for AI relevance, classifies importance, assigns taxonomy tags, generates LLM-powered reports with Mermaid diagrams via Amazon Bedrock, and publishes a static website via CloudFront.

## Architecture

```
┌──────────────┐     ┌─────────────────────────────────────────────────────┐
│  EventBridge │────▶│  Lambda 1: Report Generation Pipeline (15 min)      │
│  (Daily)     │     │  RSS → Dedup → Filter → Classify → Tag (Haiku) →   │
└──────────────┘     │  Research → Report (Sonnet) → Graph (Opus) →        │
                     │  Store CSV to S3                                     │
                     └──────────────────────────┬──────────────────────────┘
                                                │ async invoke
                     ┌──────────────────────────▼──────────────────────────┐
                     │  Lambda 2: Website Builder (10 min)                  │
                     │  Read CSV → Generate HTML/CSS/JS → Upload S3 →      │
                     │  Invalidate CloudFront                               │
                     └─────────────────────────────────────────────────────┘

                     ┌─────────────────────────────────────────────────────┐
                     │  Lambda 3: Analytics Collector                       │
                     │  API Gateway POST /events → S3 JSONL                │
                     └─────────────────────────────────────────────────────┘
```

**Key services:** Python 3.11, Amazon Bedrock (Claude Sonnet 4.6 + Opus 4.6 + Haiku 4.5), CDK, S3, CloudFront, WAF, EventBridge, API Gateway

## Project Structure

```
├── src/
│   ├── config.py                    # Centralized configuration (models, prompts, thresholds)
│   ├── pipeline/                    # Lambda 1: Report Generation Pipeline
│   │   ├── handler.py              # Entry point
│   │   ├── orchestrator.py         # Pipeline coordination
│   │   ├── rss_fetcher.py          # RSS feed retrieval
│   │   ├── relevance_filter.py     # AI/ML keyword filtering
│   │   ├── importance_classifier.py # Point-based 1-3 star scoring
│   │   ├── tagger.py              # LLM-based taxonomy tagging (Haiku 4.5)
│   │   ├── research_agent.py      # Blogpost/doc link content extraction
│   │   ├── report_generator.py    # Structured report generation (Sonnet)
│   │   ├── graph_generator.py     # Mermaid diagram generation (Opus)
│   │   └── storage_manager.py     # S3 CSV persistence
│   ├── website_builder/            # Lambda 2: Static Site Generator
│   │   ├── handler.py             # Entry point
│   │   └── builder.py             # HTML/CSS/JS generation
│   ├── analytics/                  # Lambda 3: Event Collector
│   │   └── handler.py             # API Gateway → S3 JSONL
│   └── shared/                     # Shared modules
│       ├── logger.py              # Structured JSON logging
│       └── models.py              # Data models (dataclasses)
├── infrastructure/                  # CDK Infrastructure
│   ├── app.py                     # CDK app entry point
│   └── stack.py                   # Full stack definition
├── scripts/                         # Utility scripts
│   ├── analytics_report.py        # Generate analytics CSV report via Athena
│   ├── retag_announcements.py     # Retroactively tag existing announcements
│   ├── generate_card_summaries.py # Backfill card summaries for existing data
│   └── test_local.py             # Local pipeline testing with mocked AWS
├── tests/                           # Tests (pytest + hypothesis)
├── docs/                            # Design documents and analysis
│   ├── taxonomy-analysis.md       # Multi-dimensional tagging taxonomy design
│   ├── scalability-analysis.md    # Growth projections and migration options
│   └── analytics-analysis.md     # Website analytics implementation options
├── deploy.sh                        # One-command full deployment
├── rebuild-site.sh                  # Quick redeploy + website rebuild
├── CHANGELOG.md                     # Issue tracking and feature log
├── cdk.json                         # CDK configuration
├── requirements.txt                 # Lambda runtime dependencies
└── requirements-dev.txt             # Development dependencies
```

## Quick Start

### Prerequisites

- Python 3.11+
- AWS CLI configured with credentials
- AWS CDK v2 (`npm install -g aws-cdk`)
- Bedrock model access enabled (Claude Sonnet 4.6, Opus 4.6, Haiku 4.5)

### Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install aws-cdk-lib constructs
```

### Deploy

```bash
./deploy.sh
```

Handles everything: installs deps, runs tests, bootstraps CDK, deploys the stack, and shows the CloudFront URL.

## Scripts Reference

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `./deploy.sh` | Full deployment (tests + CDK + deploy) | First deploy or major infra changes |
| `./deploy.sh --destroy` | Tear down the entire stack | Remove all resources |
| `./rebuild-site.sh` | Deploy code + rebuild website | After code changes |
| `./rebuild-site.sh --skip-cdk` | Just rebuild website (no CDK) | After data-only changes |
| `./rebuild-site.sh --pipeline` | Run full pipeline + rebuild | Fetch new news manually |
| `python scripts/retag_announcements.py` | Tag existing announcements | After adding tagging feature |
| `python scripts/generate_card_summaries.py` | Generate card summaries | After adding summary feature |
| `python scripts/analytics_report.py --days 30` | Generate analytics CSV | Check website usage metrics |

## How It Works

1. **EventBridge** triggers Lambda 1 daily at 22:00 UTC
2. **RSS Fetcher** retrieves the AWS "What's New" feed (100 items)
3. **Deduplication** skips previously processed announcements (by link)
4. **Relevance Filter** applies regex patterns for AI/ML/GenAI keywords
5. **Importance Classifier** computes a point score → 1/2/3 stars
6. **Taxonomy Tagger** (Haiku 4.5) assigns multi-dimensional tags across 5 dimensions
7. **Research Agent** follows blogpost/doc links for additional context
8. **Report Generator** (Sonnet 4.6) produces structured 6-section reports + card summary
9. **Graph Generator** (Opus 4.6) creates Mermaid architecture diagrams (2-3 star only)
10. **Storage Manager** appends results to CSV in S3
11. **Lambda 2** rebuilds the static website from CSV data
12. **CloudFront** serves the site with WAF protection and access logging

## Website Features

- **Faceted filtering** — clickable tag chips grouped by dimension (Services, Type, Concepts)
- **Time filtering** — All / Last Week / Last Month / Last 3 Months
- **Sort** — Newest first or Most important first
- **Taxonomy tags** — 5 dimensions: Services, Type, Concepts, Use Cases, Providers
- **Report pages** — 6 structured sections with bullet points + Mermaid diagrams
- **PDF export** — Client-side PDF generation via html2pdf.js
- **Timeline chart** — Stacked bar chart showing announcement volume over time
- **About modal** — Project methodology explanation
- **Analytics** — Client-side event tracking (pageviews, clicks, filter usage)

## Analytics

The site tracks usage via two mechanisms:
- **CloudFront access logs** → S3 (page views, unique IPs, geographic data)
- **Custom event tracking** → API Gateway → Lambda → S3 JSONL (clicks, filters, PDF exports)

Generate a report:
```bash
python scripts/analytics_report.py --days 30 --output report.csv
```

## Configuration

All tunable parameters live in `src/config.py`:
- AWS region and schedule (daily at 22:00 UTC)
- LLM model IDs: Sonnet 4.6 (reports), Opus 4.6 (graphs), Haiku 4.5 (tagging)
- Importance scoring weights and thresholds
- Prompt templates (report, graph, tagger)
- Timeouts and retry settings

No secrets in the repository — all credentials come from IAM roles at runtime.

## Development

```bash
# Run all tests
pytest tests/ -v

# Run specific test
pytest tests/test_cdk_stack.py -v

# Format code
black src/ tests/

# Type checking
mypy src/
```

## License

MIT

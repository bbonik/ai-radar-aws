# AI Radar AWS

Automated AWS AI/ML/GenAI news curation platform. Fetches the AWS "What's New" RSS feed daily, filters for AI relevance, classifies importance, assigns taxonomy tags, generates LLM-powered reports with Mermaid diagrams via Amazon Bedrock, and publishes a static website via CloudFront.

## Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │         Amazon Bedrock              │
                                    │  Sonnet 4.6 │ Opus 4.6 │ Haiku 4.5  │
                                    └──────────────────┬──────────────────┘
                                                       │
┌──────────────┐     ┌─────────────────────────────────┼───────────────────┐
│  EventBridge │────▶│  Lambda 1: Report Pipeline      │                   │
│  (Daily)     │     │  RSS → Dedup → Filter → Tag ────┘                   │
└──────────────┘     │  → Classify → Research → Report → Graph → Store     │
                     └──────────────────────────┬──────────────────────────┘
                                                │ async invoke
                     ┌──────────────────────────▼────────────────────────────┐
                     │  Lambda 2: Website Builder                            │
                     │  Read CSV → Generate HTML/CSS/JS → Upload → Invalidate│
                     └───────┬──────────────────────────────────┬────────────┘
                             │                                  │
                     ┌───────▼───────┐                  ┌───────▼───────┐
                     │  S3 (Data)    │                  │  S3 (Website) │
                     │  CSV storage  │                  │  Static files │
                     └───────────────┘                  └───────┬───────┘
                                                                │
                                                        ┌───────▼───────┐
                                              ┌────────▶│  CloudFront   │◀──── Users
                                              │         │  + WAF        │
                                              │         └───────────────┘
                                              │
┌─────────────────────────────────────────────┼────────────────────────────┐
│  Analytics                                  │                            │
│  ┌────────────┐    ┌──────────┐    ┌────────▼───────┐                    │
│  │ Browser JS │───▶│ API GW   │───▶│ Lambda 3       │──▶ S3 (Logs)       │
│  │ (tracking) │    │ POST     │    │ Event Collector│   + CF Access Logs │
│  └────────────┘    └──────────┘    └────────────────┘                    │
└──────────────────────────────────────────────────────────────────────────┘
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
│   │   ├── importance_classifier.py # Point-based 1-5 star scoring
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
│   ├── pipeline_health.py         # Pipeline health report (daily run status)
│   ├── retag_announcements.py     # Retroactively tag existing announcements
│   ├── reclassify_announcements.py # Recompute importance scores
│   ├── compute_geo_relevance.py   # Backfill geographic relevance badges
│   ├── generate_card_summaries.py # Backfill card summaries for existing data
│   ├── generate_missing_graphs.py # Backfill visual summaries for 2+ star items
│   └── test_local.py             # Local pipeline testing with mocked AWS
├── tests/                           # Tests (pytest + hypothesis)
├── docs/                            # Design documents and analysis
│   ├── taxonomy-analysis.md       # Multi-dimensional tagging taxonomy design
│   ├── scalability-analysis.md    # Growth projections and migration options
│   ├── analytics-analysis.md     # Website analytics implementation options
│   └── security-analysis.md      # Cybersecurity threat model and mitigations
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
- Node.js 20+ (18 works with warnings)
- AWS CLI configured with credentials
- Bedrock model access enabled (Claude Sonnet 4.6, Opus 4.6, Haiku 4.5)

### Setup & Deploy

```bash
git clone https://github.com/bbonik/ai-radar-aws.git
cd ai-radar-aws
./setup.sh    # One-time: checks prerequisites, creates venv, installs everything
./deploy.sh   # Deploys the full stack to AWS
```

That's it. Two commands from zero to a running website.

## Scripts Reference

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `./setup.sh` | Check prerequisites, create venv, install deps | First time after cloning |
| `./deploy.sh` | Full deployment (tests + CDK + deploy) | First deploy or major infra changes |
| `./deploy.sh --destroy` | Tear down the entire stack | Remove all resources |
| `./rebuild-site.sh` | Deploy code + rebuild website | After code changes |
| `./rebuild-site.sh --skip-cdk` | Just rebuild website (no CDK) | After data-only changes |
| `./run-pipeline.sh` | Trigger pipeline with live progress | See real-time processing status |
| `./rebuild-site.sh --pipeline` | Run full pipeline + rebuild | Fetch new news manually |
| `python scripts/retag_announcements.py` | Tag existing announcements | After taxonomy changes |
| `python scripts/retag_announcements.py --force` | Re-tag ALL announcements | When taxonomy tags are updated |
| `python scripts/reclassify_announcements.py` | Recompute importance scores | After scoring changes |
| `python scripts/generate_card_summaries.py` | Generate card summaries | After adding summary feature |
| `python scripts/generate_missing_graphs.py` | Backfill visual summaries | After lowering graph threshold |
| `python scripts/regenerate_all_graphs.py` | Clear + regenerate ALL visual summaries | After changing graph style/prompt |
| `python scripts/compute_geo_relevance.py` | Compute geographic relevance badges | After changing preferred geography |
| `python scripts/pipeline_health.py` | Pipeline health report (daily run status) | Check if pipeline is running correctly |
| `python scripts/pipeline_health.py --days 30` | Extended health report | Investigate historical failures |
| `python scripts/analytics_report.py --days 30` | Generate analytics CSV | Check website usage metrics |

### Running the Pipeline Manually

The pipeline runs automatically daily at 22:00 UTC. To trigger it manually with progress reporting:

```bash
./run-pipeline.sh
```

This invokes the pipeline synchronously and shows:
- How many RSS items were fetched and deduplicated
- How many are relevant AI/ML announcements
- Per-announcement progress with title and star rating
- Errors (if any)
- Final summary with duration and success/failure counts

The website is automatically rebuilt when the pipeline finishes. Hard-refresh (Cmd+Shift+R) after ~1-2 minutes to see new announcements.

## How It Works

1. **EventBridge** triggers Lambda 1 daily at 22:00 UTC
2. **RSS Fetcher** retrieves the AWS "What's New" feed (100 items)
3. **Deduplication** skips previously processed announcements (by link)
4. **Relevance Filter** applies regex patterns for AI/ML/GenAI keywords
5. **Taxonomy Tagger** (Haiku 4.5) assigns multi-dimensional tags across 6 dimensions (services from title only, types/concepts/use_cases/providers from full text, geo_availability via LLM)
6. **Importance Classifier** computes a point score → 1-5 stars (uses tags + geographic preference for scoring)
7. **Geographic Relevance** hybrid detection: APJ keyword match (authoritative) + LLM geo_availability + keyword fallback
7. **Research Agent** follows blogpost/doc links for additional context
8. **Report Generator** (Sonnet 4.6) produces structured 6-section reports + card summary
9. **Graph Generator** (Opus 4.6) creates Mermaid visual summaries (2-5 star only)
10. **Storage Manager** appends results to CSV in S3
11. **Lambda 2** rebuilds the static website from CSV data
12. **CloudFront** serves the site with WAF protection and access logging

## Website Features

- **Faceted filtering** — clickable tag chips grouped by dimension (Services, Type, Concepts)
- **Time filtering** — All / Last Week / Last Month / Last 3 Months
- **Sort** — Newest first or Most important first
- **Taxonomy tags** — 5 dimensions: Services, Type, Concepts, Use Cases, Providers
- **Geographic relevance badges** — 🌏 APJ (confirmed in your region) or 🌐 Global (available everywhere)
- **Report pages** — 6 structured sections with bullet points + Mermaid visual summaries
- **PDF export** — Client-side PDF generation via html2pdf.js
- **Timeline chart** — Stacked bar chart showing announcement volume over time (auto-aggregates to weekly when >90 days)
- **About modal** — Project methodology explanation
- **Analytics** — Client-side event tracking (pageviews, clicks, filter usage)

## Analytics

The site tracks usage via two mechanisms:
- **CloudFront access logs** → S3 (page views, unique IPs, geographic data)
- **Custom event tracking** → API Gateway → Lambda → S3 JSONL (clicks, filters, PDF exports)

Generate a report:
```bash
python scripts/analytics_report.py --days 30 --output report.csv  # Save to file
python scripts/analytics_report.py --days 30                       # Print to stdout (no file created)
```

## Configuration

All tunable parameters live in `src/config.py`:
- AWS region and schedule (daily at 22:00 UTC)
- LLM model IDs: Sonnet 4.6 (reports), Opus 4.6 (graphs), Haiku 4.5 (tagging)
- Importance scoring weights and thresholds
- Geographic preference (`preferred_geography`: apj, emea, americas, or global)
- Prompt templates (report, graph, tagger)
- Timeouts and retry settings

No secrets in the repository — all credentials come from IAM roles at runtime.

## Estimated Monthly Cost

Assumptions: ~7 new AI/ML announcements per week (~30/month), low website traffic (<10K page views/month).

| Service | Usage | Monthly Cost |
|---------|-------|-------------|
| **Bedrock — Sonnet 4.6** (reports) | 30 calls × ~2K input + 4K output tokens | ~$1.50 |
| **Bedrock — Opus 4.6** (diagrams) | 20 calls × ~2K input + 2K output tokens | ~$3.00 |
| **Bedrock — Haiku 4.5** (tagging) | 30 calls × ~1K input + 0.5K output tokens | ~$0.05 |
| **Lambda** (3 functions) | ~35 invocations/day, <5 min total | ~$0.01 |
| **S3** (3 buckets) | <50 MB storage, <1K requests/day | ~$0.01 |
| **CloudFront** | <10K requests, <1 GB transfer | ~$0.10 |
| **WAF** | 1 Web ACL + 2 rules | ~$6.00 |
| **API Gateway** (analytics) | <10K requests | ~$0.01 |
| **EventBridge** | 1 rule, 30 invocations | ~$0.00 |
| **CloudWatch** (logs + alarms) | 5 alarms, minimal logs | ~$0.50 |
| | | |
| **Total** | | **~$11/month** |

The dominant cost is **WAF** ($5/month for the Web ACL + $1/month per rule). Without WAF, the total drops to ~$5/month. Bedrock costs scale linearly with announcement volume.

> **Note**: Bedrock pricing varies by model and region. The estimates above use approximate on-demand pricing for the global inference profiles. Actual costs may differ based on token counts and regional pricing.

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

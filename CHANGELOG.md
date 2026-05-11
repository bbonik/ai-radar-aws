# AI Radar AWS — Changelog

Tracking document for features, bugs, and improvements.

---

## Batch 3 (2026-05-11)

| # | Status | Description |
|---|--------|-------------|
| F3 | ✅ Done | **Faceted filter redesign** — Replaced dropdowns/text input with clickable tag chips grouped by dimension. OR within dimension, AND across dimensions. Sort dropdown. |
| F4 | ✅ Done | **Card summary** — LLM-generated one-sentence summary (max 150 chars) for announcement cards. Complements the title. |
| F5 | ✅ Done | **Analytics (hybrid)** — CloudFront access logs + custom event tracking (API Gateway → Lambda → S3 JSONL). Athena report script. |
| F6 | ✅ Done | **"Last 3 Months" time filter** — Added fourth time option. |
| I3 | ✅ Done | **Card appearance** — Removed old orange service tag, prioritized Service+Type tags, standardized date to YYYY-MM-DD. |
| I4 | ✅ Done | **Consolidated analytics bucket** — All analytics data (CF logs + custom events) in one bucket. |

---

## Batch 2 (2026-05-09)

| # | Status | Description |
|---|--------|-------------|
| F1 | ✅ Done | **Multi-dimensional taxonomy tagging** — LLM-based tagging using Claude Haiku 4.5 across 5 dimensions. |
| F2 | ✅ Done | **Retag script** — `scripts/retag_announcements.py` for retroactive tagging. |

---

## Batch 1 (2026-05-09)

| # | Status | Description |
|---|--------|-------------|
| B1 | ✅ Fixed | **PDF export** — Added cdnjs.cloudflare.com to CSP. |
| B2 | ✅ Fixed | **Filters** — Fixed JS initialization timing (DOMContentLoaded already fired). |
| B3 | ✅ Fixed | **Timeline chart** — Same root cause as B2. |
| B4 | ✅ Fixed | **Date format** — Converted to DD/MM/YYYY, then standardized to YYYY-MM-DD. |
| B5 | ✅ Fixed | **JS syntax error** — Template placeholders produced `[...][]` breaking all client-side JS. |
| B6 | ✅ Fixed | **RFC 2822 date parsing** — RSS dates weren't ISO format, fixed parser. |
| I1 | ✅ Fixed | **Report readability** — Markdown rendering with bullet points for sections 2-6. |
| I2 | ✅ Fixed | **About section** — Modal with project methodology description. |

---

## Infrastructure Fixes (2026-05-08)

| # | Description |
|---|-------------|
| CDK app.py sys.path | Fixed `ModuleNotFoundError` when CDK CLI runs `python infrastructure/app.py` |
| Inference profile ARN | Changed from `foundation-model/` to `inference-profile/` ARN format |
| Model IDs | Updated from LEGACY models to global Sonnet 4.6 + Opus 4.6 |
| Bedrock IAM | Added foundation model ARNs to IAM policy (Bedrock resolves through them) |
| CloudFront URL output | Added `CfnOutput` so deploy script shows the website URL |

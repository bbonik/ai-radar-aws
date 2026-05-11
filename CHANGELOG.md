# AI Radar AWS — Issues & Feature Requests

Tracking document for bugs, improvements, and feature requests.

---

## Batch 2 (2026-05-09)

### Features

| # | Status | Description |
|---|--------|-------------|
| F1 | ✅ Done | **Multi-dimensional taxonomy tagging** — LLM-based tagging using Claude Haiku 4.5. Each announcement gets tags across 5 dimensions: AWS Service, Announcement Type, AI/ML Concept, Use Case, Model Provider. Tags visible on cards and report pages. Tag-based filtering with autocomplete on the main feed. |

---

## Batch 1 (2026-05-09)

### Bugs

| # | Status | Description |
|---|--------|-------------|
| B1 | ✅ Fixed | **PDF export does not work** — Clicking the "Export as PDF" button does nothing. |
| B2 | ✅ Fixed | **Filters do not work** — Service filter, time period filter, and "Rank by Importance" checkbox have no effect when clicked. |
| B3 | ✅ Fixed | **Announcement Timeline shows nothing** — The timeline chart section is empty, no data is rendered. |
| B4 | ✅ Fixed | **Date format confusing** — Announcement cards show raw date strings. Convert to DD/MM/YYYY format. |

### Improvements

| # | Status | Description |
|---|--------|-------------|
| I1 | ✅ Fixed | **Report view readability** — Reports are plain blocks of text. Use markdown rendering with bold/italics. Use bullet points for all sections except "What's New" (which stays as a summary paragraph). Bullet points should preserve all concepts but make content scannable. |
| I2 | ✅ Fixed | **About section** — Add a non-intrusive "About" section (e.g., a modal or collapsible panel accessible from the header) explaining the project's objective and methodology. Highlight the research phase so users appreciate the effort behind each report. Render as markdown. |

---

## Completed

| # | Date | Description |
|---|------|-------------|
| B1 | 2026-05-09 | PDF export — added cdnjs.cloudflare.com to CSP |
| B2 | 2026-05-09 | Filters — fixed JS initialization timing |
| B3 | 2026-05-09 | Timeline — fixed Chart.js init (same root cause as B2) |
| B4 | 2026-05-09 | Date format — converted to DD/MM/YYYY display |
| I1 | 2026-05-09 | Report readability — markdown rendering with bullet points for sections 2-6 |
| I2 | 2026-05-09 | About section — modal with project methodology description |

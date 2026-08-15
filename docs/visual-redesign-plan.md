# AI Radar AWS — Visual Redesign Plan

Working document for the 2026-08 look-and-feel update. Follows the conventions of
`docs/audit-remediation-plan.md`: every item states the current state, the proposed
change with exact values, the reasoning, the knobs open to adjustment, and what would
verify it. Nothing is implemented until the owner signs off per item.

- **Branch**: `feature/visual-redesign`
- **Scope**: presentation only — all changes live in `src/website_builder/builder.py`
  templates (CSS/JS/HTML strings). No pipeline, data, or infrastructure changes.
- **Ship mechanism**: one `cdk deploy` (new Lambda bundle) + website rebuild.
  Every item is independently revertible (CSS variables / template blocks).

**Status legend** — `PROPOSED` · `APPROVED` · `IN PROGRESS` · `DONE` · `REJECTED`

---

## Design principles

1. **Structure stays.** Card grid, filter panel, timeline, report layout are sound.
   This is a palette-and-polish pass, not a redesign.
2. **One accent, one scale, muted everything else.** Professional = restraint:
   the AWS orange is the single brand accent; importance is the single semantic
   color scale; all other color (tags, badges) drops to low-saturation supporting
   roles.
3. **Color never carries meaning alone.** The previous heat-ramp experiment failed
   because isolated color identification is unreliable. Every color encoding gets a
   redundant channel: words, fill-ratio, thickness, or position.
4. **No new dependencies.** System font stack stays (fast, CSP-clean). No webfonts,
   no CSS framework, no icon library. Inline SVG where an icon is unavoidable.
5. **PDF inherits everything.** Print output uses the same tokens; print-specific
   fixes are part of this plan, not an afterthought.

---

## Item index

All items APPROVED by the owner 2026-08-14, with knob decisions recorded below and
one amendment: **V3's weekly-aggregation threshold change was rejected** — the
90-day threshold stays as-is.

| ID | Item | Impact | Risk | Status |
|----|------|--------|------|--------|
| V1 | Importance color scale (wide-arc ramp) | High | Low — CSS variables | APPROVED (5★ = red `#ef4444`) |
| V2 | Redundant importance encoding (labels, gauge stars, 5★ marker) | High | Low | APPROVED (label on ALL cards) |
| V3 | Timeline chart restyle | Medium | Low | APPROVED **minus aggregation change** |
| V4 | Tag chip system normalisation | Medium | Low | APPROVED (hard rule: tags never wrap to a 2nd line) |
| V5 | Geo badges: replace emoji | Medium | Low | APPROVED (text only, no SVG) |
| V6 | Typography scale | Medium | Low | APPROVED |
| V7 | Header polish + stats strip | Low-Med | Low | APPROVED (stats strip = own hairline-bordered row) |
| V8 | PDF / print fixes (incl. "?" button bug) | High for exports | Low | APPROVED |
| V9 | Micro-polish (hover, focus, footer link colors) | Low | Low | APPROVED |

Explicitly **out of scope** (rejected during analysis): dark mode, hosted webfonts,
animation libraries, card-grid or filter-UX redesign, self-hosting Mermaid/Chart.js.

---

# V1 — Importance color scale

**Status**: PROPOSED · the keystone item; V2/V3 depend on it

### Current state

```css
--star-1: #9e9e9e;  /* gray    */
--star-2: #2476F9;  /* blue    */
--star-3: #24F93D;  /* green   */
--star-4: #f9a825;  /* amber   */
--star-5: #f924e1;  /* magenta */
```

Used in four places: star glyphs, card left borders, chart stacks, PDF header stars.
Problems: no perceptual ordering (green reads more positive than amber; magenta is
not "hotter" than orange); full saturation everywhere; clashes with the orange brand.

### Prior art (owner's experiment)

A heat ramp was tried before and rejected: 3/4/5 were indistinguishable on small
surfaces. Diagnosis: saturation-only / narrow-hue ramps have small steps, and card
colors are judged in isolation (absolute identification) rather than side by side.
The redesign must therefore vary **hue AND lightness together** per step, and V2
must add non-color channels.

### Proposed scale

Wide-arc sequential ramp (YlOrRd family with a cool tail). Every adjacent pair
differs in both hue and lightness; the ramp is monotonic in "heat" and survives
grayscale conversion (verifiable by desaturating a screenshot).

| Level | Name | Border/chart fill | Star glyph / text | Rationale |
|---|---|---|---|---|
| 1★ | Peripheral | `#d1d5db` | `#9ca3af` | barely-there light gray |
| 2★ | Standard | `#94a3b8` | `#64748b` | cool slate — clearly darker than 1★ |
| 3★ | Notable | `#facc15` | `#ca8a04` | light **yellow** — categorical warm jump |
| 4★ | Important | `#fb923c` | `#ea580c` | medium **orange** — redder AND darker than 3★ |
| 5★ | Critical | `#ef4444` | `#b91c1c` | dark **red** — darkest of the warm trio |

Two-column design: large surfaces (borders, chart bars) use the lighter fill tone;
small glyphs and text use the darker tone of the same hue for contrast on white.
This directly addresses the "yellow glyphs illegible on white" failure mode.

Pairwise discrimination check (the test the old ramp failed):
- 3 vs 4: yellow vs orange (~25° hue) + light vs medium — two channels
- 4 vs 5: orange vs red + medium vs dark — two channels
- 2 vs 3: cool vs warm — categorical
- 1 vs 2: light vs dark neutral

### Knobs

| Knob | Proposed | Alternatives considered |
|---|---|---|
| 5★ hue | red `#ef4444` | deep AWS-orange `#c2410c` (more on-brand, but collapses distance to 4★) |
| 3★ family | yellow | amber `#f59e0b` (closer to 4★ — rejected for discrimination) |
| 1–2★ | neutral grays | light blues (adds a hue for no benefit) |
| Glyph/text tones | one step darker per hue | same as fills (fails contrast on yellow) |

### Verification

Rebuild, then the owner repeats the exact test the previous ramp failed: identify
3/4/5 on isolated live cards at real size, plus a desaturated screenshot to confirm
grayscale ordering. WCAG note: glyph tones ≥ 3:1 against white (large-text/graphics
threshold); the CARD does not rely on color alone once V2 lands.

---

# V2 — Redundant importance encoding

**Status**: PROPOSED · what makes V1 safe; ships in the same commit

### 2a. Word label next to the stars

The chart legend already names the levels (Critical / Important / Notable /
Standard / Peripheral) — put the word on the card. Reading beats decoding.

```
★★★★★ CRITICAL              ★★★☆☆ NOTABLE
```

- 10px, uppercase, 0.06em letterspacing, weight 700, in the level's text tone
- Rendered in card header (left, after stars) and report page header
- Also fixes grayscale PDF exports, where any color scale dies

### 2b. Star row as a gauge

Unfilled stars currently render in the same color as filled (`★★★☆☆` all one
color). Change: unfilled `☆` in `#e5e7eb` at full size, so the filled/unfilled
boundary reads as a fill level — "mostly full" is perceived without counting.

### 2c. 5★ categorical marker

The hardest discrimination is 4 vs 5, and 5★ is rare (a few per month). Only 5★
cards get one extra treatment so the top level is marked categorically, not
chromatically:

- Border-left thickens 4px → 6px (all other cards keep 4px)
- Nothing else — no banner, no tinted background (tried mentally, too loud)

### Knobs

| Knob | Proposed | Alternative |
|---|---|---|
| Label placement | after stars, same line | under date (right-aligned) — weaker association |
| Label on 1–2★ | yes (consistency) | omit below 3★ for less noise — owner's call |
| 5★ marker | 6px border | small "▲" prefix; tinted header strip |

---

# V3 — Timeline chart restyle

**Status**: PROPOSED · depends on V1 palette

### Current state

Chart.js stacked bars in the five candy colors; legend ~10px; dense y-gridlines;
daily bars up to 90 days (thin and noisy at 3 months — see screenshots); stack
order puts 5★ mid-stack.

### Proposed

| Change | Value | Why |
|---|---|---|
| Colors | V1 fill tones | one scale everywhere |
| Stack order | 5★ at the **bottom** | most important segment on a stable baseline, comparable across bars |
| ~~Weekly aggregation threshold~~ | **REJECTED by owner** — stays at >90 days | daily granularity preferred in the 3-month view |
| Bar style | `borderRadius: 3`, `maxBarThickness: 28` | reads "product", prevents obese bars on short ranges |
| Y-gridlines | `color: #f1f5f9`, no x-gridlines, `ticks.maxTicksLimit: 6` | quieter frame |
| Legend | 11px, point-style markers (rounded), more padding | legibility |
| Tooltip | dark `#1e293b` background, show level name + count | matches header |

No library change; all Chart.js options in the existing `renderTimeline` config.

---

# V4 — Tag chip system normalisation

**Status**: PROPOSED

### Current state

Five material-design pastel pairs at mixed lightness:

```css
.tag-service  { background: #e3f2fd; color: #1565c0; }  /* blue   */
.tag-type     { background: #f3e5f5; color: #7b1fa2; }  /* purple */
.tag-concept  { background: #e8f5e9; color: #2e7d32; }  /* green  */
.tag-usecase  { background: #fff3e0; color: #e65100; }  /* orange */
.tag-provider { background: #fce4ec; color: #c62828; }  /* red    */
```

Legitimate concept (dimension coding) but competes with the old star colors; with
V1 the chips become the main "colorful" element and need one consistent formula.

### Proposed

Same five hues (continuity for existing users), rebuilt on one formula —
tint-50 background, shade-700 text, tint-200 1px border, identical lightness across
dimensions (Tailwind-scale values for consistency):

| Dimension | Background | Border | Text |
|---|---|---|---|
| services | `#eff6ff` | `#bfdbfe` | `#1d4ed8` |
| types | `#faf5ff` | `#e9d5ff` | `#7e22ce` |
| concepts | `#f0fdf4` | `#bbf7d0` | `#15803d` |
| use_cases | `#fff7ed` | `#fed7aa` | `#c2410c` |
| providers | `#fdf2f8` | `#fbcfe8` | `#be185d` |

Plus: chip radius from pill to 4px (pills everywhere read "toy"; small radius reads
"data"), font-size 11px, weight 500, remove any uppercase transform on values.
Filter-bar chips (currently white outlined) stay neutral but adopt the same radius
and get a hover tint; the selected state stays brand orange.

### Knob — DECIDED

Owner's rule: the exact cap (5 vs 6) is unimportant; **what matters is that card
tags always occupy exactly one line and never wrap to a second**. Since tag name
lengths vary, a count cap alone cannot guarantee this. Implementation: keep the
cap at 6 as the upper bound, and enforce the single line in CSS —
`flex-wrap: nowrap; overflow: hidden;` on the tag row plus a right-edge fade-out
mask (`mask-image: linear-gradient(90deg, #000 calc(100% - 24px), transparent)`),
so when long tags exceed the row, the overflow fades gracefully instead of
clipping mid-chip or wrapping. Deterministic single line for any tag lengths.

---

# V5 — Geo badges: replace emoji

**Status**: PROPOSED

### Current state

Unicode emoji globes baked into HTML (`🌐 Global`, `🌏 APJ`, `🌍 EMEA`, `🌎 AMER`)
in cards, report pages, and the About legend. Emoji render differently per
OS/browser, can't be styled or colored, and are the single strongest "hobby
project" signal in the UI.

### Proposed (per owner decision: text only, no icon)

- Regional badges (`APJ`, `EMEA`, `AMER`): `#f8fafc` bg, `#cbd5e1` border,
  `#475569` text, 4px radius, 10px uppercase, 600 weight
- `GLOBAL` gets a slightly stronger tint (`#eff6ff` bg, `#bfdbfe` border,
  `#1d4ed8` text) since it's the "good news" case
- Same markup in cards, report tags section, and the About legend (all three
  currently carry emoji)

---

# V6 — Typography scale

**Status**: PROPOSED

### Current state

System font stack (keep — fast, native, CSP-clean). Sizes cluster in a narrow band;
title/summary/date/meta separated mostly by boldness; header tagline is
near-invisible navy-on-navy.

### Proposed scale (the only sizes used anywhere)

| Role | Size / weight / color | Notes |
|---|---|---|
| Card title | 16.5px / 650 / `#0f172a` | `letter-spacing: -0.01em`, `line-height: 1.35` |
| Card summary | 13.5px / 400 / `#475569` | `line-height: 1.55` |
| Date + meta | 11px / 500 / `#94a3b8` | tabular-nums for dates |
| Importance label (V2) | 10px / 700 / level tone | uppercase, 0.06em |
| Chips | 11px / 500 | per V4/V5 |
| Section headings (panels, report H2) | 15px / 650 / `#0f172a` | |
| Report page H1 | 26px / 700 | `letter-spacing: -0.02em` |
| Report body | 15px / 400 / `#334155` | `line-height: 1.65` |
| Header tagline | 12px / 400 / `#94a3b8` | fixes the contrast problem |

Global: body text color `#16191f` → slate scale (`#0f172a` headings / `#334155`
body / `#475569` secondary / `#94a3b8` muted) — warmer and less harsh than
near-black on white; consistent with the token cleanup in V1/V4.

---

# V7 — Header polish + stats strip

**Status**: PROPOSED

- Tagline contrast per V6
- Nav links: 13px/500, `#cbd5e1` at rest → white on hover, orange 2px underline
  offset 6px for the hovered/active item (currently no state at all)
- **Stats strip** — new, slim bar between header and filters:
  `252 announcements · updated daily · latest 2026-08-14`
  12px muted text, no background, right-aligned counterpart to the "Filter
  Announcements" heading, or its own hairline-bordered row (knob). All three
  numbers already exist in the builder's data at generation time; zero runtime
  cost. Makes an automated site read as *alive*.
- Logo/wordmark unchanged.

---

# V8 — PDF / print fixes

**Status**: PROPOSED · contains one outright bug fix

### 8a. BUG: floating "?" button prints on every page

The dark circular help button (bottom-right of every PDF page — see the export
sample) is fixed-position chrome missing from the `@media print` hide list.
Identify the element (it is not `.about-modal-overlay`, already hidden) and add it.
Single-selector fix; the largest single improvement to export quality.

### 8b. Related Resources: stop printing raw URLs

Bare URLs wrap badly (one wraps mid-word in the sample). Render as the page's
domain + a readable path or the link's display title, e.g.
`AWS ML Blog — Accelerate cyber defense with OpenAI Daybreak…` with the full URL
retained as `href` (and shown in a smaller muted line only in print, where hrefs
are unclickable). Screen and print both improve.

### 8c. Page composition

- Page 1 currently ends after the visual summary with a half-page of whitespace:
  allow *What's New* to start on page 1 (`page-break-*` adjustments), keep
  `break-inside: avoid` on sections so none splits mid-bullet
- Slim print footer via `@page` margins where supported: site name left, page
  counter right — cheap, signals care
- Stars in print: V1 tones already survive grayscale; V2 word label makes the
  level explicit even on a B/W office printer

### 8d. Mermaid in print

Diagram container gets a max-height guard and centered scaling so tall diagrams
never overflow the page (the sample is fine; guard is preventive).

---

# V9 — Micro-polish

**Status**: PROPOSED · batched, all trivial

- Card hover: shadow deepens + `translateY(-1px)`, 150ms ease (currently shadow-only)
- `Full report →` link: brand orange kept, but arrow gets `transition: transform`,
  2px slide on hover
- Focus states: visible `outline: 2px solid #f59e0b; outline-offset: 2px` on chips,
  links, and the sort select (keyboard nav currently shows browser default or none)
- Sort dropdown + Reset button: align heights and radii with the chip system
- Selection color: brand-tinted `::selection`
- Footer (if any) link colors to match V6 muted scale

---

# Implementation & verification

**Order**: V1+V2 together (one commit — the scale must never ship without its
redundancy) → V8 (independent, high value) → V3 → V4+V5 → V6 → V7 → V9.
Each step: edit templates → run test suite (the builder property tests assert
structural invariants, not colors — expected to pass untouched) → `cdk deploy` +
rebuild → owner eyeballs the live site before the next item starts.

**The acceptance test for V1/V2** is the one the previous ramp failed: the owner
identifies 3/4/5 on isolated live cards at actual size, plus a desaturated
screenshot check for grayscale ordering. If it fails again, V1 reverts to a wider
hue arc (teal→amber→red) before any thought of returning to categorical colors.

**Cost/risk**: zero new dependencies, zero data changes, zero infra changes beyond
the Lambda bundle. Full revert of any item = restoring one CSS/template block.

# Owner decisions (2026-08-14)

1. V2: importance label on **all** cards.
2. V4: cap flexible (≤6); the binding rule is **tags never wrap to a second line**
   — enforced in CSS with nowrap + fade-out mask (see V4).
3. V5: **text-only** badges, no SVG icon.
4. V7: stats strip as its **own hairline-bordered row**.
5. V1: 5★ = **red `#ef4444`** as proposed.
6. V3 amendment: weekly-aggregation threshold **stays at 90 days** (45-day
   proposal rejected).

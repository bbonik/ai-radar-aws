# AI Radar AWS — Audit Remediation Plan

Working document for the code and infrastructure audit conducted 2026-08-13.
Tracks 17 work items, the decision taken for each, the alternatives rejected, and
the verification required before an item is considered done.

- **Branch**: `fix/audit-remediation`, cut from `main` @ `46ba08b`
- **Scope**: `aws-news-extractor/` — pipeline, website builder, analytics, CDK stack
- **Working agreement**: one item per commit; test suite green before each is presented;
  CDK diff shown and approved before any deploy; items marked `NEEDS DECISION` get
  options rather than an assumed answer.

## Guiding principle (added 2026-08-14)

Simplicity beats features over time. This is a well-engineered pet project, not a
production system with an on-call rota. Every item was re-evaluated against one test:
**does the fix add fewer moving parts than the problem it removes?** Where the answer
was no, the item was trimmed or deferred, with the reasoning recorded in place so the
decision can be revisited if circumstances change (traffic, observed abuse, the
multi-source track).

### What the simplicity pass changed

| Item | Change | Reasoning in one line |
|---|---|---|
| 0 | General `AI_RADAR_*` override framework → **one env var** | Exactly one field is deployment-specific; a framework for a hypothetical second field is the over-engineering being avoided |
| 1 | S3 server access logging **dropped** | Versioning already answers "what was the previous content", which is what recovery needs |
| 3 | Stateful line-count guard **dropped** | Needs persisted state to compare against; narrowed exceptions + the rebuild check cover the realistic failure |
| 6 | Redirect stubs and alias map **dropped** | Slugs are pure functions of links stored forever — old→new is derivable at any time; 404s are honest for a low-traffic rename |
| 8 | Rolling-estimate gate and CSV flag **dropped** | Fixed 90 s gate + one loop deadline; no state, no schema change |
| 12 | WAF / CloudFront-origin **deferred** | 10× tighter throttle + IP truncation + budget alarm + lifecycle expiry bound the damage; no standing infra for a hypothetical attacker |
| 13 | Redirect cap **dropped** | Custom opener subclass for marginal benefit; the size cap bounds what any redirect target can cost |
| 14 | IAM scoping **deferred entirely** | Versioning covers the risk; the change itself risks subtle `NoSuchKey`→`AccessDenied` breakage |
| 15 | SQS DLQ **dropped** | The `Lambda2-Errors` alarm (Item 2) already delivers the same signal; the async payload is a `run_id`, worthless to retain |
| 17e | mypy config → **remove the README claim instead** | A type-checking regime should be adopted deliberately or not at all |
| 19 | CI trimmed to **pytest + zero-config synth + secret grep** | Three steps, one workflow file |

What did **not** change: every Critical item (1, 2, 3), the slug function fix (6a),
concurrency (4), schema errors (5), asset excludes (10), CSP (11), the core fetch caps
(13), and all four generalisation items (0, 18, 19, 20) — those already are the simple
versions of their problems.

**Status legend** — `TODO` · `NEEDS DECISION` · `IN PROGRESS` · `IN REVIEW` · `DONE` · `WONTFIX`

---

## Repository model — two audiences, one codebase

This repository serves two roles simultaneously:

- **Upstream open-source project**, cloned and deployed by third parties who have their
  own account, region, domain, and preferences.
- **The maintainer's own live deployment**, which needs concrete values for all of those.

These are only in tension if deployment-specific values live in committed files. They do
today, which is the root cause of Item 9 and Item 20. The resolution is a three-layer
separation, established by Item 0 and applied by every item after it.

| Layer | Contents | Committed? |
|---|---|---|
| **Generic** | Code, CDK, tests, docs. No account IDs, domains, emails, or personal preferences. Must synthesize and pass tests with zero local configuration. | Yes |
| **Example** | `cdk.context.json.example` — every supported key, documented, with placeholder values. | Yes |
| **Local** | `cdk.context.json` — the maintainer's real values. Also any runtime overrides (see D9). | **No — gitignored** |

Two rules follow, and they apply to every item in this plan:

1. **Absent configuration must degrade, not fail.** A fresh clone with no local config must
   deploy successfully to a working default state. Where a feature needs a value that has
   no sensible generic default (an alert email), the feature is built and wired but left
   unsubscribed, with a deploy-time warning — never omitted, and never a synth failure.
2. **The generic path must be tested, not assumed.** Item 19 adds a test asserting the
   stack synthesizes with empty context. Without it, personal configuration silently
   becomes mandatory over time, which is exactly how the current state arose.

### Applies to this document

This plan lives in `docs/` and is therefore published. Live account identifiers, bucket
names, and the deployment domain are **redacted to placeholders** here. The unredacted
measurements are in `docs/audit-evidence.local.md`, which Item 0 adds to `.gitignore`.
Placeholders used: `<ACCOUNT_ID>`, `<CERT_ID>`, `<ZONE_ID>`, `<SITE_DOMAIN>`,
`<DATA_BUCKET>`.

---

## Decision log

Most decisions were resolved by the 2026-08-14 simplicity pass; per-item reasoning is
recorded in place. Still genuinely open: D1 (your email, whenever) and any tweaks you
want to the D3 slug knobs before 6a is implemented.

| # | Question | Blocks | Resolution |
|---|----------|--------|------------|
| D1 | Email address for the SNS alarm topic | Item 2 | **Not blocking.** Topic and wiring are generic; add your address to local config whenever. Deploy warns while unsubscribed. |
| D2 | Rewrite git history to purge the account ID? | Item 9 | **Resolved: no rewrite.** Remove from HEAD; optionally rotate the certificate. Least disruptive. |
| D3 | Exact slug format | Item 6 | **Default adopted**: `<last-path-segment>-<8-char-hash>`. Knobs table in Item 6 stays open to your tweaks before implementation. |
| D4 | Old report URLs after the slug change | Item 6 | **Resolved: no redirect stubs, no alias map.** Delete orphaned pages; see Item 6 for why the alias map is derivable and therefore unnecessary. |
| D5 | WAF for the analytics endpoint | Item 12 | **Resolved: deferred.** 10× tighter throttling instead; budget alarm is the cost backstop. Revisit only on observed abuse. |
| D6 | De-identify client IPs at ingest | Item 12 | **Resolved: yes.** Truncate IPv4 to /24, IPv6 to /48 in the handler. ~5 lines. |
| D7 | Research time budget behaviour | Item 8 | **Resolved:** fixed 90 s gate + in-loop deadline. No rolling statistics, no new CSV column. |
| D8 | CSV storage growth | Item 16 | **Resolved: document only.** Revisit when the AI-news track is designed. |
| D9 | Runtime config override mechanism | Items 0, 20 | **Resolved: one env var** (`PREFERRED_GEOGRAPHY`), injected by the stack from CDK context. No general framework — build one only when a second field needs it. |

---

## Sequencing rationale

Phase 0 comes first because items 3, 6, and 12 all rewrite data in S3 and there is
currently **no versioning on the data bucket**. Running a migration before the safety
net is in place would stake the only copy of 243 LLM-generated reports on the migration
code being correct. Alarms come second so that failures introduced during the remaining
work surface immediately rather than silently.

Within later phases, ordering is by blast radius ascending: code-only changes before
CDK deploys, CDK deploys before data migrations.

---

## Item index

| ID | Item | Phase | Severity | Blast radius | Status |
|----|------|-------|----------|--------------|--------|
| 0 | Configuration architecture (prerequisite for 2, 9, 20) | 0 | High | Config + docs | TODO (D9 resolved) |
| 1 | Protect the data bucket | 0 | Critical | CDK deploy | TODO |
| 2 | Wire alarms and budget to SNS | 0 | Critical | CDK deploy | TODO (D1 optional) |
| 3 | Fix the links-index write path | 1 | Critical | Code only | TODO |
| 4 | Serialise pipeline runs | 1 | Medium | CDK deploy | TODO |
| 5 | Fail loudly on CSV schema drift | 1 | High | Code only | TODO |
| 6 | Slug collisions | 2 | High | Data + site migration | TODO (D3 tweaks welcome) |
| 7 | Relevance filter accuracy | 2 | Medium | Analysis first | NEEDS DECISION (data) |
| 8 | Research time budget | 2 | Medium | Code only | TODO |
| 9 | Account identifiers out of the repo | 3 | High | Config only | TODO (D2 resolved) |
| 10 | Stop bundling local files into Lambdas | 3 | High | CDK deploy | TODO |
| 11 | Tighten CSP and Mermaid | 3 | Medium | CDK deploy + rebuild | TODO |
| 12 | Analytics: throttle + de-identify (WAF deferred) | 3 | Medium | CDK deploy + code | TODO |
| 13 | Harden outbound fetching | 3 | High | Code only | TODO |
| 14 | Scope IAM down | 3 | Medium | CDK deploy | **DEFERRED** |
| 15 | Failure visibility (stage variable only) | 4 | Low | Code only | TODO |
| 16 | CSV storage growth | 4 | Medium | Document only | TODO (D8 resolved) |
| 17 | Dead code and doc drift | 5 | Low | Code only | TODO |
| 18 | Utility scripts honour `Config` | 3 | Medium | Code only | TODO |
| 19 | CI workflow + zero-config synth guard | 0 | High | New file | TODO |
| 20 | Neutral generic defaults (`preferred_geography`) | 3 | Medium | Code + local config | TODO (D9 resolved) |

Items 0, 18, 19 and 20 were added after establishing the dual-audience requirement. They
are what make the repository genuinely re-deployable by a third party; without them the
other fixes harden one private deployment.

---

# Phase 0 — Safety net

## Item 0 — Configuration architecture

**Status**: TODO (D9 resolved) · **Severity**: High · **Prerequisite for**: 2, 9, 20

This item exists because the repository has two audiences (see *Repository model* above).
It establishes the layering that every subsequent item depends on. Nothing else in
Phase 0 should land first, because items 1, 2 and 12 all need a way to express
"this deployment wants X" without committing X.

### Evidence

Deployment-specific values are currently committed in three separate mechanisms, with no
consistent pattern:

| Value | Where | Mechanism |
|---|---|---|
| Domain, certificate ARN, hosted zone ID | `cdk.json` `context` | CDK context, committed |
| AWS region, schedule, geography preference, model IDs, scoring weights | `src/config.py` | Python dataclass defaults, committed |
| Region, stack name, function names, log group | 11 files under `scripts/` and `*.sh` | Hardcoded literals |

The CDK context mechanism is already correct — `try_get_context` with graceful fallback
to the CloudFront URL is exactly right. The defect is only that real values were written
into the committed `cdk.json` instead of a gitignored overlay. CDK reads
`cdk.context.json` and merges it over `cdk.json` automatically, so the fix needs no new
machinery.

`src/config.py` is a harder case: it is read at Lambda **runtime**, not synth time, so
context is not directly available to it. See D9.

### Decision — resolved 2026-08-14, simplicity pass (D9)

**Deploy-time values** (domain, certificate, zone, alert email): CDK context. Real
values in gitignored `cdk.context.json`; every key documented in a committed
`cdk.context.json.example`; all reads via `try_get_context` with a working generic
fallback. This mechanism already exists in the codebase and needs no new machinery.

**Runtime values**: exactly one field in `Config` is deployment-specific —
`preferred_geography` (established by the Item 20 audit of every field). So **no general
override framework**. The stack injects a single `PREFERRED_GEOGRAPHY` environment
variable from CDK context; `Config.__post_init__` reads it, validates it against the
four allowed values, and otherwise keeps the dataclass default. One env var, one field,
roughly six lines.

If a second field ever needs per-deployment override, copy the pattern. Building a
generic `AI_RADAR_*` framework for a hypothetical second user of it is exactly the
over-engineering this plan now avoids.

### Alternatives considered for D9

The four mechanisms below were evaluated as *general frameworks* before the simplicity
pass concluded that no framework is needed at all — only the first option's technique,
scoped to a single variable. Retained for the record.

- **Env-var overrides populated from CDK context** *(adopted, scoped to one variable)*. One mechanism, works
  identically in Lambda and in local scripts, no import-order tricks, and `config.py`
  stays the documented default set. Cost: a small amount of coercion code for
  ints/floats/bools, and each overridable field must be listed explicitly.
- **A gitignored `config_local.py` overlay imported if present.** Simple and typed, no
  coercion needed. Rejected as primary: a `try: import` at module scope is easy to
  misread, it does not reach Lambda unless bundled (and Item 10 is actively excluding
  local files from bundles — directly contradictory), and it splits the tunable inventory
  across two files.
- **SSM Parameter Store read at Lambda cold start.** Most flexible, allows changes with
  no redeploy. Rejected as disproportionate: adds a runtime AWS dependency and latency to
  every cold start, for values that change rarely. Worth revisiting only if you want to
  retune scoring weights without deploying.
- **Everything in CDK context, `config.py` reduced to a schema.** Rejected: `config.py`
  holding prompt templates and scoring weights as readable Python is a genuine strength
  of this codebase, and Requirement 15 of the original spec mandates a single
  configuration file.

### Proposed change

1. `cdk.context.json.example`, committed, documenting every key:
   `custom_domain`, `certificate_arn`, `hosted_zone_id`, `alert_email`,
   `preferred_geography`. Placeholder values only.
2. `cdk.context.json` created locally with your real values; added to `.gitignore`
   alongside `docs/audit-evidence.local.md`.
3. `Config.__post_init__` reading `PREFERRED_GEOGRAPHY` from the environment, validating
   against `{apj, emea, americas, global}`, raising a clear error on anything else.
4. Stack injects `PREFERRED_GEOGRAPHY` into the pipeline Lambda from context when set.
5. `scripts/_common.py` (Item 18) loads `cdk.context.json` if present and sets the same
   env var, so laptop scripts and Lambdas resolve identical config from one file.
6. README section: "Configuring your own deployment", covering the example file, the
   generic defaults, and what happens when nothing is set.

### Verification

1. `cdk synth` with **no** `cdk.context.json` present — succeeds, produces a stack with
   the CloudFront domain, no Route 53 record, no email subscription. This is the fresh
   cloner path and is currently untested; Item 19 makes it a permanent test.
2. `cdk synth` with the local file present — produces the custom domain, certificate,
   Route 53 record, and email subscription.
3. `git status` after creating the local file — shows nothing to commit.
4. `Config()` with no env vars returns documented defaults; with
   `PREFERRED_GEOGRAPHY=apj` returns `apj`; with an invalid value raises a clear error
   rather than silently falling back.

### Open questions

None — D9 resolved. `alert_email` lives in CDK context rather than a `deploy.sh` flag:
persistent across deploys, nothing to remember per invocation.

---

## Item 1 — Protect the data bucket

**Status**: TODO · **Severity**: Critical · **Covers**: audit Critical #2, Sec-Low #11

### Evidence

`infrastructure/stack.py` defines `DataBucket` (live name `<DATA_BUCKET>`) with:

```python
removal_policy=RemovalPolicy.DESTROY,
auto_delete_objects=True,
```

No `versioned=True` on any bucket in the stack — verified by grep, zero matches for
`versioned`, `alarm_actions`, `dead_letter`, or `notifications_with_subscribers`.
`deploy.sh --destroy` is a documented command in the README scripts table. Running it
irreversibly deletes `database/announcements.csv` (2.4 MB, 243 announcements, every
Sonnet and Opus generation paid for to date) and `database/links.txt`.

### Decision

Enable versioning and switch the data bucket to `RETAIN`. Leave the website and logs
buckets as `DESTROY` — both are genuinely reproducible (the site rebuilds from CSV;
logs are expiring by design).

Server access logging was in the original proposal and was **dropped in the simplicity
pass**: it requires reordering the bucket declarations plus an extra lifecycle rule, and
the question it answers ("who wrote this object") matters far less than the one
versioning answers ("what was here before"), which is what recovery actually needs.
Covers audit Sec-Low #11 by explicit acceptance rather than by fix.

### Alternatives considered

- **AWS Backup plan on the bucket** — rejected. Versioning plus a non-expiring bucket
  covers the actual failure modes here (bad overwrite, accidental destroy) at no extra
  cost. AWS Backup adds a vault, a role, and a monthly charge for a 2.4 MB file.
- **Rely on `scripts/backup.py`** — rejected as the primary control. It is manual, and
  the two archives found locally are dated 2026-06-25 and 2026-08-12, i.e. run
  irregularly. Useful as a secondary offline copy, not as the recovery mechanism.
- **Lifecycle rule to expire old versions after N days** — deferred. At this file size
  unlimited version history costs cents. Revisit if the CSV grows past ~50 MB.

### Proposed change

`infrastructure/stack.py`:

```python
self.data_bucket = s3.Bucket(
    self, "DataBucket",
    encryption=s3.BucketEncryption.S3_MANAGED,
    block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
    versioned=True,                          # NEW
    removal_policy=RemovalPolicy.RETAIN,     # CHANGED from DESTROY
    # auto_delete_objects removed — incompatible with RETAIN
)
```

`deploy.sh`: extend the `--destroy` confirmation to print which buckets are retained
versus deleted, and require typing `destroy` rather than `yes`.

### Dual-audience consideration

`RETAIN` is unambiguously right for a deployment holding 243 paid-for reports. It is
less obviously right for someone evaluating the project, because the README advertises
`./deploy.sh --destroy` as "Tear down the entire stack / Remove all resources" — and
after this change it would silently leave a bucket behind.

Rejected: making retention a config knob (`retain_data`, defaulting to `false`). That
gives cloners the unsafe default and reproduces the audit finding for everyone but you.

**Chosen instead**: keep `RETAIN` as the behaviour for all deployments, and make
`--destroy` handle the consequence explicitly. After `cdk destroy` completes, the script
reports that the data bucket was retained, shows its name and object count, and offers to
empty and delete it behind a **second, separate confirmation**. The destructive action
stays available and honest to the README's promise, but requires stated intent rather
than being a side effect. Same code path for both audiences, no knob.

### Consequences to accept

- Anyone who declines the second prompt has an orphaned bucket; redeploying into the same
  account then needs it imported or removed by hand. The script should say so at the time
  rather than leaving it to be discovered.
- CloudFormation cannot change `removal_policy` on an existing resource without
  replacement risk. Needs verification in the diff that CDK reports this as a
  metadata-only update, not a bucket replacement. **If the diff shows replacement,
  stop and reassess** — that would destroy the data it is meant to protect.

### Verification

1. `cdk diff` reviewed and confirmed to contain no `Replacement: True` on the bucket.
2. Post-deploy: `GetBucketVersioning` returns `Enabled`.
3. Overwrite `links.txt` with a test value, confirm two versions exist via
   `ListObjectVersions`, restore the prior version, confirm content matches.
4. `./deploy.sh --destroy` dry-run to confirm the new prompt text, cancelled at the prompt.

### Open questions

None. Proceeds on approval.

---

## Item 2 — Wire alarms and budget to SNS

**Status**: TODO (D1 optional) · **Severity**: Critical · **Covers**: audit Critical #3
· **Depends on**: Item 0

### Evidence

Six `cloudwatch.Alarm` constructs exist (`Lambda1-Errors`, `Lambda1-Timeout`,
`Lambda1-Duration`, `Lambda2-Errors`, `Lambda2-Timeout`, `CloudFront-HighRequestVolume`).
Grep for `alarm_actions` and `add_alarm_action` across `infrastructure/stack.py`:
zero matches. Each alarm transitions to ALARM and notifies nobody.

`budgets.CfnBudget` is declared with a `BudgetDataProperty` only. Without
`notifications_with_subscribers`, AWS Budgets sends nothing — the "$20/day catches
DDoS cost spikes" comment in the stack describes an effect that does not occur.

The `monthly-analytics-rollup` spec independently defines a `Notification_Target`
requiring "at least one subscribed site-owner endpoint so that an ALARM transition
leaves the account", which corroborates this as a known gap.

### Decision

One SNS topic, `ai-radar-alerts`, attached as `alarm_actions` to all six alarms and as an
`ACTUAL` 100%-threshold budget notification.

**The topic and all wiring are created unconditionally.** The email subscription is added
only when `alert_email` is present in local config. This satisfies rule 1 of the
repository model — absent configuration degrades rather than fails — and it is a *better*
fix than gating the whole thing on an address:

- A fresh clone gets alarms genuinely wired to a topic, visible in the console, needing
  one manual subscription. The audit finding is fixed for every deployment, not just
  this one.
- No email address ever enters a committed file.
- `deploy.sh` prints a warning when no subscription exists, so the gap is visible rather
  than silent.

D1 therefore stops being a blocker. It becomes "what do you put in your own gitignored
`cdk.context.json`", which needs no plan-level decision.

### Alternatives considered

- **Separate topics per severity** — rejected as premature. Six alarms on a site that
  runs once daily does not justify routing tiers.
- **Chatbot/Slack integration** — rejected for now. Email is zero-config and needs no
  workspace permissions. Revisit if alarm volume becomes noisy.
- **`OK` action notifications as well as `ALARM`** — rejected. Daily-cadence alarms
  with `evaluation_periods=1` would generate recovery mail on every transient blip.

### Proposed change

```python
self.alert_topic = sns.Topic(self, "AlertTopic", topic_name="ai-radar-alerts",
                             display_name="AI Radar AWS Alerts")
self.alert_topic.add_subscription(subscriptions.EmailSubscription(alert_email))

for alarm in (self.lambda1_errors_alarm, self.lambda1_timeout_alarm,
              self.lambda1_duration_alarm, self.lambda2_errors_alarm,
              self.lambda2_timeout_alarm, self.cloudfront_requests_alarm):
    alarm.add_alarm_action(cw_actions.SnsAction(self.alert_topic))
```

Budget gains:

```python
notifications_with_subscribers=[
    budgets.CfnBudget.NotificationWithSubscribersProperty(
        notification=budgets.CfnBudget.NotificationProperty(
            comparison_operator="GREATER_THAN", notification_type="ACTUAL",
            threshold=100, threshold_type="PERCENTAGE"),
        subscribers=[budgets.CfnBudget.SubscriberProperty(
            subscription_type="EMAIL", address=alert_email)],
    )
]
```

`alert_email` read from CDK context (`self.node.try_get_context("alert_email")`), set
in the gitignored `cdk.context.json` created in Item 9 — not committed.

### Verification

1. Confirm the SNS subscription email arrives and is confirmed by clicking the link.
2. `aws cloudwatch set-alarm-state --alarm-name Lambda2-Errors --state-value ALARM
   --state-reason "remediation item 2 test"` — confirm mail received, then reset to
   `INSUFFICIENT_DATA`.
3. Budget notification cannot be tested without spend; verify via
   `DescribeNotificationsForBudget` that one notification with one subscriber exists.

### Open questions

**D1 — which email address?** A distribution list is preferable to a personal address
if anyone else may need to respond. Cannot proceed without this.

---

# Phase 1 — Data integrity

## Item 3 — Fix the links-index write path

**Status**: TODO · **Severity**: Critical · **Covers**: audit Critical #1, High #4

### Evidence

Three defects in `src/pipeline/storage_manager.py`.

**3a — blanket exception catch.** `_append_link`:

```python
try:
    response = self._s3.get_object(Bucket=self._data_bucket, Key=LINKS_KEY)
    existing = response["Body"].read().decode("utf-8")
except (self._s3.exceptions.NoSuchKey, Exception):   # <-- catches everything
    existing = ""
```

`Exception` in that tuple makes the `NoSuchKey` entry redundant and swallows every
failure mode. A throttle, timeout, or 5xx on `get_object` yields `existing = ""`, and
the `put_object` two lines below replaces the whole index with one URL.

Deduplication reads only this file. `load_existing_links` falls back to the CSV solely
when the key is **absent**, not when it is implausibly short, so there is no recovery.
Consequence of one swallowed error: the next run treats the entire 100-item feed as
new, incurs a full run of Bedrock spend across three models, and appends duplicate
rows for every item still inside the feed window.

**3b — non-atomic two-file write.** `save_announcement` uploads the CSV, then the link
index. A crash between the two leaves the item in the CSV but not the index, so it is
reprocessed on the next run and duplicated.

**3c — retry re-appends a landed row.** The retry loop wraps both writes:

```python
for attempt in range(MAX_RETRIES + 1):
    try:
        ...
        self._upload_csv(ANNOUNCEMENTS_KEY, updated_content)   # succeeds
        self._append_link(announcement.link)                   # raises on put_object
        return True
    except Exception:
        ...  # retry re-runs the CSV append
```

Because 3a swallows read errors, `put_object` is the only raising path left in
`_append_link` — and when it raises, the CSV row is appended a second time.

### Decision

Narrow the catch to `NoSuchKey` only; reverse the write order so the link index is
written first; move the link write out of the CSV retry scope so a failure cannot
duplicate a row.

Write-order reasoning: the two writes cannot be made atomic without a transactional
store. Given that, choose the failure mode. Index-first means a crash between writes
loses an announcement — it is marked seen but never stored, so it is skipped
permanently and silently. CSV-first means a crash duplicates it. **Index-first is
preferred** because a duplicate corrupts the published site and the analytics slug
join, whereas a missed item is invisible and recoverable by clearing that one link.

To make the missed-item case non-silent, log at ERROR when the CSV write fails after
the index write succeeded, including the link, so it can be re-queued by hand.

### Alternatives considered

- **Derive dedup from the CSV, delete `links.txt` entirely** — rejected. The separate
  index exists specifically to fix an OOM (`7f20a0b`, "use lightweight links.txt for
  dedup"). Reverting reintroduces a solved problem.
- **Rebuild the index from the CSV on every run** — rejected as the primary path for
  the same memory reason, but worth adding as a repair path: see the sanity check below.
- **DynamoDB for the link index** — rejected as out of scope. Correct long-term answer
  and worth revisiting under Item 16, not a fix for this bug.
- **Conditional writes / optimistic concurrency via ETag** — deferred to Item 4, which
  removes concurrent runs entirely and is simpler.

### Proposed change

1. `_append_link`: `except self._s3.exceptions.NoSuchKey: existing = ""`. Let all other
   exceptions propagate.
2. `save_announcement`: write index first, then CSV, with independent error handling.
3. `load_existing_links`: if `links.txt` parses to fewer than 10 links while
   `announcements.csv` exists and is larger than 100 KB, treat the index as damaged,
   rebuild it from the CSV, and log at ERROR. Self-heals the failure this item fixes,
   for any index already damaged before the fix ships.

A stateful "line count must never shrink" guard was in the original proposal and was
**dropped in the simplicity pass**: it needs persisted state to compare against, and the
narrowed exception handling plus the rebuild check above already cover the realistic
failure.

### Verification

New tests in `tests/test_storage_manager.py`:

- `get_object` raising `ClientError(Throttling)` → `_append_link` raises, index unmodified.
- `get_object` raising `NoSuchKey` → index created with one link.
- `put_object` on the index failing → zero CSV rows appended.
- CSV write failing after a successful index write → ERROR logged naming the link,
  index retains the link.
- Damaged index (2 links) plus a large CSV → rebuild triggered, count matches CSV.
- Existing storage-manager and property tests still pass.

### Open questions

Confirm the index-first trade-off is the one you want. If you would rather never lose
an announcement and prefer to handle duplicates, say so and I will invert it and add a
duplicate-detection sweep instead.

---

## Item 4 — Serialise pipeline runs

**Status**: TODO · **Severity**: Medium · **Covers**: audit Medium #11

### Evidence

No `reserved_concurrent_executions` on any Lambda. `save_announcement` does
read-modify-write on the full CSV. Two concurrent pipeline invocations — the daily
EventBridge trigger plus a manual `./run-pipeline.sh`, which the README actively
encourages — interleave as: both read the CSV, both append their own row, both write.
Last writer wins; the other run's announcements are gone despite being logged as saved.

### Decision

`reserved_concurrent_executions=1` on the report pipeline Lambda.

### Alternatives considered

- **ETag conditional writes** — rejected as the primary fix. More code, and it converts
  a lost update into a retry storm without removing the underlying race.
- **Concurrency limit on the website builder too** — rejected. It is idempotent: it
  reads the CSV and regenerates everything. Concurrent builds waste effort but cannot
  corrupt state.
- **A lock object in S3** — rejected. Reserved concurrency is a platform-level guarantee
  and needs no cleanup logic for stale locks.

### Proposed change

One argument on `ReportPipelineLambda`. Note the side effect: a manual invocation while
the scheduled run is in flight now fails fast with a throttle error instead of silently
corrupting data. `run-pipeline.sh` should detect that response and print a clear message
rather than a raw AWS error.

### Verification

Invoke the pipeline twice in quick succession; confirm the second returns a throttling
error and that `run-pipeline.sh` reports it legibly.

---

## Item 5 — Fail loudly on CSV schema drift

**Status**: TODO · **Severity**: High · **Covers**: audit High #7

### Evidence

`_append_row_to_csv` reads field names from the existing file's header and passes them
to `csv.DictWriter`, which defaults to `extrasaction='raise'`:

```python
header_reader = csv.reader(io.StringIO(first_line))
existing_columns = next(header_reader)
writer = csv.DictWriter(output, fieldnames=existing_columns)
writer.writerow(row)   # ValueError if row has keys not in existing_columns
```

Adding a field to `ProcessedAnnouncement` therefore breaks **every** append with a bare
`ValueError: dict contains fields not in fieldnames`, until someone rewrites the S3
file. The orchestrator catches it, files it under stage `unknown` via
`_determine_failure_stage`, and the run reports every announcement as failed with no
indication that a migration is needed.

The comment says schema evolution was deliberately removed to avoid quote-doubling
corruption (`722eb25`). That decision is sound — the issue is only the diagnostics.

### Decision

Keep the no-auto-migration behaviour. Detect the mismatch before writing and raise a
`CsvSchemaMismatchError` naming the missing columns and the migration step.

### Alternatives considered

- **Re-enable schema evolution** — rejected. It caused the corruption incident this code
  was written to fix.
- **`extrasaction='ignore'`** — rejected, and actively dangerous: appends would succeed
  while silently discarding the new field's data on every row.
- **Auto-migrate on mismatch** — rejected for now. It is a whole-file rewrite of the
  source of truth triggered implicitly by a deploy. If wanted later it belongs in an
  explicit script, gated on Item 1's versioning being in place.

### Proposed change

New exception in `storage_manager.py`; compare `set(row.keys())` against
`set(existing_columns)` before constructing the writer; raise with a message naming
both the extra columns and the script to run. Add the migration script pattern to the
README scripts table if one does not already fit.

### Verification

Unit test: CSV whose header lacks a column present in the row dict → raises
`CsvSchemaMismatchError`, message contains the missing column name, no S3 write attempted.

---

# Phase 2 — User-visible correctness

## Item 6 — Slug collisions

**Status**: TODO — D4 resolved (no stubs, no alias map); D3 default adopted, knobs
below open to your tweaks · **Severity**: High · **Covers**: finding A

### Evidence — verified against live data 2026-08-13

`_slug_from_link` in `src/website_builder/builder.py`:

```python
def _slug_from_link(link: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", link)
    slug = slug.strip("-")
    if len(slug) > 80:
        slug = slug[:80].rstrip("-")
    return slug
```

The slug is derived from the **whole URL**, and the AWS boilerplate consumes most of
the budget:

```
https-aws-amazon-com-about-aws-whats-new-2026-05-      48 chars, identical for every item
                                                       ── leaves 32 chars to disambiguate
```

Measured against `database/links.txt` in the live data bucket:

| Metric | Value |
|---|---|
| Unique announcement links | 243 |
| Distinct slugs produced | 239 |
| Slugs already at the 80-char cap | **132 (55%)** |
| Colliding slugs | 2 |
| **Report pages currently unreachable** | **4** |

Both collisions are SageMaker Unified Studio clusters, where the service name alone
exhausts the 32-character budget:

```
2026/06/amazon-sagemaker-unified-studio/                              ┐ 2 links
2026/06/amazon-sagemaker-unified-studio-emr/                          ┘ 1 page lost

2026/07/amazon-sagemaker-unified-studio                               ┐
2026/07/amazon-sagemaker-unified-studio-git/                          │ 4 links
2026/07/amazon-sagemaker-unified-studio-import-existing-mwaa-...      │ 3 pages lost
2026/07/amazon-sagemaker-unified-studio-terraform/                    ┘
```

Impact is not cosmetic. Four index cards link to a report page containing a *different*
announcement's content — whichever row `build()` wrote last. And with 55% of slugs at
the cap, every new announcement sharing a service-name prefix is a coin flip.

This also breaks the analytics topic join. The `monthly-analytics-rollup` spec defines
`Ambiguous_Slug` as "a Report_Slug that matches more than one row of the
Announcement_Catalog, so page views for it cannot be attributed to a single
announcement's Topic_Tag set" — that condition is live for 6 links today.

### Decision — needs your sign-off (D3)

Derive the slug from the **last path segment** rather than the whole URL, and append a
short hash of the full link so uniqueness is structural rather than probabilistic.

```python
def _slug_from_link(link: str) -> str:
    tail = link.rstrip("/").rsplit("/", 1)[-1]
    base = re.sub(r"[^a-zA-Z0-9]+", "-", tail).strip("-").lower()[:SLUG_MAX_BASE]
    digest = hashlib.sha256(link.encode("utf-8")).hexdigest()[:SLUG_HASH_LEN]
    return f"{base}-{digest}" if base else digest
```

Produces `amazon-sagemaker-unified-studio-terraform-3f9a1c04` — shorter than today,
readable, and collision-free by construction.

### Decision table — the knobs you may want to change

| Knob | Proposed | Range considered | Notes |
|---|---|---|---|
| Source of the readable part | last path segment | whole URL / title / `pub_date` + segment | Whole URL is the current bug. Title-derived would decouple from AWS URL structure but changes if a title is ever edited. |
| `SLUG_MAX_BASE` | 150 | 80 – 200 | S3 keys allow 1024 bytes; 150 is comfortably safe. Longest current AWS segment is ~85 chars, so 150 truncates nothing today. |
| `SLUG_HASH_LEN` | 8 | 6 – 12 | 8 hex chars = 4.3 bn values. At 243 items, collision probability is ~7e-15. 6 would also be fine; 8 costs 2 characters for a wide margin. |
| Hash algorithm | sha256 truncated | md5 / sha1 / blake2s | Not security-sensitive; sha256 avoids any question. `hashlib` is stdlib either way. |
| Hash position | suffix | prefix | Suffix keeps URLs alphabetically groupable by service name. |
| Case | lowercased | preserve | AWS segments are already lowercase; explicit `.lower()` guards against future mixed case producing two slugs for one page on case-insensitive clients. |
| Separator before hash | `-` | `--` / `_` | `--` would make the hash visually separable and reversible-ish for tooling. Marginal. |

If you want a different shape entirely — e.g. `YYYY-MM/<segment>` directory nesting to
group by month, or dropping the hash and instead detecting collisions at build time and
appending `-2`, `-3` — say so. The collision-detection variant keeps URLs prettier but
makes a page's URL depend on CSV row order, which is fragile under reruns.

### Migration plan (D4)

Because 132 of 239 slugs change regardless of which variant is chosen, the rename is
unavoidable. Two consequences need handling.

**1. The website builder never deletes objects.** `_staged_upload` writes and copies but
issues no delete outside `_staging/`. So the 132 old pages remain live indefinitely,
serving stale content at URLs nothing links to. That is arguably worse than a 404 — a
previously shared link keeps showing an old version with no indication it is outdated.

**Resolution (simplicity pass): delete the orphaned pages, no redirect stubs.** Traffic
is low, the site's entry point is the index (which always links to current slugs), and
stubs would mean generating, deploying, tracking, and later deleting 132 extra objects
for the handful of old deep links that may exist. A 404 is an honest answer for a
renamed page on a news site. Meta-refresh stubs were the original proposal, rejected as
moving parts disproportionate to the audience they serve.

**2. Analytics `report_slug` history breaks at the cutover.** All existing
`report_click` events reference old slugs. The rollup spec's Topic_Metric joins page
views to announcements via the slug, so the series would show a discontinuity.

**Resolution (simplicity pass): record the cutover date; no alias map.** The map turned
out to be unnecessary on inspection: both the old and the new slug function are **pure
functions of the announcement link**, and every link is stored in the CSV permanently.
Old → new is therefore derivable at any time, by anyone, from data that already exists —
shipping a `slug_aliases.json` would persist a derivable artifact and create one more
file to keep in sync. If the rollup job ever needs to resolve pre-cutover slugs, it
computes the old slug from the link with ten lines of code. The original "cheap now,
expensive to reconstruct later" reasoning was simply wrong: reconstruction is free.

**Execution order** — deliberately split into two approvals:

- **6a** — change `_slug_from_link`, add tests including a zero-collision assertion over
  all 243 live links. No deploy. Reviewable in isolation.
- **6b** — deploy, rebuild the site, verify the four previously-colliding announcements
  each resolve to their own report, then delete the orphaned old-slug objects (one-time
  cleanup; the list is the diff of old vs. new slugs computed from the CSV links).
  Requires Item 1 done first.

### Verification

1. Property test: for any two distinct links, slugs differ (hypothesis, existing
   property-test conventions in `tests/test_property_website_builder.py`).
2. Regression test asserting zero collisions across a fixture of all 243 live links.
3. Test that a link whose last segment is empty (trailing-slash-only URL) still yields
   a usable slug.
4. Post-deploy: fetch all four previously-colliding URLs, assert each returns the
   report whose title matches its own card.
5. Confirm `reports/` object count equals unique link count after orphan deletion.

### Open questions

D4 resolved. D3 default adopted — the knobs table above remains open to your
adjustments until 6a is implemented.

---

## Item 7 — Relevance filter accuracy

**Status**: NEEDS DECISION (data) · **Severity**: Medium · **Covers**: audit Medium #9

### Evidence

`src/pipeline/relevance_filter.py`. Two distinct problems.

**7a — an exclusion pattern that can over-reach.** Matching runs against
`title + description[:200]`:

```python
r"\bconnect\b.*\bagent\b",
r"\bagent\b.*\bconnect\b",
```

`.*` spans the whole window, so any item mentioning both words anywhere in ~200
characters is dropped regardless of context. A plausible AgentCore connectivity
announcement would be silently excluded. Exclusions run before inclusions and cannot
be overridden.

**7b — inclusion patterns that are ordinary English.** `\bnova\b`, `\blex\b`,
`\bforecast\b`, `\btranslate\b`, `\bpersonalize\b`, `\bcomprehend\b`, `\bpolly\b`,
`\bagents\b` all match non-AI announcements. `\bforecast\b` in a cost-management item
and `\btranslate\b` in a localisation item would both pass.

### Decision

**Measure before changing anything.** Both risks are plausible from reading the
patterns, but neither is quantified, and the false-negative case (7a) is invisible by
construction — excluded items leave no trace in the CSV or the error file. Guessing at
thresholds here risks trading a small unmeasured problem for a larger one.

### Proposed approach

1. Build a fixture of recent AWS "What's New" items — the live feed returns ~100, and
   the 243 stored links give a known-relevant set to score against.
2. Write a throwaway analysis script (not committed to `scripts/`) reporting per pattern:
   items matched, items uniquely matched (no other pattern would have caught them), and
   for exclusions, which items were dropped and their titles.
3. Present a table of candidates to tighten, with the specific items each change would
   admit or reject.
4. Only then change patterns, with a test per change pinning the intended behaviour.

Expected shape of the fix, subject to the data: replace the two `connect.*agent`
patterns with a narrower `\bamazon connect\b` check plus a targeted exception for
AgentCore; add negative lookahead or require a co-occurring AI term for the
ambiguous single words.

### Verification

Extend `tests/test_relevance_filter.py` with a labelled corpus — one case per pattern
changed, asserting both directions (admitted when it should be, rejected when it
should not). The existing property test on word-boundary matching must still pass.

### Open questions

Needs the measurement run first. No code change proposed until you have seen the numbers.

---

## Item 8 — Research time budget

**Status**: TODO (D7 resolved) · **Severity**: Medium · **Covers**: audit Medium #8

### Evidence

`src/pipeline/research_agent.py`:

```python
def _has_sufficient_time(self) -> bool:
    remaining_ms = self._context.get_remaining_time_in_millis()
    required_ms = (self._config.research_timeout_per_announcement * 1000) + _SAFETY_MARGIN_MS
    return remaining_ms >= required_ms
```

With `research_timeout_per_announcement = 300` and a 30 s margin, this demands **330
seconds free** before researching a single announcement, even though a typical run
fetches a handful of URLs at 15 s timeout each. In a 15-minute Lambda, once ~10 minutes
are consumed every remaining announcement loses enrichment entirely.

The skip is recorded in the run summary as an aggregate `research_skipped` count, but
nothing is stored per announcement and nothing is shown on the site. A report generated
without research is indistinguishable from one generated with it.

There is also no enforcement of the 300 s budget once research begins — the value is
used only as a gate. Requirement 4.4 of the original spec allows "up to 5 minutes per
announcement", so the current code treats a ceiling as a reservation.

### Decision — resolved 2026-08-14, simplicity pass (D7)

Three candidate behaviours were considered:

- **A. Reserve realistically** — gate on a rolling estimate of observed research
  duration. **Dropped**: rolling statistics are state to maintain and behaviour that
  changes run to run.
- **B. Enforce the budget** — a wall-clock deadline inside `research()` so it returns
  whatever it has gathered when the per-item budget is exceeded, plus a much smaller
  **fixed** reservation in the gate (90 s instead of 330 s). The correct reading of
  Requirement 4.4 ("up to 5 minutes" is a ceiling, not a reservation), and the deadline
  is one `time.monotonic()` check per URL in the existing fetch loop.
- **C. Persist a per-item `research_skipped` flag to the CSV** — **dropped**: a schema
  change plus a migration, to duplicate information the run summary already logs. If
  thin reports become a real problem, revisit.

**Adopted: B alone.** Two constants and one loop guard; no state, no schema change, no
dependency on Item 5.

### Alternatives considered

- **Raise the Lambda timeout past 15 min** — not possible; 15 min is the hard limit.
- **Split research into its own Lambda / Step Function** — real answer at higher volume,
  out of scope here. Worth noting under Item 16 as the AI-news track will multiply item
  counts.
- **Drop the time gate entirely and rely on the Lambda timeout** — rejected. A hard
  timeout loses the whole run's un-flushed work rather than degrading gracefully.

### Verification

Unit tests with a mocked context returning declining remaining-time values: assert
research proceeds where it currently would not, and that an over-budget fetch returns
partial content rather than raising. Confirm `research_skipped` totals still appear in
the run summary.

---

# Phase 3 — Security

## Item 9 — Account identifiers out of the repo

**Status**: TODO (D2 resolved: no history rewrite) · **Severity**: High · **Covers**: audit Sec-High #1, Low #20

### Evidence

`cdk.json`, committed and present on `origin/main`:

```json
"custom_domain": "<SITE_DOMAIN>",
"certificate_arn": "arn:aws:acm:us-east-1:<ACCOUNT_ID>:certificate/<CERT_ID>",
"hosted_zone_id": "<ZONE_ID>"
```

Real values are present in the committed file and in git history; they are redacted here
because this document is itself published. See `docs/audit-evidence.local.md`.

`git remote -v` → `https://github.com/bbonik/ai-radar-aws.git`. The README presents this
as a public clone target and the project is MIT licensed, so the repository is intended
to be public.

AWS does not classify an account ID as a credential, and it cannot be used alone to
access anything. It is nonetheless a reconnaissance primitive: it enables targeted
`sts:AssumeRole` probing against guessable role names, S3 bucket-name enumeration, and
credible spear-phishing. The hosted zone ID and certificate ARN together disclose the
DNS and TLS topology, and the domain leaks an internal-looking `*.people.aws.dev` host.

### Decision

Apply the layering established by Item 0: remove all three from `cdk.json`, move them to
the gitignored `cdk.context.json`, keep `try_get_context` so absent values fall back to
the plain CloudFront URL. `alert_email` joins them in the same file.

Item 0 now owns the *mechanism*; this item is reduced to **purging the existing values**
and deciding what to do about history (D2). Scope note: this item is no longer a
prerequisite for anything, since Item 0 took that role — but it must land before the
branch is pushed to a public remote.

### Alternatives considered

- **SSM Parameter Store / Secrets Manager** — rejected as heavier than needed. These are
  deployment-time configuration values, not runtime secrets, and CDK context is the
  idiomatic place for them. Also avoids a lookup that would require credentials during
  `cdk synth`.
- **Environment variables** — workable but easier to forget; a file is self-documenting
  and CDK reads it without wrapper scripts.
- **Leave as-is because an account ID is not secret** — rejected. Defensible in
  isolation, but there is no upside to publishing it, and the certificate ARN plus zone
  ID add topology disclosure with no benefit.

### The history question (D2)

Removing the values from `cdk.json` does **not** unpublish them; they remain in every
commit that touched the file, on a public remote. Two options:

- **Accept the exposure.** Remove from HEAD, do not rewrite history. The account ID
  stays discoverable in the git log. Mitigate by ensuring no role names are guessable
  and no bucket relies on name secrecy — both already true here. Zero disruption.
- **Rewrite history.** `git filter-repo` to purge the strings, then force-push `main`.
  Invalidates every existing clone and fork, breaks any commit SHAs referenced
  elsewhere, and requires anyone with a checkout to re-clone. Given the account ID is
  the main item and it is not a credential, this is likely disproportionate.

**I will not rewrite history without an explicit instruction.** My recommendation is the
first option, plus rotating the ACM certificate if you consider its ARN sensitive
(cheap: request a new one, update context, delete the old).

### Verification

1. `git grep <ACCOUNT_ID>` and `git grep <ZONE_ID>` return nothing in the working tree
   (history is a separate question, D2). Add this grep to the Item 19 CI workflow as a
   standing check so a value cannot be reintroduced by a future commit.
2. `cdk synth` with `cdk.context.json` present → template contains the custom domain and
   certificate.
3. `cdk synth` with the file absent → template falls back to the CloudFront domain and
   creates no Route 53 record. This is the path a third-party cloner takes and is
   currently untested.

---

## Item 10 — Stop bundling local files into Lambda packages

**Status**: TODO · **Severity**: High · **Covers**: audit Sec-High #2

### Evidence

All three Lambdas use `lambda_.Code.from_asset(".")` with this exclude list:

```python
[".git/*", ".hypothesis/*", ".kiro/*", ".pytest_cache/*", "tests/*",
 "infrastructure/*", "cdk.out/**", "node_modules/*", "__pycache__/*",
 "docs/*", "scripts/*", "*.pyc", ".venv/*"]
```

No entry for `*.zip`, `.env`, `*.pem`, or `backups/`. Present in the working tree right now:

```
ai-radar-backup-2026-06-25.zip          416 KB   (repo root)
backups/ai-radar-backup-2026-08-12.zip  2.5 MB
```

Both are bundled into all three deployment packages, including the internet-facing
analytics function. `scripts/backup.py` defaults `--output` to `.`, so the documented
backup workflow writes archives exactly where CDK collects them. `.gitignore` covers
them for git, but CDK asset bundling does not read `.gitignore`.

Confidentiality impact is limited — the archives contain published website data — but
anyone with `lambda:GetFunction` can download the package via a presigned URL, and the
same gap ships any `.env`, key file, or credentials file left in the root.

### Decision

Extract the exclude list to a module-level constant (it is currently duplicated three
times, which is how the drift happened) and extend it with deny entries for archives,
secrets, and backup directories.

### Alternatives considered

- **Switch to an allowlist via `bundling`** — the structurally correct answer: bundle
  only `src/` plus `images/` and nothing else. Rejected for now because it changes the
  packaging mechanism for all three functions and risks omitting something at runtime.
  Recorded as a follow-up worth doing once the deny list is in place.
- **Change `backup.py` default output to `~/backups`** — worth doing as well, but it
  does not protect against the general case. Both.

### Proposed change

```python
LAMBDA_ASSET_EXCLUDES = [
    ".git/*", ".hypothesis/*", ".kiro/*", ".pytest_cache/*", ".venv/*",
    "tests/*", "infrastructure/*", "scripts/*", "docs/*",
    "cdk.out/**", "node_modules/*", "__pycache__/*", "*.pyc",
    # added — never ship local artefacts or secrets
    "*.zip", "backups/*", ".env", ".env.*", "*.pem", "*.key",
    "*.pptx", "cdk.context.json", "*.csv",
]
```

`*.csv` included because a downloaded copy of the announcements file in the root would
otherwise ship. Verify no runtime code reads a CSV from the package (it reads from S3).

Also change `backup.py`'s default `--output` to `./backups` and confirm that directory
is both gitignored and asset-excluded.

### Verification

1. `cdk synth`, then inspect the asset staging directory under `cdk.out/` and confirm no
   `.zip`, `.csv`, or `backups/` entries.
2. Compare asset size before and after; expect a ~2.9 MB reduction per function.
3. Deploy and run the pipeline end to end — confirms nothing required was excluded.

---

## Item 11 — Tighten CSP and Mermaid

**Status**: TODO · **Severity**: Medium · **Covers**: audit Medium #12, Sec-Med #4/#5, Sec-Low #10

### Evidence

Current policy in `infrastructure/stack.py`:

```
default-src 'self';
script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com;
style-src 'self' 'unsafe-inline';
img-src 'self' data:;
connect-src 'self' https://*.execute-api.us-east-1.amazonaws.com
```

**11a — `connect-src` wildcard.** `https://*.execute-api.us-east-1.amazonaws.com`
authorises **any AWS customer's** API Gateway in us-east-1. If script execution is ever
achieved on the page, CSP places no meaningful restriction on the exfiltration
destination. The stack already computes `analytics_api_url` and can interpolate the
specific API ID.

**11b — stale CDN grant.** `cdnjs.cloudflare.com` was added for html2pdf.js (changelog
B1). Commit `aeb6682` replaced PDF export with browser-native print. Grep confirms no
`cdnjs` reference in the generated HTML. The allowance now serves nothing.

**11c — `unsafe-inline` / `unsafe-eval`.** Genuinely required: Mermaid and Chart.js are
initialised inline, and Mermaid evaluates dynamically. Not a defect, but it means CSP
provides little defence behind the HTML escaping, and nothing in the code records that
this is deliberate.

**11d — Mermaid `securityLevel` unset.** `mermaid.initialize({ startOnLoad: true, theme:
'neutral' })`. Mermaid 10 defaults to `securityLevel: 'strict'`, which sanitises label
HTML — so this is safe today. But the rendered content is LLM-generated, and the
protection depends on an upstream default rather than an explicit setting.

### Credit where due

Both CDN scripts already use SRI with pinned versions:

```html
<script src="...chart.js@4.5.1/..." integrity="sha384-jb8JQ..." crossorigin="anonymous">
<script src="...mermaid@10.9.6/..." integrity="sha384-qX9Vv..." crossorigin="anonymous">
```

That is the control that actually matters for third-party JS and is frequently skipped.
Any version bump must regenerate the hashes — worth a comment near the constants.

### Decision

Pin `connect-src` to the real API endpoint; drop `cdnjs.cloudflare.com`; set
`securityLevel: 'strict'` explicitly; add a comment explaining why `unsafe-*` remain and
what would have to change to remove them (extract inline init to a served `.js` file,
then a nonce or hash-based policy).

### Alternatives considered

- **Remove `unsafe-inline` now via nonces** — rejected in this item. CloudFront cannot
  inject a per-response nonce without a Lambda@Edge or CloudFront Function, which is a
  meaningful addition. Achievable by moving all inline script into `assets/app.js` and
  using hashes; recorded as a follow-up rather than bundled here.
- **Self-host Mermaid and Chart.js** — would let `script-src` drop to `'self'`. Rejected
  for now: loses CDN caching and adds a vendoring step, for a benefit already largely
  covered by SRI.
- **Add `report-uri` / `report-to`** — worth having, but needs an endpoint to receive
  reports. Deferred.

### Verification

CSP changes break pages silently, so verification is explicit. After deploy, on both
`index.html` and a report page, with the browser console open and zero CSP violations:

1. Mermaid diagram renders on a 2★+ report.
2. Timeline chart renders and updates when a filter is applied.
3. Analytics POST to `/events` succeeds (Network tab, 200).
4. Print-to-PDF produces correct output.
5. Confirm the response headers on a live request match the intended policy.

---

## Item 12 — Analytics: throttle and de-identify (WAF deferred)

**Status**: TODO (D5, D6 resolved) · **Severity**: Medium (downgraded from High in the
simplicity pass) · **Covers**: audit Medium #10, Sec-High #3 (partially, by accepted
risk), Sec-Med #7

### Evidence

**12a — no WAF on the only public write path.** The Web ACL is declared
`scope="CLOUDFRONT"` and attached to the distribution via `web_acl_id`. The analytics
HTTP API is a separate `apigwv2.CfnApi` with no ACL association. Its only protection is
stage throttling (`throttling_rate_limit=50`, `burst=100`). CORS on the API restricts
origins, but CORS is browser-enforced and bypassed trivially with curl — and
`src/analytics/handler.py` performs no server-side origin check. At 50 rps sustained
that is ~4.3 M objects/day into the logs bucket, each also inflating Athena scan cost.

**12b — client IPs retained 90 days.** The handler stores `source_ip` and `user_agent`
per event. CloudFront access logs store `c_ip`, and `unique_visitors_cf` is
`COUNT(DISTINCT c_ip)`. The site has no privacy notice or consent mechanism. The
`monthly-analytics-rollup` spec is explicit that rollups must hold aggregates only
precisely so that "unlimited rollup history must not become unlimited PII retention" —
the raw tier is where the exposure remains.

**12c — minor.** The handler returns `Access-Control-Allow-Origin: *`, contradicting the
origin-restricted CORS configuration on the API.

### Decision — resolved 2026-08-14, simplicity pass (D5, D6)

**D5 — protecting the endpoint: deferred.** Both options add standing infrastructure —
a second CloudFront origin plus path behaviour, or a second Web ACL at ~$5/month plus
per-rule charges (~30% on the current ~$18 estimate) — to defend a pet project's
telemetry endpoint against an attack that has never occurred. The simple mitigations
bound the damage instead:

- Tighten stage throttling from 50 rps / 100 burst to **5 rps / 20 burst** — an order of
  magnitude, still far above legitimate traffic for this site.
- The Item 2 budget alarm is the cost backstop; the 90-day lifecycle rule bounds storage.
- Fix the handler's `Access-Control-Allow-Origin: *` to echo only the configured origins.

Worst case becomes bounded and observable rather than prevented — an explicit accepted
risk. If the budget alarm or the CloudFront request alarm ever fires on real abuse,
revisit with the CloudFront-second-origin option, which was judged the better of the two
(reuses the existing ACL, makes the endpoint same-origin, collapses the CORS question).

**D6 — de-identification: adopted.** Truncate at ingest: IPv4 to /24, IPv6 to /48,
before the event is written — about five lines in the handler. Keeps geographic and
uniqueness signal at street-block granularity while ceasing to store a device
identifier.

Consequence to accept: `unique_visitors_cf` becomes distinct-truncated-prefix rather
than distinct-IP, so it undercounts multiple visitors behind one /24 and the metric is
not comparable across the cutover. That must be recorded in the README analytics
section and in the rollup design, which already carries a `Derivation_Label` concept
suited to expressing it.

Note the CloudFront access logs are written by CloudFront, not by our code, so `c_ip`
truncation cannot happen at ingest for that source — it would have to happen in the
Athena query layer or via a shorter lifecycle on `cloudfront/`. Worth deciding
separately; the custom-event path is fixable directly.

### Alternatives considered

- **API key or signed request from the browser** — rejected. Any credential shipped to a
  static page is public by definition; it adds friction without adding protection.
- **Drop `source_ip` entirely** — viable and simplest, but loses the ability to
  distinguish visitors at all in the custom-event stream. `session_id` is
  client-supplied and trivially forged, so it is not a substitute.
- **Cognito / auth on the endpoint** — rejected as disproportionate for anonymous
  pageview telemetry on a public site.

### Verification

1. Confirm the new throttle limits are live (burst of curl requests sees 429s at the
   expected point) and the handler echoes only configured origins.
2. Confirm a stored JSONL record contains a truncated IP, not a full address.
3. Confirm the site's own tracking still records events end to end (one object appears
   under `events/<date>/`).
4. Re-run `scripts/analytics_report.py --days 7` and confirm it still produces output
   against the modified field.

### Open questions

D5 and D6 resolved. Still open, not blocking: do you want a privacy notice on the site?
Not legally assessed here — flagging that EU visitors plus IP retention is the
combination that usually triggers a consent requirement, and that D6 materially reduces
the exposure either way.

---

## Item 13 — Harden outbound fetching

**Status**: TODO · **Severity**: High · **Covers**: audit High #5, Sec-Med #8

### Evidence

`src/pipeline/research_agent.py`:

```python
with urlopen(request, timeout=_URL_FETCH_TIMEOUT) as response:
    ...
    raw_bytes = response.read()          # no size limit
```

- No cap on response size — one large page inflates both the decoded HTML string and
  `_TextExtractor._text_parts` simultaneously.
- No cap on URL count per announcement: `_extract_urls` returns every URL found in the
  description plus the item's own link, each with a 15 s timeout.
- No redirect limit — `urlopen` follows redirects by default, so a short URL can lead to
  an arbitrarily large resource.
- Scheme is filtered to `http(s)` in `_extract_urls_from_text`, which is correct and
  worth keeping. Note IMDS is not reachable from Lambda, so credential theft via SSRF is
  not the risk here.

Practical risk today is **resource exhaustion** and using the function as an outbound
request amplifier. The input is currently the AWS feed, so exposure is low. That changes
materially with `multi-source-ai-news`, which seeds `https://hnrss.org/newest?q=AI` —
user-submitted URLs flowing into this exact code path.

### Decision

Fix this **before** the multi-source spec lands, not after. Add four bounds:
per-response size cap, per-item URL count cap, redirect limit, and https-only
enforcement (downgrading the current http-or-https allowance).

### Alternatives considered

- **Domain allowlist** — rejected for the AWS track: announcements legitimately link to
  arbitrary partner and documentation domains, and an allowlist would silently degrade
  research quality. Reconsider per-source for the AI-news track, where feeds are known.
- **Move research to a sandboxed function with no other permissions** — sound defence in
  depth, out of scope here. Worth noting alongside Item 16.
- **Switch to `requests` with `stream=True`** — rejected: adds a dependency to a
  Lambda that currently needs only boto3, for something `urlopen` can do with a bounded
  `read(n)`.

### Proposed change

New constants in the module, with values open to your adjustment:

| Bound | Proposed | Rationale |
|---|---|---|
| `_MAX_RESPONSE_BYTES` | 2 MB | Report generator truncates page text to 3000 chars anyway; 2 MB of HTML is far more than needed to reach that. |
| `_MAX_URLS_PER_ITEM` | 8 | Observed announcements link to a handful. At 15 s each, 8 caps a single item at ~2 min. |
| scheme | https only | AWS links are https. Removes cleartext fetches. |

A redirect cap was in the original proposal and was **dropped in the simplicity pass**:
it requires a custom `HTTPRedirectHandler` subclass on a per-call opener, `urlopen`
already errors on redirect loops by default, and the size cap bounds what any redirect
target can cost. Marginal benefit, real moving part.

Implementation: `response.read(_MAX_RESPONSE_BYTES + 1)` and treat an over-length result
as a truncation (log a warning, use the first `_MAX_RESPONSE_BYTES`) rather than an
error — partial page text is still useful.

### Verification

Unit tests with a mocked `urlopen`: an oversized body is truncated and logged, not
raised; a 9-URL description fetches 8; an `http://` URL is skipped. Existing
`tests/test_property_research_agent.py` must still pass.

---

## Item 14 — Scope IAM down

**Status**: DEFERRED (simplicity pass) · **Severity**: Medium · **Covers**: audit Sec-Med #9

### Evidence

```python
self.data_bucket.grant_read_write(self.report_pipeline_lambda)
```

`grant_read_write` produces `s3:GetObject*`, `s3:PutObject*`, `s3:DeleteObject*`,
`s3:Abort*`, plus `s3:List*` on the bucket, across **all** keys. The pipeline only ever
reads and writes `database/announcements.csv`, `database/links.txt`, and
`errors/failed_announcements.csv`. It never deletes.

Consequence: a bug in the storage manager, or a compromised dependency, can delete the
announcement catalogue. Item 1's versioning makes that recoverable, but the permission
is not needed in the first place.

### Decision — DEFERRED in the simplicity pass

The risk/benefit inverted on re-review. Item 1's versioning already makes accidental
deletion recoverable, while the scoping change itself carries a real chance of subtle
breakage: `s3:ListBucket` interacts with S3's error semantics, and losing it silently
turns `NoSuchKey` (handled in `load_existing_links`) into `AccessDenied` (unhandled).
Prevention that risks breaking the pipeline, guarding against a scenario recovery
already covers, is a poor trade for this project.

Explicitly accepted risk: the pipeline role retains `s3:DeleteObject` on the data
bucket. If implemented later, the original proposal below stands.

Original decision: replace with prefix-scoped grants — read/write on `database/*` and
`errors/*`, no delete.

### Alternatives considered

- **Leave as-is now that versioning exists** — originally rejected ("versioning is
  recovery; least privilege is prevention; both are cheap"), now **adopted**: the change
  is cheap, but its verification burden and failure mode are not.
- **Also scope the website builder's `grant_read_write` on the website bucket** — the
  builder genuinely needs delete for staging cleanup (`delete_objects` on `_staging/`)
  and, after Item 6, for removing stale report pages. Leave it, but confirm the staging
  cleanup path still works after any change.

### Proposed change

```python
self.data_bucket.grant_read(self.report_pipeline_lambda, "database/*")
self.data_bucket.grant_put(self.report_pipeline_lambda, "database/*")
self.data_bucket.grant_read(self.report_pipeline_lambda, "errors/*")
self.data_bucket.grant_put(self.report_pipeline_lambda, "errors/*")
```

`grant_put` omits delete. Note `s3:ListBucket` is still required for the bucket itself —
verify the generated policy retains it, or the `NoSuchKey` paths in
`load_existing_links` may surface as `AccessDenied` instead, which would change error
handling behaviour.

### Verification

1. `cdk diff` — confirm no `s3:DeleteObject` on the data bucket for the pipeline role.
2. Run the full pipeline post-deploy. This is the item most likely to break something
   subtly, so verify all three writes occur: a new CSV row, an updated `links.txt`, and
   (by forcing a failure) an error-file append.
3. Confirm `load_existing_links` still distinguishes "missing key" from "access denied".

---

# Phase 4 — Robustness

## Item 15 — Failure visibility (stage variable only)

**Status**: TODO · **Severity**: Low (downgraded — DLQ dropped) · **Covers**: audit
Medium #13 (by accepted risk), Low #14

### Evidence

**15a — no DLQ on the async invocation.** `_invoke_website_builder` uses
`InvocationType="Event"`. Lambda retries an async invocation twice on failure and then
discards it. No `dead_letter_queue` or `on_failure` destination is configured on the
website builder. A failed build leaves the site stale with no signal beyond the
`Lambda2-Errors` alarm — which, until Item 2, notifies nobody.

**15b — stage attribution by string matching.** `_determine_failure_stage`:

```python
if "report" in exc_msg or "generation" in exc_msg: return "report_generation"
if "graph" in exc_msg or "mermaid" in exc_msg:     return "graph_generation"
if "s3" in exc_msg or "storage" in exc_msg:        return "storage"
if "research" in exc_msg or "fetch" in exc_msg:    return "research"
return "unknown"
```

Guesses the failed stage from the exception message. A Bedrock error whose message
happens to contain a URL is filed under `research`; a storage error mentioning a report
title is filed under `report_generation`. The error CSV's `stage` column — the field an
operator would filter on — is therefore unreliable. The orchestrator already knows
which stage it is executing.

### Decision — trimmed in the simplicity pass

**DLQ dropped.** Once Item 2 lands, the existing `Lambda2-Errors` alarm already notifies
on a failed build — which is the entire value here, because the async payload is just a
`run_id` and the build is idempotent from CSV: there is nothing worth retaining or
replaying. An SQS queue plus a depth alarm would be two more resources delivering the
same email one step later. The design doc already accepts a stale site until the next
daily run; with Item 2, that acceptance stops being silent, which closes the actual gap.

**Kept: the stage variable.** Replace the string matching with an explicit `stage`
variable set as `_process_announcement` progresses, and delete
`_determine_failure_stage`. A net deletion of code that also fixes the error CSV's
reliability.

### Alternatives considered

- **SQS DLQ with depth alarm** — the original proposal; dropped as above.
- **`on_failure` destination to SNS** — same reasoning; the Item 2 alarm already covers
  notification.
- **Retry the website build from within the pipeline** — rejected. Idempotent daily
  build; a stale site for one day is acceptable and now visible.

### Proposed change

In `orchestrator._process_announcement`, replace the guess with a local `stage` variable
updated before each call, used in both the `except` paths. Delete
`_determine_failure_stage`. Stage values become a closed set (small enum or module
constants) so the error CSV's `stage` column is reliable.

### Verification

Unit test forcing a failure at each stage and asserting the recorded `stage` matches.

---

## Item 16 — CSV storage growth

**Status**: TODO (D8 resolved: document only) · **Severity**: Medium · **Covers**: audit High #6, revised

### Correction to the original finding

I initially rated this High based on commit messages describing a 34 MB CSV and repeated
OOM incidents (`f7ff1b5`, `de046ce`, `722eb25`). The live file measured
**2.4 MB** at 243 announcements. Either the 34 MB figure reflected the corruption
incident that `722eb25` fixed, or the `drop_aws_service_column` migration shrank it
substantially. At current size the per-item full-file rewrite is not a live problem.
**Revised to Medium.**

### Evidence

`save_announcement` downloads the entire CSV, appends one row, and re-uploads, once per
announcement. For a run of *n* new items the run transfers O(n²) bytes. At 2.4 MB and
~16 items/week this is negligible. The Lambda is at 1024 MB memory following the earlier
OOM work.

`multi-source-ai-news` requirement 10.7 asks the design to "document the growth
characteristics and the item volume at which that approach must change", and its
Run_Item_Cap implies materially higher per-run volume than the AWS track.

### Decision — needs your input (D8)

Recommend **documenting the limit now and deferring the migration** until the AI-news
track is being built, since that spec mandates a separate data file anyway and is the
natural point to choose a better format for both.

Options to document, with rough thresholds to be computed properly during the write-up:

- **Status quo, per-item full rewrite.** Simple, atomic per write, no format change.
  Becomes uncomfortable somewhere around 20–50 MB or ~30 items per run, depending on
  memory headroom.
- **JSONL append.** Retains one file, removes the read-modify-write entirely, needs a
  reader change in the website builder and the analytics scripts. No schema-header
  coupling, so it also dissolves Item 5's problem.
- **One object per announcement plus a manifest.** Best scaling and cheapest per write;
  largest change, since the builder, all seven backfill scripts, and `backup.py` all
  read the single CSV today.

### Deliverable

A section added to this document (or a separate `docs/storage-growth.md` if it grows
large) covering: measured current size and per-announcement average, the transfer volume
formula, the memory ceiling, the volume at which each option becomes necessary, and a
recommendation for the AI-news track's own file. No code changes under this item.

### Open questions

D8. Also worth deciding whether the AI-news track should adopt the target format from
the outset rather than copying the CSV approach and migrating both later.

---

# Phase 5 — Cleanup

## Item 17 — Dead code and doc drift

**Status**: TODO · **Severity**: Low · **Covers**: audit Low #15–#19

Individually trivial; grouped into one commit. Each is independently revertible.

### 17a — Dead code

| Location | Item | Action |
|---|---|---|
| `src/pipeline/tagger.py` | `_validate_geo` — superseded by `_validate_geo_list` | Remove |
| `src/pipeline/importance_classifier.py` | `_has_blogpost_links` — superseded by `_compute_link_score` | Remove |
| `src/pipeline/rss_fetcher.py` | `from urllib.error import URLError` — unused | Remove |
| `src/pipeline/research_agent.py` | `from urllib.error import URLError` — unused | Remove |

Check the test suite for direct references before removing — a test may exercise
`_has_blogpost_links` even though production code no longer calls it.

### 17b — Competing geo-relevance implementations

Two functions compute geographic relevance with different return contracts:

- `orchestrator._resolve_geo_relevance` → comma-separated multi-geo (`"apj,emea"`).
  This one feeds the badges and the Geography filter dimension.
- `ImportanceClassifier.compute_geo_relevance` → `"local"` / `"global"` / `""`.
  Not called by the orchestrator; `scripts/compute_geo_relevance.py` should be checked
  for which it uses.

Having both invites editing the wrong one. Decision: establish which is authoritative,
then either delete the other or rename it to state its scope (e.g.
`_scoring_geo_signal`). Needs a read of the backfill script before choosing — noted as a
small open question rather than assumed.

### 17c — Stale comments and docstrings

| Location | Says | Actual |
|---|---|---|
| `models.ProcessedAnnouncement.importance_level` | "1, 2, or 3" | 1–5 |
| `graph_generator` module docstring | "3-star and above" | gates at `importance_level < 2` |
| `ImportanceClassifier` class docstring | lists QuickSight as high tier | `HIGH_TIER_TAGS` omits it; `quicksight` is in `MEDIUM_TIER_TAGS` |
| `models.Report` | six sections | seven, `card_summary` added |
| `design.md` | 1–3 stars throughout, `aws_service` column | 1–5 stars; column retired in `1df4d6c` |

The `design.md` drift is larger than a comment fix. Proposal: leave the spec as the
historical record of the original design and add a short "Deviations since
implementation" section at its end rather than rewriting it. Rewriting a spec to match
the code loses the record of what was intended.

### 17d — `deploy.sh` hides test failures

```bash
python -m pytest tests/ -q --tb=short 2>&1 | tail -5
```

`set -euo pipefail` does correctly abort on failure — but only five lines survive, so
the actual assertion is usually cut off. Change to write full output to a temp file,
print the tail on success, and print the whole thing on failure.

### 17e — `mypy` documented but unconfigured

README lists `mypy src/` under Development. No `mypy.ini`, `setup.cfg`, or
`pyproject.toml` exists, so it runs with defaults and produces noise rather than signal.

Simplicity pass: rather than adding a config file to justify a README line, **remove the
`mypy src/` line from the README**. Nobody runs it, nothing enforces it, and a
type-checking regime should be adopted deliberately or not at all. If a contributor
later wants it, that PR brings the config.

### Verification

Full test suite green. `./deploy.sh` with a deliberately broken test shows the real
failure. README no longer documents commands with nothing behind them.

---

# Phase 3 (cont.) — Generalisation

These items exist solely because of the dual-audience requirement. They do not fix
security or correctness defects in the maintainer's deployment — they are what makes the
repository actually re-deployable by someone else, which is currently not true.

## Item 18 — Utility scripts honour `Config`

**Status**: TODO · **Severity**: Medium · **Depends on**: Item 0

### Evidence

`Config.aws_region` exists and is honoured by the CDK stack and the Lambda code. Eleven
call sites bypass it:

```
scripts/generate_card_summaries.py:38    boto3.client("cloudformation", region_name="us-east-1")
scripts/generate_missing_graphs.py:32    boto3.client("cloudformation", region_name="us-east-1")
scripts/reclassify_announcements.py:32   boto3.client("cloudformation", region_name="us-east-1")
scripts/regenerate_all_graphs.py:34      boto3.client("cloudformation", region_name="us-east-1")
scripts/retag_announcements.py:40        boto3.client("cloudformation", region_name="us-east-1")
scripts/pipeline_health.py:19-20         LOG_GROUP = "/aws/lambda/ai-radar-report-pipeline"; REGION = "us-east-1"
rebuild-site.sh:43,49,57                 --region us-east-1  (×3)
run-pipeline.sh:21,23                    FUNCTION_NAME=...; REGION="us-east-1"
```

`deploy.sh:60` does it correctly:

```bash
REGION=$(python3 -c "from src.config import Config; print(Config().aws_region)")
```

Consequence: a cloner who sets `aws_region = "eu-west-1"` gets a working deployment and
eleven broken tools, each failing with a confusing "stack does not exist" rather than
anything pointing at the cause. This is a latent bug for the maintainer too — changing
region would require edits in eleven files.

Stack and function names (`AiRadarAwsStack`, `ai-radar-report-pipeline`,
`ai-radar-website-builder`) are a different case: they are defined *by this repository*,
so hardcoding them is legitimate. They should still be single-sourced rather than repeated
as string literals in ten files, but that is tidiness, not a portability defect.

### Decision

Region comes from `Config` everywhere. Resource names move to module-level constants in
one place — `src/config.py` is the natural home given Requirement 15 — and scripts import
them. Shell scripts adopt the `deploy.sh` pattern of deriving region from `Config`.

### Alternatives considered

- **Rely on the ambient `AWS_DEFAULT_REGION` / profile region** — rejected. It would work,
  but it silently decouples the scripts from the deployed region, so a mismatched shell
  environment would fail confusingly or, worse, operate on the wrong region.
- **A shared `scripts/_common.py` helper** — worth doing for the repeated
  "find bucket from stack resources" block, which appears in six scripts. Include it.

### Verification

`grep -rn 'region_name="us-east-1"' scripts/` returns nothing. Run two scripts end to end
against the live stack. Set `aws_region` to a different value and confirm the scripts fail
with a message naming the region they looked in.

---

## Item 19 — CI workflow and zero-config synth guard

**Status**: TODO · **Severity**: High · **Depends on**: Item 0

### Evidence

No `.github` directory. An open-source project accepting pull requests has no automated
verification: `pytest` and `mypy` run only when someone remembers locally, and `deploy.sh`
truncates test output to five lines (Item 17d). There is also no test that the stack
synthesizes without local configuration, which is precisely how the current
committed-personal-values state arose and how it would recur.

### Decision

A GitHub Actions workflow on push and pull request, running:

1. `pytest tests/` — full suite including the hypothesis property tests.
2. `cdk synth` with **no** `cdk.context.json` present, asserting success. This is the
   fresh-clone path and the structural guard for the whole repository model.
3. A secret-hygiene grep for the account ID, zone ID, and site domain, failing the build
   if any reappear (per Item 9 verification).

Three steps, one workflow file, no matrix, no caching cleverness.

### Value to both audiences

For the project: contributors get feedback without maintainer intervention, and the
zero-config test means a PR cannot make personal configuration mandatory. For this
deployment: a safety net across the remaining 19 changes, several of which touch data
integrity, rather than relying on a local run before each commit.

### Alternatives considered

- **Pre-commit hooks instead of CI** — complementary, not a substitute; hooks are
  bypassable and do not run on contributors' PRs.
- **Include `mypy`** — dropped along with Item 17e; a type-checking regime should be a
  deliberate adoption, not a CI checkbox.
- **Add deploy-on-merge** — explicitly rejected. Deployment touches a live site and a
  production data bucket; it stays manual and human-initiated.

### Verification

Open a scratch PR with a deliberately failing test and confirm the workflow blocks it.
Confirm the zero-config synth step fails if a required context value is made mandatory.

---

## Item 20 — Neutral generic defaults

**Status**: TODO (D9 resolved) · **Severity**: Medium · **Depends on**: Item 0

### Evidence

`src/config.py`:

```python
preferred_geography: str = "apj"
region_expansion_bonus_local: float = 1.0
region_expansion_penalty_remote: float = -1.5
```

This is a personal preference shipped as the project default, and it is not cosmetic — it
applies a 2.5-point swing to every region-expansion announcement, which spans more than a
full star at the configured thresholds. Anyone cloning the repository silently gets
APJ-biased scoring with no indication that a preference was applied on their behalf.

`compute_geo_relevance` already treats `"global"` as "no preference, no badge", so a
neutral default is supported by the existing code paths.

The README documents `preferred_geography` as configurable, listing `apj, emea, americas,
or global` — so the intent was always that this is a per-deployment choice. The default
just happens to be one deployment's answer.

### Decision

Change the committed default to `"global"` (documented as: no geographic bias, no badges).
Set `apj` for this deployment via the D9 override mechanism.

Audit `Config` for any other field in the same category. Candidates reviewed:

| Field | Value | Verdict |
|---|---|---|
| `preferred_geography` | `"apj"` | **Personal — change default to `global`** |
| `aws_region` | `"us-east-1"` | Generic. Bedrock model availability and the CloudFront/ACM us-east-1 requirement make this a sound default. |
| `schedule_hour` / `schedule_minute` | 22:00 UTC | Generic enough; feed publishes daily and any hour works. |
| Scoring weights and thresholds | various | Generic. Tuned for the project's purpose, not to a person. |
| Model IDs | Sonnet 4.6 / Opus 4.6 / Haiku 4.5 | Generic. |
| `rss_url` | AWS "What's New" | Generic, and the point of the project. |

So `preferred_geography` is the only field needing to change.

### Consequences to accept

Changing the default alters scoring for any future cloner relative to today's behaviour —
which is the intent. It does **not** alter this deployment, provided the override lands in
the same change. Sequencing matters: ship the override mechanism and the local value
together with the default change, or one pipeline run would score without the bias and
produce inconsistent stars against historical data.

### Verification

`Config()` with no overrides returns `"global"`. `Config()` with the override set returns
`"apj"`. Re-run `scripts/reclassify_announcements.py` in dry-run mode against live data
and confirm zero score changes for this deployment — proving the override is effective and
the change is invisible here.

---

# Appendix A — Verified evidence snapshot

Collected 2026-08-13 against the live `AiRadarAwsStack` in `us-east-1` using read-only
calls (`DescribeStackResources`, `GetObject`, `HeadObject`).

Bucket names, the account ID and the site domain are redacted; the unredacted table is in
`docs/audit-evidence.local.md` (gitignored). The measurements themselves are retained here
because they justify the severity ratings and are not sensitive.

| Fact | Value |
|---|---|
| Stack resources | 43 |
| Buckets | `<DATA_BUCKET>`, `<LOGS_BUCKET>`, `<WEBSITE_BUCKET>` |
| `database/announcements.csv` | 2.4 MB |
| Unique announcement links | 243 |
| Distinct slugs | 239 |
| Slugs at the 80-char cap | 132 (55%) |
| Colliding slugs / pages lost | 2 / 4 |
| Local archives in the asset path | 2 zip files, 416 KB and 2.5 MB, in the repo root and `backups/` |
| Grep for `alarm_actions`, `versioned`, `dead_letter`, `notifications_with_subscribers` | zero matches |
| CDN scripts | Chart.js 4.5.1 and Mermaid 10.9.6, both with SRI and `crossorigin` |

# Appendix B — Audit finding to item mapping

Confirms nothing from the audit was dropped.

| Audit finding | Item |
|---|---|
| Critical #1 — `_append_link` swallows exceptions | 3 |
| Critical #2 — data bucket DESTROY, no versioning | 1 |
| Critical #3 — alarms and budget notify nobody | 2 |
| High #4 — non-atomic two-file write | 3 |
| High #5 — unbounded response read | 13 |
| High #6 — CSV rewrite growth (revised to Medium) | 16 |
| High #7 — schema drift hard-fails appends | 5 |
| Medium #8 — coarse research time gate | 8 |
| Medium #9 — relevance filter accuracy | 7 |
| Medium #10 — unauthenticated analytics write, PII | 12 |
| Medium #11 — concurrent runs lose writes | 4 |
| Medium #12 — CSP `unsafe-inline` / `unsafe-eval` | 11 |
| Medium #13 — no DLQ on async invocation | 15 |
| Low #14 — `_determine_failure_stage` string sniffing | 15 |
| Low #15 — stale docs and comments | 17c |
| Low #16 — dead code | 17a |
| Low #17 — two geo-relevance implementations | 17b |
| Low #18 — `deploy.sh` truncates test output | 17d |
| Low #19 — mypy unconfigured | 17e |
| Low #20 / Sec-High #1 — account ID in `cdk.json` | 9 |
| Finding A — slug truncation and collisions | 6 |
| Sec-High #2 — backup archives bundled into Lambdas | 10 |
| Sec-High #3 — no WAF on the analytics API | 12 |
| Sec-Med #4 — `connect-src` wildcard | 11 |
| Sec-Med #5 — stale `cdnjs` CSP grant | 11 |
| Sec-Med #7 — client IP retention | 12 |
| Sec-Med #8 — outbound fetch hardening | 13 |
| Sec-Med #9 — IAM broader than needed | 14 |
| Sec-Low #10 — Mermaid `securityLevel` unset | 11 |
| Sec-Low #11 — no S3 access logging on data bucket | 1 |

Added after the dual-audience requirement was established:

| Finding | Item |
|---|---|
| Deployment values committed across three inconsistent mechanisms | 0 |
| Scripts hardcode region, bypassing `Config` | 18 |
| No CI; generic-path deployability untested | 19 |
| `preferred_geography` ships a personal preference as project default | 20 |
| This plan document leaked account identifiers into `docs/` | fixed inline; redacted |

Findings resolved by **explicit risk acceptance** in the simplicity pass rather than by
fix — each recorded with reasoning in its item, each with a stated revisit trigger:

| Finding | Item | Revisit when |
|---|---|---|
| Sec-High #3 — no WAF on analytics API | 12 | Budget or CloudFront request alarm fires on real abuse |
| Sec-Med #9 — pipeline role retains delete on data bucket | 14 | Never, unless multi-source track raises the stakes |
| Medium #13 — no DLQ on async invocation | 15 | A failed build ever needs replaying rather than waiting a day |
| Sec-Low #11 — no S3 access logging on data bucket | 1 | A corruption incident where versioning alone can't attribute cause |

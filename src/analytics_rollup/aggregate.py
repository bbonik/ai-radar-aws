"""Aggregate_Builder: combine stored Monthly_Rollups into the All_Time_CSV.

Pure arithmetic over the JSON artifacts under rollups/ — zero Athena
queries (Req 5.2), no forecasting or smoothing (Req 13.11). Every all-time
row carries exactly one Derivation_Label (Req 6.8):

- exact:       additive scalars and topic metrics (true sums)
- upper bound: distinct-count scalars (a visitor/session active in two
               months is counted in both)
- approximate: combined ranked metrics (each month contributes only its
               own top 30 rows)
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from src.analytics_rollup import queries, rollup

_MONTH_KEY_RE = re.compile(r"^rollups/(\d{4}-(?:0[1-9]|1[0-2]))\.json$")

ADDITIVE_METRICS = ("total_pageviews_cf", "pageviews_events", "pdf_exports", "about_opens")
NON_ADDITIVE_METRICS = ("unique_visitors_cf", "total_sessions_events")

RANKED_NOTE = "approximate: each month contributes only its own top 30 rows"


def list_rollup_months(s3, bucket: str) -> list[str]:
    """Months with a stored JSON rollup, ascending."""
    months = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=rollup.ROLLUP_PREFIX):
        for obj in page.get("Contents", []):
            m = _MONTH_KEY_RE.match(obj["Key"])
            if m:
                months.append(m.group(1))
    return sorted(months)


def validate_rollup(data: dict) -> bool:
    """A rollup usable for aggregation (Req 5.8, 11.7)."""
    if not isinstance(data, dict):
        return False
    coverage = data.get("coverage", {})
    if not isinstance(coverage, dict) or coverage.get("status") not in ("complete", "partial"):
        return False
    scalars = data.get("scalars", {})
    if not isinstance(scalars, dict):
        return False
    for metric in queries.SCALAR_METRICS:
        value = scalars.get(metric)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False
    return True


def load_rollups(s3, bucket: str, months: list[str]) -> tuple[dict[str, dict], list[str]]:
    """(month -> parsed rollup, unreadable/invalid months)."""
    loaded: dict[str, dict] = {}
    excluded: list[str] = []
    for month in months:
        try:
            body = s3.get_object(Bucket=bucket, Key=rollup.month_json_key(month))["Body"].read()
            data = json.loads(body)
        except Exception:  # noqa: BLE001 — any unreadable artifact is excluded, run continues (Req 5.8)
            excluded.append(month)
            continue
        if validate_rollup(data):
            loaded[month] = data
        else:
            excluded.append(month)
    return loaded, excluded


def _mean_1dp(total: int, count: int) -> str:
    """Arithmetic mean rounded half up to one decimal place (Req 6.3).

    Callers guarantee count >= 1 (no division at zero months, Req 6.9).
    """
    mean = Decimal(total) / Decimal(count)
    return str(mean.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _gap_months(present: list[str], excluded: list[str]) -> list[str]:
    """Missing months in the contiguous span of everything stored (Req 5.6).

    Excluded (unreadable) months count as gaps too (Req 5.8) but still
    stretch the span, since an artifact for them exists under the prefix.
    """
    span = sorted(set(present) | set(excluded))
    if not span:
        return []
    full = queries.month_range(span[0], span[-1])
    included = set(present)
    return [m for m in full if m not in included]


def _combine_ranked(rollups: dict[str, dict], metric: str) -> list[dict]:
    """Sum counts for identical full keys, re-rank, top 30 (Req 6.4, 6.6)."""
    combined: dict[tuple, int] = {}
    for data in rollups.values():
        for entry in data.get("ranked", {}).get(metric, []):
            key = tuple(sorted(entry["key"].items()))
            combined[key] = combined.get(key, 0) + entry["count"]

    def sort_key(item):
        key, count = item
        parts = dict(key)
        if metric == "filter_usage":  # dimension first, then tag (Req 6.6)
            return (-count, parts["dimension"], parts["tag"])
        (single_value,) = parts.values()
        return (-count, single_value)

    ranked = sorted(combined.items(), key=sort_key)[: queries.RANKED_LIMIT]
    return [{"key": dict(k), "count": c} for k, c in ranked]


def build_all_time_csv(rollups: dict[str, dict], excluded: list[str],
                       generated_at: str) -> str:
    """Render the All_Time_CSV from included rollups (Req 5, 6, 13.10)."""
    months = sorted(rollups)
    n = len(months)
    partial_months = [m for m in months if rollups[m]["coverage"]["status"] == "partial"]

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["AI Radar AWS - All-Time Analytics"])
    w.writerow(["Months Included", n])
    w.writerow(["Earliest Month", months[0] if n else "N/A"])
    w.writerow(["Latest Month", months[-1] if n else "N/A"])
    w.writerow(["Generated", generated_at])
    if n:
        w.writerow(["Coverage", "partial" if partial_months else "complete",
                    "partial months: " + "; ".join(partial_months) if partial_months else ""])
    else:
        w.writerow(["Coverage", "N/A", ""])
    w.writerow(["Gaps", "; ".join(_gap_months(months, excluded))])
    w.writerow(["Unreadable Rollups", "; ".join(sorted(excluded))])
    if n == 0:
        w.writerow(["No months are available"])  # Req 11.5
    w.writerow([])

    # Per-month series (scalars only, Req 5.3)
    w.writerow(["=== Monthly Series ==="])
    w.writerow(["Month"] + [rollup.SCALAR_LABELS[m] for m in queries.SCALAR_METRICS]
               + ["Coverage"])
    for month in months:
        data = rollups[month]
        w.writerow([month] + [data["scalars"][m] for m in queries.SCALAR_METRICS]
                   + [data["coverage"]["status"]])
    w.writerow([])

    # All-time totals (Req 6.1-6.3, 6.8, 6.9)
    w.writerow(["=== All-Time Totals ==="])
    w.writerow(["Metric", "Value", "Derivation", "Notes"])
    for metric in queries.SCALAR_METRICS:
        label = rollup.SCALAR_LABELS[metric]
        if n == 0:
            derivation = "exact" if metric in ADDITIVE_METRICS else "upper bound"
            w.writerow([label, "N/A", derivation, ""])
            continue
        values = [rollups[m]["scalars"][metric] for m in months]
        total = sum(values)
        if metric in ADDITIVE_METRICS:
            w.writerow([label, total, "exact", ""])
        else:
            note = (f"max single month={max(values)}; mean={_mean_1dp(total, n)}; "
                    "a visitor/session active in two months is counted in both")
            w.writerow([label, total, "upper bound", note])
    w.writerow([])

    # Combined ranked metrics (Req 6.4-6.6, 6.8)
    for metric in queries.RANKED_METRICS:
        w.writerow([rollup.RANKED_SECTION_TITLES[metric]])
        w.writerow([f"Note: {RANKED_NOTE}"])
        w.writerow(rollup.RANKED_COLUMNS[metric] + ["Derivation"])
        combined = _combine_ranked(rollups, metric) if n else []
        if combined:
            for row in rollup._ranked_csv_rows(combined, metric):
                w.writerow(row + ["approximate"])
        else:
            w.writerow(["No data"])
        w.writerow([])

    # Topic series + totals (Req 5.10, 6.11, 6.12, 13.10)
    per_pair: dict[tuple[str, str], dict[str, int]] = {}
    for month in months:
        for m in rollups[month].get("topics", {}).get("metrics", []):
            pair = (m["dimension"], m["tag"])
            per_pair.setdefault(pair, {})[month] = m["views"]

    w.writerow(["=== Topic Series ==="])
    w.writerow(["Dimension", "Tag", "Month", "Views"])
    if per_pair:
        for (dim, tag) in sorted(per_pair):
            for month in sorted(per_pair[(dim, tag)]):
                w.writerow([dim, tag, month, per_pair[(dim, tag)][month]])
    else:
        w.writerow(["No data"])
    w.writerow([])

    w.writerow(["=== Topic Totals (All-Time) ==="])
    w.writerow(["Dimension", "Tag", "Total Views", "Derivation",
                "First Active", "Last Active", "Mean", "Latest Month Views"])
    if per_pair:
        latest = months[-1]
        totals = []
        for pair, series in per_pair.items():
            active = sorted(m for m, v in series.items() if v > 0)
            totals.append((
                pair, sum(series.values()),
                active[0] if active else "",
                active[-1] if active else "",
                _mean_1dp(sum(series.values()), n),
                series.get(latest, 0),
            ))
        totals.sort(key=lambda t: (-t[1], t[0][0], t[0][1]))
        for (dim, tag), total, first, last, mean, latest_views in totals:
            w.writerow([dim, tag, total, "exact", first, last, mean, latest_views])
    else:
        w.writerow(["No data"])

    return out.getvalue()


def run_aggregate(s3, logs_bucket: str) -> tuple[str, list[str]]:
    """Regenerate the All_Time_CSV; returns (key, excluded months)."""
    months = list_rollup_months(s3, logs_bucket)
    rollups, excluded = load_rollups(s3, logs_bucket, months)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = build_all_time_csv(rollups, excluded, generated_at)
    s3.put_object(Bucket=logs_bucket, Key=rollup.ALL_TIME_KEY,
                  Body=text.encode("utf-8"), ContentType="text/csv")
    if excluded:
        print(json.dumps({
            "component": "analytics_rollup", "event": "aggregate_excluded_rollups",
            "excluded_months": sorted(excluded),
        }))
    return rollup.ALL_TIME_KEY, excluded

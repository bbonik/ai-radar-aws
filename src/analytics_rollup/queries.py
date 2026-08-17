"""Query_Definition_Module: the single home of the analytics Athena SQL.

Shared by the rollup Lambda and scripts/analytics_report.py (Req 10.5) so
the two can never drift. Every query takes an explicit half-open UTC window
[start, end) — month mode passes exact month bounds (Req 1.5); day mode
passes (now - days, far-future) to preserve the existing behaviour.

Per-metric filters are copied verbatim from the original report script
(Req 2.2, 2.9): sc_status = 200 on all three CloudFront metrics; the
'%.html' path filter only on total_pageviews_cf / top_pages_cf. Ranked
queries carry a deterministic tiebreak (count DESC, then key ASC) and a
30-row limit (Req 2.3, 2.4, 10.9).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

ATHENA_DATABASE = "ai_radar_analytics"
ATHENA_WORKGROUP = "primary"
RANKED_LIMIT = 30

# Sentinel upper bound for day-scoped mode (no real upper bound today).
FAR_FUTURE = "9999-12-31"

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

SCALAR_METRICS = (
    "total_pageviews_cf",
    "unique_visitors_cf",
    "total_sessions_events",
    "pageviews_events",
    "pdf_exports",
    "about_opens",
)
RANKED_METRICS = ("top_pages_cf", "report_clicks", "filter_usage")


def is_valid_month(target_month: str) -> bool:
    """True iff the argument is a well-formed YYYY-MM month (Req 4.9)."""
    return bool(_MONTH_RE.match(target_month))


def window_for_month(target_month: str) -> tuple[str, str]:
    """Half-open [first day of month, first day of next month) as ISO dates."""
    if not is_valid_month(target_month):
        raise ValueError(f"not a valid YYYY-MM Target_Month: {target_month!r}")
    year, month = int(target_month[:4]), int(target_month[5:7])
    if month == 12:
        return f"{year}-12-01", f"{year + 1}-01-01"
    return f"{year}-{month:02d}-01", f"{year}-{month + 1:02d}-01"


def window_for_days(days: int, now: datetime) -> tuple[str, str]:
    """Day-scoped window preserving the original report's semantics."""
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    return start, FAR_FUTURE


def month_range(start_month: str, end_month: str) -> list[str]:
    """Inclusive ascending list of months from start to end (Req 4.2)."""
    for m in (start_month, end_month):
        if not is_valid_month(m):
            raise ValueError(f"not a valid YYYY-MM Target_Month: {m!r}")
    if end_month < start_month:
        raise ValueError("range end precedes range start")
    months = []
    year, month = int(start_month[:4]), int(start_month[5:7])
    while True:
        current = f"{year}-{month:02d}"
        months.append(current)
        if current == end_month:
            return months
        month += 1
        if month > 12:
            month, year = 1, year + 1


# ─── DDL (verbatim schema from the original report script) ──────────────────

def create_database_sql() -> str:
    return f"CREATE DATABASE IF NOT EXISTS {ATHENA_DATABASE}"


def cloudfront_table_sql(logs_bucket: str) -> str:
    return f"""
    CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.cloudfront_logs (
        `date` DATE,
        `time` STRING,
        x_edge_location STRING,
        sc_bytes BIGINT,
        c_ip STRING,
        cs_method STRING,
        cs_host STRING,
        cs_uri_stem STRING,
        sc_status INT,
        cs_referer STRING,
        cs_user_agent STRING,
        cs_uri_query STRING,
        cs_cookie STRING,
        x_edge_result_type STRING,
        x_edge_request_id STRING,
        x_host_header STRING,
        cs_protocol STRING,
        cs_bytes BIGINT,
        time_taken FLOAT,
        x_forwarded_for STRING,
        ssl_protocol STRING,
        ssl_cipher STRING,
        x_edge_response_result_type STRING,
        cs_protocol_version STRING,
        fle_status STRING,
        fle_encrypted_fields INT,
        c_port INT,
        time_to_first_byte FLOAT,
        x_edge_detailed_result_type STRING,
        sc_content_type STRING,
        sc_content_len BIGINT,
        sc_range_start BIGINT,
        sc_range_end BIGINT
    )
    ROW FORMAT DELIMITED
    FIELDS TERMINATED BY '\\t'
    LOCATION 's3://{logs_bucket}/cloudfront/'
    TBLPROPERTIES ('skip.header.line.count'='2')
    """


def custom_events_table_sql(logs_bucket: str) -> str:
    return f"""
    CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.custom_events (
        event_type STRING,
        path STRING,
        report_slug STRING,
        tag STRING,
        dimension STRING,
        session_id STRING,
        `timestamp` STRING,
        server_timestamp STRING,
        source_ip STRING,
        user_agent STRING
    )
    ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
    LOCATION 's3://{logs_bucket}/events/'
    """


# ─── Window predicates ───────────────────────────────────────────────────────

def _cf_window(start: str, end: str) -> str:
    return f"date >= DATE('{start}') AND date < DATE('{end}')"


def _ev_window(start: str, end: str) -> str:
    # server_timestamp is ISO 8601; lexicographic comparison equals temporal.
    return f"server_timestamp >= '{start}' AND server_timestamp < '{end}'"


# ─── Report_Metric_Set queries (9) ───────────────────────────────────────────

def total_pageviews_cf_sql(start: str, end: str) -> str:
    return f"""
        SELECT COUNT(*) as total_requests
        FROM {ATHENA_DATABASE}.cloudfront_logs
        WHERE {_cf_window(start, end)}
        AND cs_uri_stem LIKE '%.html'
        AND sc_status = 200
    """


def unique_visitors_cf_sql(start: str, end: str) -> str:
    return f"""
        SELECT COUNT(DISTINCT c_ip) as unique_ips
        FROM {ATHENA_DATABASE}.cloudfront_logs
        WHERE {_cf_window(start, end)}
        AND sc_status = 200
    """


def top_pages_cf_sql(start: str, end: str) -> str:
    return f"""
        SELECT cs_uri_stem, COUNT(*) as hits
        FROM {ATHENA_DATABASE}.cloudfront_logs
        WHERE {_cf_window(start, end)}
        AND cs_uri_stem LIKE '%.html'
        AND sc_status = 200
        GROUP BY cs_uri_stem
        ORDER BY hits DESC, cs_uri_stem ASC
        LIMIT {RANKED_LIMIT}
    """


def total_sessions_events_sql(start: str, end: str) -> str:
    return f"""
        SELECT COUNT(DISTINCT session_id) as sessions
        FROM {ATHENA_DATABASE}.custom_events
        WHERE {_ev_window(start, end)}
    """


def pageviews_events_sql(start: str, end: str) -> str:
    return f"""
        SELECT COUNT(*) as pageviews
        FROM {ATHENA_DATABASE}.custom_events
        WHERE event_type = 'pageview'
        AND {_ev_window(start, end)}
    """


def report_clicks_sql(start: str, end: str) -> str:
    return f"""
        SELECT report_slug, COUNT(*) as clicks
        FROM {ATHENA_DATABASE}.custom_events
        WHERE event_type = 'report_click'
        AND {_ev_window(start, end)}
        GROUP BY report_slug
        ORDER BY clicks DESC, report_slug ASC
        LIMIT {RANKED_LIMIT}
    """


def filter_usage_sql(start: str, end: str) -> str:
    return f"""
        SELECT dimension, tag, COUNT(*) as uses
        FROM {ATHENA_DATABASE}.custom_events
        WHERE event_type = 'filter_tag'
        AND {_ev_window(start, end)}
        GROUP BY dimension, tag
        ORDER BY uses DESC, dimension ASC, tag ASC
        LIMIT {RANKED_LIMIT}
    """


def pdf_exports_sql(start: str, end: str) -> str:
    return f"""
        SELECT COUNT(*) as exports
        FROM {ATHENA_DATABASE}.custom_events
        WHERE event_type = 'pdf_export'
        AND {_ev_window(start, end)}
    """


def about_opens_sql(start: str, end: str) -> str:
    return f"""
        SELECT COUNT(*) as opens
        FROM {ATHENA_DATABASE}.custom_events
        WHERE event_type = 'about_open'
        AND {_ev_window(start, end)}
    """


METRIC_QUERIES = {
    "total_pageviews_cf": total_pageviews_cf_sql,
    "unique_visitors_cf": unique_visitors_cf_sql,
    "top_pages_cf": top_pages_cf_sql,
    "total_sessions_events": total_sessions_events_sql,
    "pageviews_events": pageviews_events_sql,
    "report_clicks": report_clicks_sql,
    "filter_usage": filter_usage_sql,
    "pdf_exports": pdf_exports_sql,
    "about_opens": about_opens_sql,
}


# ─── Topic and coverage queries (Req 13.1, 2.11/4.5) ─────────────────────────

def report_pageviews_by_slug_sql(start: str, end: str) -> str:
    """Per-report-page view counts, NO row limit (Req 13.1)."""
    return f"""
        SELECT cs_uri_stem, COUNT(*) as views
        FROM {ATHENA_DATABASE}.cloudfront_logs
        WHERE {_cf_window(start, end)}
        AND cs_uri_stem LIKE '/reports/%.html'
        AND sc_status = 200
        GROUP BY cs_uri_stem
    """


def coverage_bounds_cf_sql(start: str, end: str) -> str:
    """Global earliest date + in-window earliest/latest, one scan."""
    in_window = _cf_window(start, end)
    return f"""
        SELECT CAST(MIN(date) AS VARCHAR) as global_min,
               CAST(MIN(CASE WHEN {in_window} THEN date END) AS VARCHAR) as win_min,
               CAST(MAX(CASE WHEN {in_window} THEN date END) AS VARCHAR) as win_max
        FROM {ATHENA_DATABASE}.cloudfront_logs
    """


def coverage_bounds_events_sql(start: str, end: str) -> str:
    in_window = _ev_window(start, end)
    return f"""
        SELECT MIN(SUBSTR(server_timestamp, 1, 10)) as global_min,
               MIN(CASE WHEN {in_window} THEN SUBSTR(server_timestamp, 1, 10) END) as win_min,
               MAX(CASE WHEN {in_window} THEN SUBSTR(server_timestamp, 1, 10) END) as win_max
        FROM {ATHENA_DATABASE}.custom_events
    """

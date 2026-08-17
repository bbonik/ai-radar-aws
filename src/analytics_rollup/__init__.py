"""Monthly analytics rollup: aggregate-then-expire history preservation.

Raw analytics (CloudFront logs, custom events) expire after 90 days. This
package rolls each completed calendar month into small permanent artifacts
under the logs bucket's `rollups/` prefix, plus a single all-time CSV.

Spec: .kiro/specs/monthly-analytics-rollup/ (requirements.md, design.md).
"""

"""
GA4 Data API: custom channel groups, monthly AI revenue share, and AI source misallocation.

Requires service account (or ADC) with analytics.readonly and GA4 property access.
"""
from __future__ import annotations

import os
import re
import sys
import urllib.parse
from collections import defaultdict
from typing import Iterable, Sequence

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    GetMetadataRequest,
    Metric,
    OrderBy,
    RunReportRequest,
)
from google.auth import default

# GA4 Data API requires this scope; default ADC often only has cloud-platform.
GA4_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/analytics.readonly",)

property_id = os.environ.get("GA4_PROPERTY_ID", "241379560")
starting_date = os.environ.get("GA4_START_DATE", "366daysAgo")
ending_date = os.environ.get("GA4_END_DATE", "yesterday")

# Optional: force the exact Data API dimension api_name from get_metadata (includes the suffix).
# Example: sessionCustomChannelGroup:8454243731 — not just the numeric ID, and not the UI label.
SESSION_CHANNEL_DIM_OVERRIDE = os.environ.get("GA4_SESSION_CUSTOM_CHANNEL_DIMENSION", "").strip() or None


def ga4_log(message: str) -> None:
    """One-line stderr trace for QA (pipelines, report generation, crawls)."""
    print(f"[GA4] {message}", file=sys.stderr, flush=True)


def _ga4_client() -> BetaAnalyticsDataClient:
    credentials, _ = default(scopes=GA4_SCOPES)
    return BetaAnalyticsDataClient(credentials=credentials)


def _normalize_ga4_property_id(raw: str) -> str:
    s = raw.strip()
    if s.startswith("properties/"):
        s = s[len("properties/") :]
    if not s.isdigit():
        raise ValueError(f"GA4 property id must be numeric (got {raw!r})")
    return s


def fetch_top_page_urls_for_origin(
    property_id: str,
    origin_base: str,
    *,
    limit: int = 100,
    start_date: str = "90daysAgo",
    end_date: str = "yesterday",
) -> tuple[list[str], str | None]:
    """
    Return the top ``limit`` page URLs for the crawl origin (hostname must match GA4 rows).

    Uses ``screenPageViews`` with dimensions ``hostName`` + ``pagePathPlusQueryString``.
    Returns (urls, error_message); error_message is None on success.
    """
    try:
        pid = _normalize_ga4_property_id(property_id)
    except ValueError as e:
        ga4_log(f"top_pages: skip — invalid property id: {e}")
        return [], str(e)

    ga4_log(
        f"top_pages: start property={pid} origin={origin_base!r} "
        f"limit={limit} {start_date!r} → {end_date!r}"
    )
    base_p = urllib.parse.urlparse(origin_base.rstrip("/") + "/")
    scheme = base_p.scheme or "https"
    origin_canon_host = (base_p.netloc or "").lower()
    origin_cmp = origin_canon_host.removeprefix("www.")

    client = _ga4_client()
    request = RunReportRequest(
        property=f"properties/{pid}",
        dimensions=[
            Dimension(name="hostName"),
            Dimension(name="pagePathPlusQueryString"),
        ],
        metrics=[Metric(name="screenPageViews")],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        order_bys=[
            OrderBy(
                desc=True,
                metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
            )
        ],
        limit=min(max(limit, 1), 250000),
    )
    try:
        response = client.run_report(request)
    except Exception as e:  # noqa: BLE001
        ga4_log(f"top_pages: run_report failed property={pid}: {e}")
        return [], str(e)

    raw_n = len(response.rows or [])
    ga4_log(f"top_pages: API returned {raw_n} row(s) before host/path filter")

    out: list[str] = []
    seen: set[str] = set()
    for row in response.rows or []:
        if len(row.dimension_values) < 2:
            continue
        host = (row.dimension_values[0].value or "").strip().lower()
        path = (row.dimension_values[1].value or "").strip()
        if not path or path in ("(not set)", "/"):
            continue
        if "?" in path:
            path = path.split("?", 1)[0]
        host_cmp = host.removeprefix("www.") if host else origin_cmp
        if host and host_cmp != origin_cmp:
            continue
        if not path.startswith("/"):
            path = "/" + path
        url = urllib.parse.urlunparse((scheme, origin_canon_host, path, "", "", ""))
        key = urllib.parse.urldefrag(url)[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= limit:
            break

    ga4_log(f"top_pages: done — {len(out)} URL(s) after filter (cap={limit})")
    if out:
        preview = ", ".join(out[:3])
        ga4_log(f"top_pages: first URLs: {preview}")
    return out, None


# --- Custom channel group metadata (prefix before ":" in api_name) ---
_CUSTOM_CHANNEL_GROUP_SCOPES = (
    "sessionCustomChannelGroup",
    "firstUserCustomChannelGroup",
    "customChannelGroup",
)


def _parse_custom_channel_group_dimension(api_name: str) -> tuple[str, str] | None:
    if ":" not in api_name:
        return None
    scope, channel_id = api_name.split(":", 1)
    if scope not in _CUSTOM_CHANNEL_GROUP_SCOPES or not channel_id.strip():
        return None
    return scope, channel_id


def _iter_custom_channel_group_rows(property_id: str) -> list[tuple[str, str, str, str, str]]:
    ga4_log(f"metadata: get_metadata property={property_id} (custom channel group dimensions)")
    client = _ga4_client()
    response = client.get_metadata(
        GetMetadataRequest(name=f"properties/{property_id}/metadata")
    )
    rows: list[tuple[str, str, str, str, str]] = []
    for dimension in response.dimensions:
        parsed = _parse_custom_channel_group_dimension(dimension.api_name)
        if not parsed:
            continue
        scope, channel_id = parsed
        rows.append(
            (scope, channel_id, dimension.api_name, dimension.ui_name, dimension.description)
        )
    scope_order = {s: i for i, s in enumerate(_CUSTOM_CHANNEL_GROUP_SCOPES)}
    rows.sort(
        key=lambda r: (
            scope_order.get(r[0], 99),
            int(r[1]) if r[1].isdigit() else r[1],
        )
    )
    ga4_log(f"metadata: parsed {len(rows)} custom channel group dimension(s)")
    return rows


def list_custom_channel_groups(property_id: str) -> None:
    rows = _iter_custom_channel_group_rows(property_id)
    if not rows:
        print("No custom channel group dimensions found for this property.")
        return
    print(f"Custom channel groups ({len(rows)}):\n")
    print(f"{'Scope':<32} {'ID':<12} {'API name (for RunReport)':<46} Display name")
    print("-" * 120)
    for _scope, _cid, api_name, ui_name, _desc in rows:
        print(f"{_scope:<32} {_cid:<12} {api_name:<46} {ui_name}")
    print()
    for _scope, _cid, api_name, ui_name, desc in rows:
        if desc:
            print(f"  {api_name}\n    {desc}\n")


def get_ga4_metadata(property_id: str) -> None:
    ga4_log(f"full_metadata: get_metadata property={property_id} (full dimension/metric list)")
    client = _ga4_client()
    response = client.get_metadata(
        GetMetadataRequest(name=f"properties/{property_id}/metadata")
    )
    print("Dimensions:")
    for dimension in response.dimensions:
        print(f"{dimension.api_name}: {dimension.ui_name} - {dimension.description}")
    print("\nMetrics:")
    for metric in response.metrics:
        print(f"{metric.api_name}: {metric.ui_name} - {metric.description}")


def _prefer_session_channel_dimension(rows: Sequence[tuple[str, str, str, str, str]]) -> str | None:
    session_rows = [r for r in rows if r[0] == "sessionCustomChannelGroup"]
    if not session_rows:
        return None
    bias = ("channel", "traffic", "marketing", "acquisition", "default", "session")
    for _s, _cid, api_name, ui_name, _d in session_rows:
        u = (ui_name or "").lower()
        if any(b in u for b in bias):
            return api_name
    return session_rows[0][2]


def _fallback_key_event_channel_dimension(
    rows: Sequence[tuple[str, str, str, str, str]],
) -> str | None:
    key_rows = [r for r in rows if r[0] == "customChannelGroup"]
    if not key_rows:
        return None
    return key_rows[0][2]


def resolve_session_custom_channel_dimension(property_id: str) -> str:
    """
    Resolve the Data API dimension for **custom** Admin channel groupings.

    Calls the Metadata API (``get_metadata``) and picks a ``sessionCustomChannelGroup:<numeric_id>``
    api_name (not the UI label). Use ``sessionDefaultChannelGroup`` when the user has not defined
    AI bucket labels—see ``ga4_fetch.fetch_ga4_traffic`` (it skips this when ``ai_channel_names`` is empty).
    """
    if SESSION_CHANNEL_DIM_OVERRIDE:
        ga4_log(
            f"channel_dim: using GA4_SESSION_CUSTOM_CHANNEL_DIMENSION={SESSION_CHANNEL_DIM_OVERRIDE!r}"
        )
        return SESSION_CHANNEL_DIM_OVERRIDE
    rows = _iter_custom_channel_group_rows(property_id)
    api = _prefer_session_channel_dimension(rows)
    if api:
        ga4_log(f"channel_dim: resolved session scope → {api!r}")
        return api
    fe = _fallback_key_event_channel_dimension(rows)
    if fe:
        print(
            "WARNING: No sessionCustomChannelGroup in metadata; using key-event "
            f"customChannelGroup ({fe}). Source/misallocation rows are still "
            "session-level — interpret with care, or add session-scoped channel groups.\n"
        )
        ga4_log(f"channel_dim: fallback key-event scope → {fe!r}")
        return fe
    raise RuntimeError(
        "No sessionCustomChannelGroup or customChannelGroup dimension in metadata. "
        "Set GA4_SESSION_CUSTOM_CHANNEL_DIMENSION to the full api_name from "
        "`python ga4_data_api.py metadata`."
    )


# --- AI channel bucket (values returned for the custom channel dimension) ---
_AI_CHANNEL_VALUE_EXACT = frozenset(
    {
        "ai",
        "geo",
        "llm",
        "gen ai",
        "genai",
        "generative",
    }
)
_AI_CHANNEL_VALUE_SUBSTRINGS = (
    "ai traffic",
    "ai organic",
    "ai paid",
    "organic ai",
    "ai referral",
    "paid ai",
    "ai search",
    "ai discovery",
    "ai-assisted",
    "generative",
    "gen ai",
    "llm",
    "chatgpt",
    "perplexity",
    "claude",
    "gemini",
    "copilot",
    "llm traffic",
    "geo ",
    " geo",
    "geo/",
    "geo-",
)


def is_ai_channel_bucket(value: str | None) -> bool:
    """True if this custom channel group *value* should count as AI/GEO in reports."""
    if not value:
        return False
    v = value.strip().lower()
    if not v or v == "(not set)":
        return False
    if v in _AI_CHANNEL_VALUE_EXACT:
        return True
    for sub in _AI_CHANNEL_VALUE_SUBSTRINGS:
        if sub in v:
            return True
    # Standalone token "ai" (e.g. "Brand | AI") without matching longer substring above
    if re.search(r"\bai\b", v):
        return True
    return False


# --- Known AI / LLM referrers (sessionSource-style); extend per client ---
# Public tuple: used by ``source_looks_ai_related`` and by ``ga4_fetch`` when no custom AI channel is configured
# (monthly AI sessions by ``sessionSource`` chart — same matching rules everywhere).
AI_TRAFFIC_SOURCE_SUBSTRINGS: tuple[str, ...] = (
    "chatgpt",
    "openai",
    "perplexity",
    "claude",
    "anthropic",
    "gemini.google",
    "bard.google",
    "copilot.microsoft",
    "copilot.",
    "mistral",
    "deepseek",
    "character.ai",
    "poe.com",
    "you.com",
    "meta.ai",
    "llm",
    "bing.com/chat",
    "edgeservices.bing",
    "duck.ai",
    "phind",
    "writesonic",
    "jasper.ai",
    "copy.ai",
)


def _normalize_source(source: str | None) -> str:
    return (source or "").strip().lower()


def source_looks_ai_related(source: str | None) -> bool:
    s = _normalize_source(source)
    if not s or s == "(not set)":
        return False
    if s == "(direct)":
        return False
    for frag in AI_TRAFFIC_SOURCE_SUBSTRINGS:
        if frag in s:
            return True
    return False


def _run_report_rows(
    property_id: str,
    dimensions: list[str],
    metrics: list[str],
    start: str,
    end: str,
    limit: int = 100000,
) -> list[list[str]]:
    ga4_log(
        f"run_report: property={property_id} {start!r}→{end!r} "
        f"dims={dimensions} metrics={metrics} limit={limit}"
    )
    client = _ga4_client()
    offset = 0
    all_rows: list[list[str]] = []
    while True:
        request = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=[Dimension(name=d) for d in dimensions],
            metrics=[Metric(name=m) for m in metrics],
            date_ranges=[DateRange(start_date=start, end_date=end)],
            limit=limit,
            offset=offset,
        )
        response = client.run_report(request)
        batch = 0
        for row in response.rows:
            dims = [dv.value for dv in row.dimension_values]
            mets = [mv.value for mv in row.metric_values]
            all_rows.append(dims + mets)
            batch += 1
        ga4_log(f"run_report: chunk offset={offset} rows={batch} (cumulative={len(all_rows)})")
        if batch < limit:
            break
        offset += limit
    ga4_log(f"run_report: finished property={property_id} total_rows={len(all_rows)}")
    return all_rows


def monthly_ai_revenue_pct(
    property_id: str,
    channel_dim: str,
    start: str = starting_date,
    end: str = ending_date,
) -> list[dict[str, float | str]]:
    """
    Aggregate by calendar month (yearMonth): AI revenue / total revenue * 100.
    Uses totalRevenue; requires ecommerce/revenue data in GA4.
    """
    dimensions = ["yearMonth", channel_dim]
    metrics = ["totalRevenue"]
    rows = _run_report_rows(property_id, dimensions, metrics, start, end)
    ga4_log(f"monthly_ai_revenue_pct: raw_row_groups={len(rows)}")

    month_total: dict[str, float] = defaultdict(float)
    month_ai: dict[str, float] = defaultdict(float)

    for r in rows:
        if len(r) < 3:
            continue
        ym, channel_value, rev_s = r[0], r[1], r[2]
        try:
            rev = float(rev_s or 0)
        except ValueError:
            rev = 0.0
        month_total[ym] += rev
        if is_ai_channel_bucket(channel_value):
            month_ai[ym] += rev

    months_sorted = sorted(month_total.keys())
    out: list[dict[str, float | str]] = []
    for ym in months_sorted:
        total = month_total[ym]
        ai_rev = month_ai.get(ym, 0.0)
        pct = (100.0 * ai_rev / total) if total else 0.0
        out.append(
            {
                "year_month": ym,
                "total_revenue": round(total, 2),
                "ai_revenue": round(ai_rev, 2),
                "ai_pct_of_revenue": round(pct, 2),
            }
        )
    ga4_log(f"monthly_ai_revenue_pct: output_months={len(out)}")
    return out


def misallocated_ai_sources(
    property_id: str,
    channel_dim: str,
    start: str = starting_date,
    end: str = ending_date,
) -> list[dict[str, float | str]]:
    """
    sessionSource × custom channel: rows where source looks AI-related but
    the session's bucket in the custom channel group is not an AI bucket.
    Aggregated by source (sum sessions).
    """
    dimensions = ["sessionSource", channel_dim]
    metrics = ["sessions"]
    rows = _run_report_rows(property_id, dimensions, metrics, start, end)
    ga4_log(f"misallocated_ai_sources: raw_row_groups={len(rows)}")

    mis_by_source: dict[str, float] = defaultdict(float)
    for r in rows:
        if len(r) < 3:
            continue
        source, channel_value, sess_s = r[0], r[1], r[2]
        if not source_looks_ai_related(source):
            continue
        if is_ai_channel_bucket(channel_value):
            continue
        try:
            sess = float(sess_s or 0)
        except ValueError:
            sess = 0.0
        mis_by_source[source] += sess

    ranked = sorted(mis_by_source.items(), key=lambda x: x[1], reverse=True)
    out = [{"session_source": src, "sessions": round(n, 0)} for src, n in ranked if n > 0]
    ga4_log(f"misallocated_ai_sources: output_sources={len(out)}")
    return out


def print_monthly_ai_revenue_table(series: Iterable[dict[str, float | str]]) -> None:
    rows = list(series)
    print("\n=== Monthly AI revenue as % of total revenue ===\n")
    if not rows:
        print("(no rows — check date range and that totalRevenue is populated)\n")
        return
    print(f"{'Year-Month':<12} {'Total revenue':>16} {'AI revenue':>16} {'AI %':>10}")
    print("-" * 58)
    for row in rows:
        print(
            f"{row['year_month']!s:<12} "
            f"{row['total_revenue']!s:>16} "
            f"{row['ai_revenue']!s:>16} "
            f"{row['ai_pct_of_revenue']!s:>9}%"
        )
    print()


def print_misallocated_table(rows: Sequence[dict[str, float | str]]) -> None:
    print("\n=== Misallocated AI-like sources (not in AI channel bucket) ===\n")
    print(
        "Sources matching AI/LLM referrers whose custom channel value "
        "did not match AI/GEO bucket heuristics.\n"
    )
    if not rows:
        print("(none in this date range — good alignment, or no AI-shaped referrers.)\n")
        return
    print(f"{'Session source':<48} {'Sessions':>12}")
    print("-" * 62)
    for row in rows:
        src = str(row["session_source"])[:46]
        print(f"{src:<48} {row['sessions']!s:>12}")
    print()


def run_ai_traffic_audit(property_id: str | None = None) -> None:
    pid = property_id or globals()["property_id"]
    ga4_log(f"run_ai_traffic_audit: property={pid}")
    channel_dim = resolve_session_custom_channel_dimension(pid)
    print(f"Using custom channel dimension: {channel_dim}\n")

    monthly = monthly_ai_revenue_pct(pid, channel_dim)
    print_monthly_ai_revenue_table(monthly)

    mis = misallocated_ai_sources(pid, channel_dim)
    print_misallocated_table(mis)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "metadata":
        list_custom_channel_groups(property_id)
    elif len(sys.argv) > 1 and sys.argv[1] == "full-metadata":
        get_ga4_metadata(property_id)
    else:
        run_ai_traffic_audit(property_id)

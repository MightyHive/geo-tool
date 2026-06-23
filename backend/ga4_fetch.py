"""
Pull GA4 session data via the Google Analytics Data API (same surface as the
Analytics MCP `run_report` tool) and build `ga4_traffic.json` for report.html.

Auth: Application Default Credentials — typically `GOOGLE_APPLICATION_CREDENTIALS`
pointing at a service-account JSON with Viewer (or Analyst) on the GA4 property.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ga4_data_api import GA4_SCOPES, ga4_log

# Channel-bucket gap rows: session sources must match known AI *agent* / product referrers.
# User-agent lineup is aligned with ``assets/reference/robots.txt`` (Tier 1–2 allow list). Training-only /
# low-value crawlers from that file (e.g. Bytespider, CCBot) are intentionally omitted here.
# Brand-owned domains (Facebook, Amazon, Apple) count only when ``bot`` appears in the source
# string (e.g. FacebookBot-style identifiers), never bare ``facebook.com`` human referrals.
_AI_AGENT_SOURCE_SUBSTRINGS: tuple[str, ...] = (
    # OpenAI / ChatGPT (GPTBot, OAI-SearchBot, ChatGPT-User)
    "chatgpt.com",
    "chat.openai.com",
    "openai.com",
    "openai.org",
    "oaiusercontent.com",
    # Anthropic (ClaudeBot, anthropic-ai)
    "claude.ai",
    "anthropic.com",
    # Perplexity (PerplexityBot)
    "perplexity.ai",
    "perplexity.com",
    # Google AI surfaces (Google-Extended, GoogleOther — not all of google.com)
    "gemini.google",
    "bard.google",
    "aistudio.google",
    "notebooklm.google",
    "generativelanguage.googleapis",
    "vertexaisearch.cloud.google",
    "ai.google",
    # Microsoft Copilot (not generic bing.com)
    "copilot.microsoft",
    "edgeservices.bing",
    # Apple (Applebot-Extended) — only when "bot" in source (handled by brand gate)
    # Amazon (Amazonbot) — only when "bot" in source
    # Meta / Facebook product AI (meta.ai); facebook.com requires "bot" (FacebookBot)
    "meta.ai",
    # Other common LLM / chat referrers (still agent/product hosts, not generic "ai" tokens)
    "you.com",
    "poe.com",
    "character.ai",
    "mistral.ai",
    "deepseek.com",
)

# Substrings that imply blocked / non-GEO agents from the same robots template — skip in gaps.
_AI_AGENT_EXCLUDE_SOURCE_SUBSTRINGS: tuple[str, ...] = (
    "bytespider",
    "bytedance",
    "ccbot",
    "commoncrawl",
)

# If GA4 surfaces crawler / product names in ``sessionSource`` (normalized to lower).
_AI_AGENT_UA_NAME_SUBSTRINGS: tuple[str, ...] = (
    "gptbot",
    "oai-searchbot",
    "oai_searchbot",
    "chatgpt-user",
    "chatgpt_user",
    "claudebot",
    "anthropic-ai",
    "anthropic_ai",
    "perplexitybot",
    "google-extended",
    "google_extended",
    "googleother",
    "google-other",
    "applebot-extended",
    "applebot_extended",
    "amazonbot",
    "facebookbot",
)


def normalize_property_id(raw: str) -> str:
    s = raw.strip()
    if s.startswith("properties/"):
        s = s[len("properties/") :]
    if not s.isdigit():
        raise ValueError(f"GA4 property id must be numeric (got {raw!r})")
    return s


_MONTH_ABBREV = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def year_month_label(ym: str) -> str:
    """GA4 ``yearMonth`` is YYYYMM, e.g. ``202501`` → ``Jan 2025``."""
    s = (ym or "").strip()
    if len(s) == 6 and s.isdigit():
        y = s[:4]
        mo = int(s[4:6], 10)
        if 1 <= mo <= 12:
            return f"{_MONTH_ABBREV[mo - 1]} {y}"
    return s


def last_complete_calendar_month_end() -> date:
    """Last day of the previous calendar month (exclude the in-progress month)."""
    today = date.today()
    first_this = date(today.year, today.month, 1)
    return first_this - timedelta(days=1)


def cap_ga4_end_date_to_last_full_month(end_date: str) -> str:
    """
    GA4 charts should not include the current incomplete calendar month.
    Cap ``yesterday`` / ``today`` / explicit YYYY-MM-DD to the last day of the prior month.
    Leave relative strings like ``90daysAgo`` unchanged (GA4 still needs a literal end for ranges).
    """
    cap_d = last_complete_calendar_month_end()
    cap_s = cap_d.strftime("%Y-%m-%d")
    s = (end_date or "").strip()
    low = s.lower()
    if low in ("today", "yesterday"):
        return cap_s
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        y, mo, d = (int(x) for x in s.split("-"))
        try:
            parsed = date(y, mo, d)
        except ValueError:
            return cap_s
        return min(parsed, cap_d).strftime("%Y-%m-%d")
    return s


def normalize_ga4_api_date(value: str, *, fallback: str = "365daysAgo") -> str:
    """
    GA4 Data API v1 only accepts: YYYY-MM-DD, yesterday, today, or ``NdaysAgo`` (N integer).
    Strings like ``12monthsAgo`` are rejected — map common aliases to ``NdaysAgo``.
    """
    s = (value or "").strip()
    if not s:
        return fallback
    low = s.lower().replace(" ", "")
    if low in ("yesterday", "today"):
        return low
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m_days = re.fullmatch(r"(\d+)daysago", low)
    if m_days:
        return f"{m_days.group(1)}daysAgo"
    # Common invalid aliases from docs / env typos
    months_alias: dict[str, int] = {
        "1monthsago": 31,
        "2monthsago": 62,
        "3monthsago": 92,
        "6monthsago": 183,
        "12monthsago": 365,
        "18monthsago": 548,
        "24monthsago": 730,
    }
    if low in months_alias:
        return f"{months_alias[low]}daysAgo"
    m_mon = re.fullmatch(r"(\d+)monthsago", low)
    if m_mon:
        n = int(m_mon.group(1))
        days = min(366 * 6, max(1, int(round(n * 30.5))))
        return f"{days}daysAgo"
    return s


def _ga4_client_with_scopes() -> Any:
    """Same ADC + scopes as ga4_data_api (Analytics read)."""
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.auth import default

    credentials, _ = default(scopes=GA4_SCOPES)
    return BetaAnalyticsDataClient(credentials=credentials)


def _channel_set(names: list[str]) -> set[str]:
    return {n.strip().lower() for n in names if n and n.strip()}


def _session_source_is_ai_agent_referrer(source: str, _medium: str) -> bool:
    """
    True only for session sources that look like known AI agent / product hostnames
    (see ``assets/reference/robots.txt``). Excludes generic ``facebook.com`` / ``amazon.`` / ``apple.``
    unless ``bot`` appears in the source (e.g. FacebookBot-style values GA4 may surface).
    Session medium is not used for fuzzy matching (only ``session_source`` is evaluated).
    """
    s = (source or "").strip().lower()
    if not s or s in ("(not set)", "(direct)", "direct"):
        return False
    for bad in _AI_AGENT_EXCLUDE_SOURCE_SUBSTRINGS:
        if bad in s:
            return False
    for ua in _AI_AGENT_UA_NAME_SUBSTRINGS:
        if ua in s:
            return True
    # Brand-owned hosts: require an explicit *bot* token in the source (user rule: FacebookBot yes, Facebook no).
    if "bot" not in s:
        if "facebook.com" in s or ".facebook." in s or s.startswith("fb.com") or ".fb.com" in s:
            return False
        if re.search(r"(^|[./])amazon\.", s):
            return False
        if re.search(r"(^|[./])apple\.", s):
            return False
    for frag in _AI_AGENT_SOURCE_SUBSTRINGS:
        if frag in s:
            return True
    return False


def _row_sessions(row: Any) -> int:
    try:
        return int(float(row.metric_values[0].value))
    except (ValueError, TypeError, IndexError, AttributeError):
        return 0


def _row_metric_int(row: Any, index: int) -> int:
    try:
        return int(float(row.metric_values[index].value))
    except (ValueError, TypeError, IndexError, AttributeError):
        return 0


def _conversion_rate_summary(sessions: int, purchases: int) -> dict[str, Any]:
    rate_pct: float | None = None
    if sessions > 0:
        rate_pct = round(100.0 * purchases / sessions, 2)
    return {
        "sessions": int(sessions),
        "purchases": int(purchases),
        "rate_pct": rate_pct,
    }


def _fetch_conversion_rates(
    client: Any,
    prop: str,
    dr: list[Any],
    channel_dim_api: str,
    ai_norm: set[str],
    g4_mod: Any,
) -> dict[str, Any]:
    """Property-wide and AI-segment conversion rate (ecommercePurchases / sessions)."""
    from google.analytics.data_v1beta.types import Dimension, Metric, RunReportRequest

    def totals_req(offset: int) -> Any:
        return RunReportRequest(
            property=prop,
            date_ranges=dr,
            metrics=[Metric(name="sessions"), Metric(name="ecommercePurchases")],
            limit=100,
            offset=offset,
        )

    all_sessions = 0
    all_purchases = 0
    for row in _paginate_run_report(client, totals_req, label="conversion_rate_all"):
        all_sessions += _row_metric_int(row, 0)
        all_purchases += _row_metric_int(row, 1)

    ai_sessions = 0
    ai_purchases = 0
    ai_mode = "ai_channel" if ai_norm else "known_ai_sources"

    if ai_norm:

        def ch_req(offset: int) -> Any:
            return RunReportRequest(
                property=prop,
                date_ranges=dr,
                dimensions=[Dimension(name=channel_dim_api)],
                metrics=[Metric(name="sessions"), Metric(name="ecommercePurchases")],
                limit=100000,
                offset=offset,
            )

        for row in _paginate_run_report(
            client, ch_req, label=f"conversion_rate_ai_{channel_dim_api.replace(':', '_')}"
        ):
            ch = str(row.dimension_values[0].value).strip().lower()
            if ch not in ai_norm:
                continue
            ai_sessions += _row_metric_int(row, 0)
            ai_purchases += _row_metric_int(row, 1)
    elif g4_mod is not None:
        looks = g4_mod.source_looks_ai_related

        def src_req(offset: int) -> Any:
            return RunReportRequest(
                property=prop,
                date_ranges=dr,
                dimensions=[Dimension(name="sessionSource")],
                metrics=[Metric(name="sessions"), Metric(name="ecommercePurchases")],
                limit=100000,
                offset=offset,
            )

        for row in _paginate_run_report(client, src_req, label="conversion_rate_ai_sources"):
            src = str(row.dimension_values[0].value)
            if not looks(src):
                continue
            ai_sessions += _row_metric_int(row, 0)
            ai_purchases += _row_metric_int(row, 1)
    else:
        ai_mode = "unavailable"

    return {
        "all_channels": _conversion_rate_summary(all_sessions, all_purchases),
        "ai": {**_conversion_rate_summary(ai_sessions, ai_purchases), "mode": ai_mode},
    }


def _paginate_run_report(
    client: Any,
    request_factory: Any,
    page_size: int = 100000,
    *,
    label: str = "run_report",
) -> list[Any]:
    rows: list[Any] = []
    offset = 0
    while True:
        ga4_log(f"{label}: request offset={offset} limit={page_size}")
        req = request_factory(offset)
        resp = client.run_report(req)
        chunk = list(resp.rows or [])
        rows.extend(chunk)
        ga4_log(f"{label}: chunk rows={len(chunk)} cumulative={len(rows)}")
        if len(chunk) < page_size:
            break
        offset += page_size
    ga4_log(f"{label}: done total_rows={len(rows)}")
    return rows


def _sum_ai_channel_sessions_in_range(
    client: Any,
    prop: str,
    channel_dim_api: str,
    ai_norm: set[str],
    range_start: str,
    range_end: str,
) -> int:
    """Total sessions attributed to configured AI channel bucket(s) over a GA4 date range."""
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )

    dr = [DateRange(start_date=range_start, end_date=range_end, name="ai_sum")]

    def req_fn(offset: int) -> Any:
        return RunReportRequest(
            property=prop,
            date_ranges=dr,
            dimensions=[Dimension(name=channel_dim_api)],
            metrics=[Metric(name="sessions")],
            limit=100000,
            offset=offset,
        )

    total = 0
    for row in _paginate_run_report(
        client, req_fn, label=f"ai_bucket_sessions_sum_{channel_dim_api.replace(':', '_')}"
    ):
        dims = [d.value for d in row.dimension_values]
        if not dims:
            continue
        ch = str(dims[0]).strip().lower()
        if ch not in ai_norm:
            continue
        total += _row_sessions(row)
    return total


def _sum_ai_source_sessions_heuristic_in_range(
    client: Any,
    prop: str,
    g4_mod: Any,
    range_start: str,
    range_end: str,
) -> int:
    """Total sessions whose ``sessionSource`` matches ``ga4_data_api.source_looks_ai_related``."""
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )

    looks = g4_mod.source_looks_ai_related
    dr = [DateRange(start_date=range_start, end_date=range_end, name="ai_src_sum")]

    def req_fn(offset: int) -> Any:
        return RunReportRequest(
            property=prop,
            date_ranges=dr,
            dimensions=[Dimension(name="sessionSource")],
            metrics=[Metric(name="sessions")],
            limit=100000,
            offset=offset,
        )

    total = 0
    for row in _paginate_run_report(client, req_fn, label="ai_source_sessions_sum_heuristic"):
        dims = [d.value for d in row.dimension_values]
        if not dims:
            continue
        src = str(dims[0]).strip()
        if not looks(src):
            continue
        total += _row_sessions(row)
    return total


def _build_monthly_ai_sessions_by_source_pack(
    client: Any,
    prop: str,
    dr: list[Any],
    channel_dim_api: str,
    ai_norm: set[str],
    g4_mod: Any,
    cap_ym: str,
    all_months_sorted: list[str],
) -> dict[str, Any]:
    """
    Monthly stacked-bar data: sessions by ``sessionSource``.

    - If ``ai_norm`` is non-empty: only rows whose custom channel value is in ``ai_norm`` (user-configured AI bucket).
    - Else: only sources matching ``ga4_data_api.source_looks_ai_related`` (``AI_TRAFFIC_SOURCE_SUBSTRINGS``).
    """
    from google.analytics.data_v1beta.types import Dimension, Metric, RunReportRequest

    month_src: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    mode: str | None

    if ai_norm:
        mode = "ai_channel"

        def req_ch(offset: int) -> Any:
            return RunReportRequest(
                property=prop,
                date_ranges=dr,
                dimensions=[
                    Dimension(name="yearMonth"),
                    Dimension(name="sessionSource"),
                    Dimension(name=channel_dim_api),
                ],
                metrics=[Metric(name="sessions")],
                limit=100000,
                offset=offset,
            )

        for row in _paginate_run_report(
            client, req_ch, label=f"monthly_ai_by_source_{channel_dim_api.replace(':', '_')}"
        ):
            dims = [d.value for d in row.dimension_values]
            if len(dims) < 3:
                continue
            ym, src, ch = dims[0], dims[1], dims[2]
            if str(ym) > cap_ym:
                continue
            if ch.strip().lower() not in ai_norm:
                continue
            s = (src or "").strip() or "(not set)"
            month_src[ym][s] += _row_sessions(row)

    else:
        if g4_mod is None:
            return {"mode": None, "months": [], "source_order": []}
        mode = "known_ai_sources"
        looks = g4_mod.source_looks_ai_related

        def req_src(offset: int) -> Any:
            return RunReportRequest(
                property=prop,
                date_ranges=dr,
                dimensions=[
                    Dimension(name="yearMonth"),
                    Dimension(name="sessionSource"),
                ],
                metrics=[Metric(name="sessions")],
                limit=100000,
                offset=offset,
            )

        for row in _paginate_run_report(client, req_src, label="monthly_ai_by_source_heuristic"):
            dims = [d.value for d in row.dimension_values]
            if len(dims) < 2:
                continue
            ym, src = dims[0], dims[1]
            if str(ym) > cap_ym:
                continue
            if not looks(src):
                continue
            s = (src or "").strip() or "(not set)"
            month_src[ym][s] += _row_sessions(row)

    if not month_src:
        return {"mode": mode, "months": [], "source_order": []}

    totals: dict[str, int] = defaultdict(int)
    for _ym, srcmap in month_src.items():
        for s, n in srcmap.items():
            totals[s] += n

    TOP_N = 14
    other_label = "Other sources"
    top_sources = [s for s, _ in sorted(totals.items(), key=lambda x: -x[1])[:TOP_N]]

    months_out: list[dict[str, Any]] = []
    for ym in all_months_sorted:
        if str(ym) > cap_ym:
            continue
        m = month_src.get(ym) or {}
        by_s: dict[str, int] = {}
        other = 0
        for s, n in m.items():
            if s in top_sources:
                by_s[s] = by_s.get(s, 0) + n
            else:
                other += n
        if other > 0:
            by_s[other_label] = other
        months_out.append(
            {
                "year_month": ym,
                "label": year_month_label(str(ym)),
                "by_source": by_s,
            }
        )

    has_other = any(other_label in (mo.get("by_source") or {}) for mo in months_out)
    source_order = list(top_sources)
    if has_other and other_label not in source_order:
        source_order.append(other_label)

    return {"mode": mode, "months": months_out, "source_order": source_order}


def fetch_ga4_traffic(
    property_id: str,
    ai_channel_names: list[str],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    try:
        from google.analytics.data_v1beta.types import (
            DateRange,
            Dimension,
            Metric,
            OrderBy,
            RunReportRequest,
        )
    except ImportError as e:
        raise ImportError(
            "GA4 fetch requires google-analytics-data. Install: pip install google-analytics-data"
        ) from e

    raw_start = start_date or os.environ.get("GA4_START_DATE") or "365daysAgo"
    raw_end = end_date or os.environ.get("GA4_END_DATE") or "yesterday"
    start_date = normalize_ga4_api_date(raw_start, fallback="365daysAgo")
    end_date = normalize_ga4_api_date(raw_end, fallback="yesterday")
    end_date = cap_ga4_end_date_to_last_full_month(end_date)
    cap_d = last_complete_calendar_month_end()
    cap_ym = f"{cap_d.year}{cap_d.month:02d}"

    pid = normalize_property_id(property_id)
    prop = f"properties/{pid}"
    dr = [DateRange(start_date=start_date, end_date=end_date, name="range1")]
    dr_gap = [DateRange(start_date="90daysAgo", end_date=end_date, name="gap90")]
    ai_norm = _channel_set(ai_channel_names)

    ga4_log(
        f"fetch_ga4_traffic: start property={pid} {start_date!r}→{end_date!r} "
        f"ai_channel_names={len(ai_norm)}"
    )

    g4_mod: Any = None
    channel_dim_api = "sessionDefaultChannelGroup"
    try:
        import ga4_data_api as g4_mod

        # Custom bucket labels (GA4_AI_CHANNEL_NAMES) only apply to custom channel dimensions.
        # Without labels, use Google's default session channel (not sessionCustomChannelGroup:…).
        if ai_norm:
            channel_dim_api = g4_mod.resolve_session_custom_channel_dimension(pid)
            ga4_log(
                f"fetch_ga4_traffic: AI channel name(s) set — metadata-resolved dimension {channel_dim_api!r} "
                "(sessionCustomChannelGroup:<id> from Admin custom channel grouping when present)"
            )
        else:
            ga4_log(
                "fetch_ga4_traffic: no AI channel names — using sessionDefaultChannelGroup for "
                "monthly sessions + gaps; stacked-by-source chart uses known AI referrers only."
            )
    except ImportError as e:
        ga4_log(
            f"fetch_ga4_traffic: ga4_data_api not importable ({e}); "
            "sessions trend + gaps use sessionDefaultChannelGroup; custom bundle skipped"
        )
        g4_mod = None
    except Exception as e:  # noqa: BLE001 — metadata/resolve; fall back to default channel
        ga4_log(
            f"fetch_ga4_traffic: channel dimension resolve failed ({e!r}); "
            "sessions trend + gaps use sessionDefaultChannelGroup"
        )
        channel_dim_api = "sessionDefaultChannelGroup"

    client = _ga4_client_with_scopes()

    def sessions_by_month_req(offset: int) -> Any:
        return RunReportRequest(
            property=prop,
            date_ranges=dr,
            dimensions=[
                Dimension(name="yearMonth"),
                Dimension(name=channel_dim_api),
            ],
            metrics=[Metric(name="sessions")],
            limit=100000,
            offset=offset,
        )

    month_total: dict[str, int] = defaultdict(int)
    month_ai: dict[str, int] = defaultdict(int)
    channels_seen: set[str] = set()

    for row in _paginate_run_report(
        client,
        sessions_by_month_req,
        label=f"monthly_sessions_{channel_dim_api.replace(':', '_')}",
    ):
        dims = [d.value for d in row.dimension_values]
        if len(dims) < 2:
            continue
        ym, ch = dims[0], dims[1]
        sessions = _row_sessions(row)
        channels_seen.add(ch.strip().lower())
        month_total[ym] += sessions
        if ch.strip().lower() in ai_norm:
            month_ai[ym] += sessions

    monthly_sessions_out: list[dict[str, int | str]] = []
    for ym in sorted(month_total.keys()):
        monthly_sessions_out.append(
            {
                "year_month": ym,
                "label": year_month_label(ym),
                "total_sessions": month_total[ym],
                "ai_sessions": month_ai.get(ym, 0),
            }
        )

    monthly_sessions_out = [r for r in monthly_sessions_out if str(r.get("year_month", "")) <= cap_ym]

    all_months_sorted = sorted(month_total.keys())
    by_src_pack = _build_monthly_ai_sessions_by_source_pack(
        client,
        prop,
        dr,
        channel_dim_api,
        ai_norm,
        g4_mod,
        cap_ym,
        all_months_sorted,
    )

    if not ai_norm and by_src_pack.get("months"):
        ai_by_ym: dict[str, int] = {}
        for mo in by_src_pack["months"]:
            ym = str(mo.get("year_month") or "")
            ai_by_ym[ym] = sum(int(v) for v in (mo.get("by_source") or {}).values())
        for row in monthly_sessions_out:
            ym = str(row.get("year_month") or "")
            row["ai_sessions"] = ai_by_ym.get(ym, 0)

    total_ai_90 = 0
    if ai_norm:
        total_ai_90 = _sum_ai_channel_sessions_in_range(
            client, prop, channel_dim_api, ai_norm, "90daysAgo", end_date
        )
    elif g4_mod is not None:
        total_ai_90 = _sum_ai_source_sessions_heuristic_in_range(
            client, prop, g4_mod, "90daysAgo", end_date
        )

    def gaps_req(offset: int) -> Any:
        return RunReportRequest(
            property=prop,
            date_ranges=dr_gap,
            dimensions=[
                Dimension(name="sessionSource"),
                Dimension(name="sessionMedium"),
                Dimension(name=channel_dim_api),
            ],
            metrics=[Metric(name="sessions")],
            order_bys=[OrderBy(desc=True, metric=OrderBy.MetricOrderBy(metric_name="sessions"))],
            limit=100000,
            offset=offset,
        )

    gap_rows: list[dict[str, Any]] = []
    for row in _paginate_run_report(
        client, gaps_req, label=f"gaps_source_medium_{channel_dim_api.replace(':', '_')}"
    ):
        dims = [d.value for d in row.dimension_values]
        if len(dims) < 3:
            continue
        src, med, ch = dims[0], dims[1], dims[2]
        sessions = _row_sessions(row)
        if sessions <= 0:
            continue
        ch_l = ch.strip().lower()
        if ch_l in ai_norm:
            continue
        if g4_mod is not None:
            if not g4_mod.source_looks_ai_related(src):
                continue
        elif not _session_source_is_ai_agent_referrer(src, med):
            continue
        gap_rows.append(
            {
                "session_source": src,
                "session_medium": med,
                "channel_bucket": ch,
                "session_default_channel_group": ch,
                "sessions": sessions,
            }
        )

    gap_rows.sort(key=lambda r: int(r.get("sessions") or 0), reverse=True)
    if total_ai_90 > 0:
        gap_rows = [r for r in gap_rows if int(r.get("sessions") or 0) * 100 >= total_ai_90]
    else:
        gap_rows = []

    ga4_log(
        f"fetch_ga4_traffic: monthly_session_buckets={len(monthly_sessions_out)} "
        f"gap_candidates_kept={len(gap_rows)} distinct_channel_buckets_seen={len(channels_seen)}"
    )

    conversion_rate: dict[str, Any] = {}
    try:
        conversion_rate = _fetch_conversion_rates(
            client, prop, dr, channel_dim_api, ai_norm, g4_mod
        )
        ga4_log(
            "fetch_ga4_traffic: conversion_rate "
            f"all={conversion_rate.get('all_channels', {}).get('rate_pct')}% "
            f"ai={conversion_rate.get('ai', {}).get('rate_pct')}% "
            f"ai_mode={conversion_rate.get('ai', {}).get('mode')}"
        )
    except Exception as e:  # noqa: BLE001 — optional metric; sessions export still succeeds
        conversion_rate = {"error": str(e)}
        ga4_log(f"fetch_ga4_traffic: conversion_rate fetch failed: {e}")

    has_ai = bool(ai_norm)
    notes_parts = [
        f"GA4 Data API · property {pid} · {start_date} → {end_date} · "
        f"calendar month (yearMonth) × {channel_dim_api} through last complete month only; "
        f"bucket-gap table uses 90 days to {end_date} and omits sources under 1% of AI-channel sessions in that window.",
    ]
    if ai_norm and not any(ai_norm & channels_seen):
        notes_parts.append(
            f"None of the configured AI channel names appeared in {channel_dim_api} for this range — "
            "check spelling vs GA4 Admin channel group bucket labels (matched case-insensitively)."
        )
    if by_src_pack.get("mode") == "ai_channel":
        notes_parts.append(
            "Stacked AI-by-source chart: sessionSource for sessions whose channel bucket matches your configured AI name(s) only."
        )
    elif by_src_pack.get("mode") == "known_ai_sources" and (by_src_pack.get("months") or []):
        notes_parts.append(
            "Stacked AI-by-source chart: sessionSource rows matching ga4_data_api.AI_TRAFFIC_SOURCE_SUBSTRINGS "
            "(no GA4_AI_CHANNEL_NAMES / --ga4-ai-channels)."
        )

    out: dict[str, Any] = {
        "source": "google_analytics_data_api",
        "property_id": pid,
        "has_ai_channel": has_ai,
        "ai_channel_names": [n for n in ai_channel_names if n.strip()],
        "monthly_sessions": monthly_sessions_out,
        "monthly_ai_sessions_by_source": by_src_pack,
        "weekly_channel_dimension": channel_dim_api,
        "source_medium_gaps": gap_rows[:500],
        "source_medium_gaps_ai_sessions_denominator_90d": int(total_ai_90),
        "conversion_rate": conversion_rate,
        "notes": " ".join(notes_parts),
    }

    # Custom channel groups: monthly AI % of revenue + misallocated AI-like sources (same dimension as sessions trend + gaps).
    if g4_mod is not None:
        ga4_log("fetch_ga4_traffic: custom channel bundle (monthly + misallocation) …")
        try:
            monthly = g4_mod.monthly_ai_revenue_pct(
                pid, channel_dim_api, start_date=start_date, end_date=end_date
            )
            monthly = [r for r in monthly if str(r.get("year_month", "")) <= cap_ym]
            mis = g4_mod.misallocated_ai_sources(
                pid, channel_dim_api, start_date=start_date, end_date=end_date
            )
            out["custom_channel_dimension"] = channel_dim_api
            out["monthly_ai_revenue_pct"] = monthly
            out["misallocated_ai_sources"] = mis
            ga4_log(
                f"fetch_ga4_traffic: custom bundle ok dimension={channel_dim_api!r} "
                f"monthly_points={len(monthly)} misallocated_sources={len(mis)}"
            )
        except Exception as e:  # noqa: BLE001 — surface API issues without failing sessions trend export
            out["custom_channel_bundle_error"] = str(e)
            out["custom_channel_dimension"] = None
            out["monthly_ai_revenue_pct"] = []
            out["misallocated_ai_sources"] = []
            ga4_log(f"fetch_ga4_traffic: custom bundle failed (sessions trend export still ok): {e}")
    else:
        out["custom_channel_bundle_error"] = (
            "Custom channel bundle skipped (ga4_data_api not importable)."
        )
        out["custom_channel_dimension"] = None
        out["monthly_ai_revenue_pct"] = []
        out["misallocated_ai_sources"] = []
        ga4_log("fetch_ga4_traffic: custom bundle skipped (ga4_data_api not importable)")

    ga4_log("fetch_ga4_traffic: finished building payload")
    return out


def fetch_and_save(
    audit_dir: Path,
    property_id: str,
    ai_channel_names: list[str],
) -> Path:
    audit_dir = audit_dir.resolve()
    audit_dir.mkdir(parents=True, exist_ok=True)
    ga4_log(f"fetch_and_save: audit_dir={audit_dir} property={property_id.strip()!r}")
    data = fetch_ga4_traffic(property_id, ai_channel_names)
    out = audit_dir / "ga4_traffic.json"
    import json

    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ga4_log(f"fetch_and_save: wrote {out}")
    return out

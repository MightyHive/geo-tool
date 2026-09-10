#!/usr/bin/env python3
"""
Pull daily/weekly GA4 sessions and ecommerce purchases by custom channel rules.

Dimensions used for classification:
  ``sessionSource``, ``sessionMedium``, ``sessionDefaultChannelGroup``

Channel rules (first match wins)::

  1. AI   — sessionSource matches chatgpt|copilot|gemini|perplexity|claude
  2. PPC  — default channel group in Paid Search / Cross-network / Paid Shopping /
            Paid Video / Display / Paid Other, OR sessionMedium == cpc
  3. SEO  — default channel group in Organic Search / Google Places / Organic Shopping,
            OR sessionSource == search.brave.com, OR sessionSource matches Google Places
  4. Direct — GA4 default channel group Direct
  5. Other Traffic — everything else

Auth: Google OAuth — same CLI flow as other research GA4 scripts
(``research/.ga4_oauth_token.json``).

Environment:
  GA4_PROPERTY_ID / GA4_PROPERTY_NAME
  GA4_START_DATE / GA4_END_DATE        Override default YYYY-MM-DD range (ISO only)

Default date range: 2022-06-01 through 2026-05-31.

Outputs (default: ``research/ga4/exports/daily/``)::

  ga4_channel_long_{name}_{property_id}.csv     — date × channel
  ga4_channel_wide_{name}_{property_id}.csv     — one row per day
  ga4_channel_weekly_{name}_{property_id}.csv   — Sunday-start week × channel metrics
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

_GA4_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _GA4_ROOT.parent.parent
_RESEARCH_ROOT = _GA4_ROOT.parent
_BACKEND_ROOT = _REPO_ROOT / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from paths import GA4_DAILY  # noqa: E402

from ga4_data_api import ga4_log  # noqa: E402
from ga4_fetch import (  # noqa: E402
    _ga4_client_with_scopes,
    _paginate_run_report,
    _row_metric_int,
    normalize_ga4_api_date,
    normalize_property_id,
)
from ga4_oauth import (  # noqa: E402
    _DEFAULT_CLI_TOKEN_PATH,
    acquire_cli_credentials,
    install_oauth_application_default_credentials,
)
from geo_app_env import load_app_environment  # noqa: E402

DEFAULT_START = "2022-06-01"
DEFAULT_END = "2026-05-31"

CHANNEL_AI = "AI"
CHANNEL_PPC = "PPC"
CHANNEL_SEO = "SEO"
CHANNEL_DIRECT = "Direct"
CHANNEL_OTHER = "Other Traffic"
CHANNEL_ORDER: tuple[str, ...] = (
    CHANNEL_AI,
    CHANNEL_PPC,
    CHANNEL_SEO,
    CHANNEL_DIRECT,
    CHANNEL_OTHER,
)

PPC_CHANNEL_GROUPS = frozenset(
    {
        "Paid Search",
        "Cross-network",
        "Paid Shopping",
        "Paid Video",
        "Display",
        "Paid Other",
    }
)
SEO_CHANNEL_GROUPS = frozenset(
    {
        "Organic Search",
        "Google Places",
        "Organic Shopping",
    }
)

# Case-insensitive source patterns (mirrors GA4 REGEXP_CONTAINS intent).
_AI_SOURCE_RE = re.compile(
    r"chatgpt|copilot|gemini|perplexity|claude",
    re.IGNORECASE,
)
_GOOGLE_PLACES_SOURCE_RE = re.compile(
    r"Google Places|Google\+Places",
    re.IGNORECASE,
)


def _filename_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").lower()
    return slug or "property"


def resolve_property_id(cli_value: str | None) -> str:
    """CLI ``--property-id`` overrides ``GA4_PROPERTY_ID`` env."""
    if cli_value and str(cli_value).strip():
        return normalize_property_id(str(cli_value))
    env_val = (os.environ.get("GA4_PROPERTY_ID") or "").strip()
    if env_val:
        return normalize_property_id(env_val)
    raise SystemExit("GA4 property id required: pass --property-id or set GA4_PROPERTY_ID")


def resolve_property_name(cli_value: str | None, property_id: str) -> str:
    """CLI ``--property-name`` overrides ``GA4_PROPERTY_NAME`` env."""
    if cli_value and str(cli_value).strip():
        return str(cli_value).strip()
    env_val = (os.environ.get("GA4_PROPERTY_NAME") or "").strip()
    if env_val:
        return env_val
    return property_id


def export_output_paths(
    output_dir: Path,
    *,
    property_name: str,
    property_id: str,
) -> tuple[Path, Path, Path]:
    slug = _filename_slug(property_name)
    pid = normalize_property_id(property_id)
    stem = f"ga4_channel_{{kind}}_{slug}_{pid}.csv"
    return (
        output_dir / stem.format(kind="long"),
        output_dir / stem.format(kind="wide"),
        output_dir / stem.format(kind="weekly"),
    )


def _parse_iso_date(value: str) -> date:
    y, m, d = (int(x) for x in value.split("-"))
    return date(y, m, d)


def _ga4_api_date_to_iso(value: str, *, reference: date | None = None) -> str:
    """Convert a GA4 API date (``YYYY-MM-DD``, ``yesterday``, ``NdaysAgo``) to ``YYYY-MM-DD``."""
    s = normalize_ga4_api_date(value)
    ref = reference or date.today()
    low = s.lower()
    if low == "today":
        return _format_iso_date(ref)
    if low == "yesterday":
        return _format_iso_date(ref - timedelta(days=1))
    m_days = re.fullmatch(r"(\d+)daysAgo", low)
    if m_days:
        return _format_iso_date(ref - timedelta(days=int(m_days.group(1))))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    raise ValueError(f"Cannot resolve GA4 date {value!r} to YYYY-MM-DD")


def resolve_export_dates(start: str, end: str) -> tuple[str, str]:
    """Normalize and resolve export bounds to concrete ISO dates."""
    iso_start = _ga4_api_date_to_iso(start or DEFAULT_START)
    iso_end = _ga4_api_date_to_iso(end or DEFAULT_END)
    if iso_start > iso_end:
        raise ValueError(f"Start date {iso_start} is after end date {iso_end}")
    return iso_start, iso_end


def _env_iso_date(name: str, fallback: str) -> str:
    """Use env override only when it is an explicit YYYY-MM-DD (ignore audit-tool relatives)."""
    raw = (os.environ.get(name) or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    return fallback


def _format_iso_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _ga4_date_to_iso(raw: str) -> str:
    s = (raw or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def sunday_week_start(d: date) -> date:
    """Sunday-start week label (matches Google Trends / PPC brand exports)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def classify_channel(
    session_source: str,
    session_medium: str,
    default_channel_group: str,
) -> str:
    """Map a GA4 traffic row to AI / PPC / SEO / Direct / Other Traffic."""
    source = (session_source or "").strip()
    medium = (session_medium or "").strip()
    group = (default_channel_group or "").strip()

    if _AI_SOURCE_RE.search(source):
        return CHANNEL_AI

    if group in PPC_CHANNEL_GROUPS or medium.lower() == "cpc":
        return CHANNEL_PPC

    if (
        group in SEO_CHANNEL_GROUPS
        or source.lower() == "search.brave.com"
        or _GOOGLE_PLACES_SOURCE_RE.search(source)
    ):
        return CHANNEL_SEO

    if group == "Direct":
        return CHANNEL_DIRECT

    return CHANNEL_OTHER


def _year_chunks(start: date, end: date) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    for year in range(start.year, end.year + 1):
        chunk_start = max(start, date(year, 1, 1))
        chunk_end = min(end, date(year, 12, 31))
        chunks.append((_format_iso_date(chunk_start), _format_iso_date(chunk_end)))
    return chunks


_EVENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def parse_conversion_event_names(raw: str | None, *, default: str = "purchase") -> list[str]:
    """
    Extract GA4 event names from a comma-separated spec.

    Accepts an optional series label encoded as ``event:Label`` on the first
    token (and legacy per-event ``event:Label`` / ``event=Label``) and ignores
    labels for the Data API pull. Multiple events are summed into one
    conversions metric.
    """
    text = (raw or "").strip() or default
    names: list[str] = []
    seen: set[str] = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if ":" in token:
            event_part = token.split(":", 1)[0]
        elif "=" in token:
            event_part = token.split("=", 1)[0]
        else:
            event_part = token
        event_name = event_part.strip()
        if not event_name or not _EVENT_NAME_RE.match(event_name):
            raise ValueError(
                "Conversion event must start with a letter and use letters, numbers, underscores, or hyphens"
            )
        if event_name in seen:
            continue
        seen.add(event_name)
        names.append(event_name)
    return names or [default]


def fetch_daily_traffic_rows(
    property_id: str,
    start_date: str,
    end_date: str,
    *,
    conversion_event_name: str = "purchase",
) -> list[tuple[str, str, int, int]]:
    """
    Return rows of (iso_date, classified_channel, sessions, conversions).

    Fetches ``date × sessionSource × sessionMedium × sessionDefaultChannelGroup``
    in calendar-year chunks with pagination, then applies ``classify_channel``.
    ``purchase`` uses ``ecommercePurchases``. Other events use a separate
    ``eventName × eventCount`` report so sessions are not duplicated. When
    multiple events are listed (comma-separated, optional ``event:Label``),
    their counts are summed into the conversions column.
    """
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )

    pid = normalize_property_id(property_id)
    prop = f"properties/{pid}"
    client = _ga4_client_with_scopes()

    start_d = _parse_iso_date(start_date)
    end_d = _parse_iso_date(end_date)
    chunks = _year_chunks(start_d, end_d)
    event_names = parse_conversion_event_names(conversion_event_name)
    use_ecommerce_purchases = "purchase" in event_names
    custom_events = [name for name in event_names if name != "purchase"]

    ga4_log(
        f"channel_export: property={pid} dims=source/medium/defaultChannelGroup "
        f"{start_date}→{end_date} ({len(chunks)} year chunk(s)); "
        f"conversions={','.join(event_names)}"
    )

    # Aggregate early by (date, classified_channel) to keep memory down.
    agg: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    raw_rows = 0

    for chunk_start, chunk_end in chunks:
        dr = [DateRange(start_date=chunk_start, end_date=chunk_end, name="range")]

        def request_factory(offset: int) -> RunReportRequest:
            return RunReportRequest(
                property=prop,
                date_ranges=dr,
                dimensions=[
                    Dimension(name="date"),
                    Dimension(name="sessionSource"),
                    Dimension(name="sessionMedium"),
                    Dimension(name="sessionDefaultChannelGroup"),
                ],
                metrics=[
                    Metric(name="sessions"),
                    *([Metric(name="ecommercePurchases")] if use_ecommerce_purchases else []),
                ],
                limit=100_000,
                offset=offset,
            )

        for row in _paginate_run_report(
            client,
            request_factory,
            label=f"daily_traffic_{chunk_start}_{chunk_end}",
        ):
            dims = [d.value for d in row.dimension_values]
            if len(dims) < 4:
                continue
            iso_date = _ga4_date_to_iso(dims[0])
            source = (dims[1] or "").strip() or "(direct)"
            medium = (dims[2] or "").strip() or "(none)"
            group = (dims[3] or "").strip() or "(not set)"
            channel = classify_channel(source, medium, group)
            sessions = _row_metric_int(row, 0)
            purchases = _row_metric_int(row, 1) if use_ecommerce_purchases else 0
            agg[(iso_date, channel)][0] += sessions
            agg[(iso_date, channel)][1] += purchases
            raw_rows += 1

        if custom_events:
            from google.analytics.data_v1beta.types import Filter, FilterExpression

            for event_name in custom_events:

                def conversion_request_factory(
                    offset: int,
                    *,
                    _event_name: str = event_name,
                ) -> RunReportRequest:
                    return RunReportRequest(
                        property=prop,
                        date_ranges=dr,
                        dimensions=[
                            Dimension(name="date"),
                            Dimension(name="sessionSource"),
                            Dimension(name="sessionMedium"),
                            Dimension(name="sessionDefaultChannelGroup"),
                            Dimension(name="eventName"),
                        ],
                        metrics=[Metric(name="eventCount")],
                        dimension_filter=FilterExpression(
                            filter=Filter(
                                field_name="eventName",
                                string_filter=Filter.StringFilter(
                                    match_type=Filter.StringFilter.MatchType.EXACT,
                                    value=_event_name,
                                    case_sensitive=True,
                                ),
                            )
                        ),
                        limit=100_000,
                        offset=offset,
                    )

                for row in _paginate_run_report(
                    client,
                    conversion_request_factory,
                    label=f"daily_conversion_{event_name}_{chunk_start}_{chunk_end}",
                ):
                    dims = [d.value for d in row.dimension_values]
                    if len(dims) < 5 or dims[4] != event_name:
                        continue
                    iso_date = _ga4_date_to_iso(dims[0])
                    source = (dims[1] or "").strip() or "(direct)"
                    medium = (dims[2] or "").strip() or "(none)"
                    group = (dims[3] or "").strip() or "(not set)"
                    channel = classify_channel(source, medium, group)
                    agg[(iso_date, channel)][1] += _row_metric_int(row, 0)
                    raw_rows += 1

    out = [
        (iso_date, channel, sessions, purchases)
        for (iso_date, channel), (sessions, purchases) in sorted(agg.items())
    ]
    ga4_log(
        f"channel_export: fetched {raw_rows} raw row(s) → "
        f"{len(out)} date×channel aggregate(s)"
    )
    return out


def build_long_rows(
    raw_rows: list[tuple[str, str, int, int]],
) -> list[dict[str, int | str]]:
    long_rows: list[dict[str, int | str]] = []
    for iso_date, channel, sessions, purchases in raw_rows:
        long_rows.append(
            {
                "date": iso_date,
                "channel_group": channel,
                "sessions": sessions,
                "purchases": purchases,
            }
        )
    return long_rows


def build_wide_rows(
    raw_rows: list[tuple[str, str, int, int]],
    *,
    start_date: str,
    end_date: str,
) -> tuple[list[str], list[dict[str, int | str]]]:
    by_date_channel: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for iso_date, channel, sessions, purchases in raw_rows:
        by_date_channel[(iso_date, channel)][0] += sessions
        by_date_channel[(iso_date, channel)][1] += purchases

    channels = list(CHANNEL_ORDER)
    start_d = _parse_iso_date(start_date)
    end_d = _parse_iso_date(end_date)
    all_dates: list[str] = []
    cursor = start_d
    while cursor <= end_d:
        all_dates.append(_format_iso_date(cursor))
        cursor += timedelta(days=1)

    # Column-safe prefixes for wide CSV.
    prefix_map = {
        CHANNEL_AI: "AI",
        CHANNEL_PPC: "PPC",
        CHANNEL_SEO: "SEO",
        CHANNEL_DIRECT: "Direct",
        CHANNEL_OTHER: "Other_Traffic",
    }
    purchase_cols = [f"{prefix_map[ch]}_purchases" for ch in channels]
    session_cols = [f"{prefix_map[ch]}_sessions" for ch in channels]
    fieldnames = ["date", "Ecommerce_purchases", "Sessions", *purchase_cols, *session_cols]

    wide_rows: list[dict[str, int | str]] = []
    for iso_date in all_dates:
        row: dict[str, int | str] = {"date": iso_date}
        total_sessions = 0
        total_purchases = 0
        for ch in channels:
            sessions, purchases = by_date_channel.get((iso_date, ch), [0, 0])
            prefix = prefix_map[ch]
            row[f"{prefix}_sessions"] = sessions
            row[f"{prefix}_purchases"] = purchases
            total_sessions += sessions
            total_purchases += purchases
        row["Ecommerce_purchases"] = total_purchases
        row["Sessions"] = total_sessions
        wide_rows.append(row)

    return fieldnames, wide_rows


def build_weekly_rows(
    raw_rows: list[tuple[str, str, int, int]],
    *,
    property_name: str,
    property_id: str,
    start_date: str,
    end_date: str,
) -> tuple[list[str], list[dict[str, int | str]]]:
    """Sunday-start weekly wide rows with sessions + purchases by classified channel."""
    by_week_channel: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for iso_date, channel, sessions, purchases in raw_rows:
        week = _format_iso_date(sunday_week_start(_parse_iso_date(iso_date)))
        by_week_channel[(week, channel)][0] += sessions
        by_week_channel[(week, channel)][1] += purchases

    start_d = _parse_iso_date(start_date)
    end_d = _parse_iso_date(end_date)
    weeks: list[str] = []
    cursor = sunday_week_start(start_d)
    last_week = sunday_week_start(end_d)
    while cursor <= last_week:
        weeks.append(_format_iso_date(cursor))
        cursor += timedelta(days=7)

    prefix_map = {
        CHANNEL_AI: "ai",
        CHANNEL_PPC: "ppc",
        CHANNEL_SEO: "seo",
        CHANNEL_DIRECT: "direct",
        CHANNEL_OTHER: "other_traffic",
    }
    # Keep in sync with CHANNEL_ORDER / prefix_map (was missing direct_* and broke DictWriter).
    channel_fields = [
        col
        for ch in CHANNEL_ORDER
        for col in (f"{prefix_map[ch]}_sessions", f"{prefix_map[ch]}_purchases")
    ]
    fieldnames = [
        "property_name",
        "property_id",
        "week",
        "sessions",
        "purchases",
        *channel_fields,
    ]

    weekly_rows: list[dict[str, int | str]] = []
    for week in weeks:
        row: dict[str, int | str] = {
            "property_name": property_name,
            "property_id": property_id,
            "week": week,
        }
        total_sessions = 0
        total_purchases = 0
        for ch in CHANNEL_ORDER:
            sessions, purchases = by_week_channel.get((week, ch), [0, 0])
            pfx = prefix_map[ch]
            row[f"{pfx}_sessions"] = sessions
            row[f"{pfx}_purchases"] = purchases
            total_sessions += sessions
            total_purchases += purchases
        row["sessions"] = total_sessions
        row["purchases"] = total_purchases
        weekly_rows.append(row)

    return fieldnames, weekly_rows


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, int | str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    ga4_log(f"channel_export: wrote {path} ({len(rows)} row(s))")


def run_export(
    property_id: str,
    *,
    property_name: str,
    start_date: str = DEFAULT_START,
    end_date: str = DEFAULT_END,
    output_dir: Path | None = None,
    token_path: Path | None = None,
    force_login: bool = False,
    auth_code: str | None = None,
    conversion_event_name: str = "purchase",
) -> tuple[Path, Path, Path]:
    out_dir = (output_dir or GA4_DAILY).resolve()
    token_file = (token_path or _DEFAULT_CLI_TOKEN_PATH).expanduser().resolve()
    pid = normalize_property_id(property_id)

    creds = acquire_cli_credentials(
        token_path=token_file,
        force_login=force_login,
        auth_code=auth_code,
    )
    install_oauth_application_default_credentials(creds, token_path=token_file)

    iso_start, iso_end = resolve_export_dates(start_date, end_date)

    raw = fetch_daily_traffic_rows(
        pid,
        iso_start,
        iso_end,
        conversion_event_name=conversion_event_name,
    )
    long_rows = build_long_rows(raw)
    wide_fieldnames, wide_rows = build_wide_rows(raw, start_date=iso_start, end_date=iso_end)
    weekly_fieldnames, weekly_rows = build_weekly_rows(
        raw,
        property_name=property_name,
        property_id=pid,
        start_date=iso_start,
        end_date=iso_end,
    )

    long_path, wide_path, weekly_path = export_output_paths(
        out_dir,
        property_name=property_name,
        property_id=pid,
    )

    write_csv(
        long_path,
        ["date", "channel_group", "sessions", "purchases"],
        long_rows,
    )
    write_csv(wide_path, wide_fieldnames, wide_rows)
    write_csv(weekly_path, weekly_fieldnames, weekly_rows)

    # Channel totals for a quick sanity check
    totals: dict[str, int] = defaultdict(int)
    for _, channel, sessions, _ in raw:
        totals[channel] += sessions

    print(f"Property: {property_name} ({pid})")
    print("Channel rules: AI → PPC → SEO → Direct → Other Traffic")
    print(f"Date range: {iso_start} → {iso_end}")
    print(f"Conversion event: {conversion_event_name}")
    print(f"Long CSV:    {long_path}")
    print(f"Wide CSV:    {wide_path}")
    print(f"Weekly CSV:  {weekly_path}")
    print(f"Days: {len(wide_rows):,}  |  long rows: {len(long_rows):,}  |  weeks: {len(weekly_rows):,}")
    print(
        "Session totals: "
        + ", ".join(f"{ch}={totals.get(ch, 0):,}" for ch in CHANNEL_ORDER)
    )
    return long_path, wide_path, weekly_path


def main() -> None:
    load_app_environment()

    parser = argparse.ArgumentParser(
        description=(
            "Export daily/weekly GA4 sessions and purchases by AI/PPC/SEO/Direct/Other Traffic rules."
        )
    )
    parser.add_argument(
        "--property-id",
        default=None,
        metavar="ID",
        help="GA4 numeric property id (overrides GA4_PROPERTY_ID env)",
    )
    parser.add_argument(
        "--property-name",
        default=None,
        metavar="NAME",
        help="Label for output filenames (overrides GA4_PROPERTY_NAME env; default: property id)",
    )
    parser.add_argument(
        "--start-date",
        default=_env_iso_date("GA4_START_DATE", DEFAULT_START),
        help=f"Start date YYYY-MM-DD (default: {DEFAULT_START}; ignores GA4_START_DATE if relative)",
    )
    parser.add_argument(
        "--end-date",
        default=_env_iso_date("GA4_END_DATE", DEFAULT_END),
        help=f"End date YYYY-MM-DD (default: {DEFAULT_END}; ignores GA4_END_DATE if relative)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=GA4_DAILY,
        help="Directory for output CSV files",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Force a new Google OAuth browser login (ignore cached token)",
    )
    parser.add_argument(
        "--auth-code",
        default=None,
        help="OAuth authorization code (or full redirect URL) if redirect URI is remote",
    )
    parser.add_argument(
        "--token-path",
        type=Path,
        default=_DEFAULT_CLI_TOKEN_PATH,
        help=f"Path for cached OAuth token (default: {_DEFAULT_CLI_TOKEN_PATH})",
    )
    parser.add_argument(
        "--conversion-event",
        default=(os.environ.get("GA4_CONVERSION_EVENT_NAME") or "purchase").strip(),
        help=(
            "GA4 conversion event(s), comma-separated; optional event:Label "
            "(default: purchase). Multiple events are summed."
        ),
    )
    args = parser.parse_args()

    property_id = resolve_property_id(args.property_id)
    property_name = resolve_property_name(args.property_name, property_id)

    run_export(
        property_id,
        property_name=property_name,
        start_date=args.start_date,
        end_date=args.end_date,
        output_dir=args.output_dir,
        token_path=args.token_path,
        force_login=args.login,
        auth_code=args.auth_code,
        conversion_event_name=args.conversion_event,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Pull daily GA4 sessions and ecommerce purchases by channel group via the Data API.

Uses GA4's **default** session channel group (``sessionDefaultChannelGroup``): Direct,
Organic Search, Paid Search, Paid Social, Organic Social, Email, Affiliates, Referral,
Video, and Display. This script does not use custom Admin channel groupings.

Auth: Google OAuth — same web flow as the deployed FastAPI app on Cloud Run
(``GET /api/ga4/login`` → Google consent → ``/api/ga4/callback``).

Uses ``ga4_oauth.build_flow`` with redirect URI ``{WEB_PUBLIC_ORIGIN}/api/ga4/callback``
(or ``GA4_OAUTH_REDIRECT_URI`` when set, as on Cloud Run). Tokens cache to
``research/.ga4_oauth_token.json``.

Configure via env (see ``env/.env.development``) or ``.streamlit/secrets.toml`` —
same OAuth client as the deployed app.

Environment:
  WEB_PUBLIC_ORIGIN                    App origin (default: http://localhost:5173)
  GA4_OAUTH_REDIRECT_URI               Override callback (Cloud Run sets this to …/api/ga4/callback)
  GA4_PROPERTY_ID / GA4_PROPERTY_NAME        Fallback when CLI flags omitted
  GA4_START_DATE / GA4_END_DATE        Override default YYYY-MM-DD range (ISO only)
  GA4_OAUTH_CLIENT_ID / GA4_OAUTH_CLIENT_SECRET

Default date range: 2022-06-01 through 2026-05-31.

Outputs (in this directory by default):
  ga4_channel_long_{name}_{property_id}.csv
  ga4_channel_wide_{name}_{property_id}.csv
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

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_ROOT = _REPO_ROOT / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

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
CHANNEL_DIMENSION = "sessionDefaultChannelGroup"

# GA4 default session channel group labels → wide CSV column prefixes.
DEFAULT_CHANNEL_GROUPS: tuple[str, ...] = (
    "Direct",
    "Organic Search",
    "Paid Search",
    "Paid Social",
    "Organic Social",
    "Email",
    "Affiliates",
    "Referral",
    "Video",
    "Display",
)

_CHANNEL_LABEL_TO_PREFIX: dict[str, str] = {
    label.lower(): re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
    for label in DEFAULT_CHANNEL_GROUPS
}
_CHANNEL_LABEL_TO_PREFIX["(not set)"] = "Unassigned"

WIDE_CHANNEL_PREFIXES: tuple[str, ...] = tuple(
    _CHANNEL_LABEL_TO_PREFIX[label.lower()] for label in DEFAULT_CHANNEL_GROUPS
) + ("Unassigned",)


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
) -> tuple[Path, Path]:
    slug = _filename_slug(property_name)
    pid = normalize_property_id(property_id)
    stem = f"ga4_channel_{{kind}}_{slug}_{pid}.csv"
    return (
        output_dir / stem.format(kind="long"),
        output_dir / stem.format(kind="wide"),
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


def _channel_column_prefix(channel: str) -> str:
    label = (channel or "").strip() or "(not set)"
    mapped = _CHANNEL_LABEL_TO_PREFIX.get(label.lower())
    if mapped:
        return mapped
    safe = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
    return safe or "Other"


def _year_chunks(start: date, end: date) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    for year in range(start.year, end.year + 1):
        chunk_start = max(start, date(year, 1, 1))
        chunk_end = min(end, date(year, 12, 31))
        chunks.append((_format_iso_date(chunk_start), _format_iso_date(chunk_end)))
    return chunks


def fetch_daily_channel_metrics(
    property_id: str,
    channel_dim: str,
    start_date: str,
    end_date: str,
) -> list[tuple[str, str, int, int]]:
    """
    Return rows of (iso_date, channel_group, sessions, purchases) for the range.
    Fetches calendar-year chunks with pagination.
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

    ga4_log(
        f"channel_export: property={pid} dim={channel_dim!r} "
        f"{start_date}→{end_date} ({len(chunks)} year chunk(s))"
    )

    out: list[tuple[str, str, int, int]] = []
    dim_label = channel_dim.replace(":", "_")

    for chunk_start, chunk_end in chunks:
        dr = [DateRange(start_date=chunk_start, end_date=chunk_end, name="range")]

        def request_factory(offset: int) -> RunReportRequest:
            return RunReportRequest(
                property=prop,
                date_ranges=dr,
                dimensions=[
                    Dimension(name="date"),
                    Dimension(name=channel_dim),
                ],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="ecommercePurchases"),
                ],
                limit=100_000,
                offset=offset,
            )

        for row in _paginate_run_report(
            client,
            request_factory,
            label=f"daily_channel_{dim_label}_{chunk_start}_{chunk_end}",
        ):
            dims = [d.value for d in row.dimension_values]
            if len(dims) < 2:
                continue
            iso_date = _ga4_date_to_iso(dims[0])
            channel = (dims[1] or "").strip() or "(not set)"
            sessions = _row_metric_int(row, 0)
            purchases = _row_metric_int(row, 1)
            out.append((iso_date, channel, sessions, purchases))

    ga4_log(f"channel_export: fetched {len(out)} raw row(s)")
    return out


def _wide_channel_columns(seen_prefixes: set[str]) -> list[str]:
    channels = list(WIDE_CHANNEL_PREFIXES)
    if "Other" in seen_prefixes:
        channels.append("Other")
    return channels


def build_long_rows(
    raw_rows: list[tuple[str, str, int, int]],
) -> list[dict[str, int | str]]:
    agg: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for iso_date, channel, sessions, purchases in raw_rows:
        key = (iso_date, channel)
        agg[key][0] += sessions
        agg[key][1] += purchases

    long_rows: list[dict[str, int | str]] = []
    for (iso_date, channel), (sessions, purchases) in sorted(agg.items()):
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
    channel_prefixes: dict[str, str] = {}
    prefix_counts: dict[str, int] = defaultdict(int)

    for iso_date, channel, sessions, purchases in raw_rows:
        prefix = _channel_column_prefix(channel)
        prefix_counts[prefix] += 1
        channel_prefixes[channel] = prefix
        by_date_channel[(iso_date, prefix)][0] += sessions
        by_date_channel[(iso_date, prefix)][1] += purchases

    seen_prefixes = set(channel_prefixes.values())
    channels = _wide_channel_columns(seen_prefixes)

    start_d = _parse_iso_date(start_date)
    end_d = _parse_iso_date(end_date)
    all_dates: list[str] = []
    cursor = start_d
    while cursor <= end_d:
        all_dates.append(_format_iso_date(cursor))
        cursor += timedelta(days=1)

    purchase_cols = [f"{ch}_purchases" for ch in channels]
    session_cols = [f"{ch}_sessions" for ch in channels]
    fieldnames = ["date", "Ecommerce_purchases", "Sessions", *purchase_cols, *session_cols]

    wide_rows: list[dict[str, int | str]] = []
    for iso_date in all_dates:
        row: dict[str, int | str] = {"date": iso_date}
        total_sessions = 0
        total_purchases = 0
        per_ch_sessions: dict[str, int] = defaultdict(int)
        per_ch_purchases: dict[str, int] = defaultdict(int)

        for ch in channels:
            sessions, purchases = by_date_channel.get((iso_date, ch), [0, 0])
            per_ch_sessions[ch] = sessions
            per_ch_purchases[ch] = purchases
            total_sessions += sessions
            total_purchases += purchases

        row["Ecommerce_purchases"] = total_purchases
        row["Sessions"] = total_sessions
        for ch in channels:
            row[f"{ch}_purchases"] = per_ch_purchases[ch]
            row[f"{ch}_sessions"] = per_ch_sessions[ch]
        wide_rows.append(row)

    return fieldnames, wide_rows


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
) -> tuple[Path, Path]:
    out_dir = (output_dir or Path(__file__).resolve().parent).resolve()
    token_file = (token_path or _DEFAULT_CLI_TOKEN_PATH).expanduser().resolve()
    pid = normalize_property_id(property_id)

    creds = acquire_cli_credentials(
        token_path=token_file,
        force_login=force_login,
        auth_code=auth_code,
    )
    install_oauth_application_default_credentials(creds, token_path=token_file)

    iso_start, iso_end = resolve_export_dates(start_date, end_date)

    raw = fetch_daily_channel_metrics(pid, CHANNEL_DIMENSION, iso_start, iso_end)
    long_rows = build_long_rows(raw)
    wide_fieldnames, wide_rows = build_wide_rows(raw, start_date=iso_start, end_date=iso_end)

    long_path, wide_path = export_output_paths(
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

    print(f"Property: {property_name} ({pid})")
    print(f"Channel dimension: {CHANNEL_DIMENSION}")
    print(f"Date range: {iso_start} → {iso_end}")
    print(f"Long CSV:  {long_path}")
    print(f"Wide CSV:  {wide_path}")
    print(f"Days: {len(wide_rows):,}  |  long rows: {len(long_rows):,}")
    return long_path, wide_path


def main() -> None:
    load_app_environment()

    parser = argparse.ArgumentParser(
        description="Export daily GA4 sessions and purchases by channel group."
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
        default=Path(__file__).resolve().parent,
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
    )


if __name__ == "__main__":
    main()

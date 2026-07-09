#!/usr/bin/env python3
"""
Pull GA4 sessions for Paid Search + Cross-network campaigns, split Branded vs Non-Branded PPC.

Campaigns whose ``sessionCampaignName`` contains ``brand`` (any case) count as **Branded PPC**;
all other campaigns in those channel groups sum to **Non-Branded PPC**.

Weekly buckets use **Sunday week start** (same convention as Google Trends exports).

Auth: same OAuth CLI flow as ``ga4_channel_export.py`` (token cache: ``research/.ga4_oauth_token.json``).

Usage::

    # Single property
    python research/ga4/ga4_ppc_brand_export.py --property-id 250460017 --property-name good_food

    # Batch from CSV (columns: property_name, ga4_property_id)
    python research/ga4/ga4_ppc_brand_export.py --properties-file research/config/ga4_ppc_properties.csv

Outputs (default: ``research/ga4/exports/ppc/``):
  ga4_ppc_brand_weekly_{name}_{id}.csv              — one property
  ga4_ppc_brand_weekly_combined.csv                 — all properties from --properties-file
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
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

from paths import GA4_PPC, GA4_PROPERTIES  # noqa: E402

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
PPC_CHANNEL_GROUPS = ("Paid Search", "Cross-network")
CHANNEL_DIMENSION = "sessionDefaultChannelGroup"
CAMPAIGN_DIMENSION = "sessionCampaignName"

WEEKLY_FIELDNAMES = [
    "property_name",
    "property_id",
    "week",
    "branded_ppc_sessions",
    "non_branded_ppc_sessions",
    "total_ppc_sessions",
]


@dataclass(frozen=True)
class PropertySpec:
    property_name: str
    property_id: str


def _filename_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").lower()
    return slug or "property"


def resolve_property_id(cli_value: str | None) -> str:
    if cli_value and str(cli_value).strip():
        return normalize_property_id(str(cli_value))
    env_val = (os.environ.get("GA4_PROPERTY_ID") or "").strip()
    if env_val:
        return normalize_property_id(env_val)
    raise SystemExit("GA4 property id required: pass --property-id or set GA4_PROPERTY_ID")


def resolve_property_name(cli_value: str | None, property_id: str) -> str:
    if cli_value and str(cli_value).strip():
        return str(cli_value).strip()
    env_val = (os.environ.get("GA4_PROPERTY_NAME") or "").strip()
    if env_val:
        return env_val
    return property_id


def _parse_iso_date(value: str) -> date:
    y, m, d = (int(x) for x in value.split("-"))
    return date(y, m, d)


def _format_iso_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def sunday_week_start(d: date) -> date:
    """Sunday-start week label (matches Google Trends ``Week`` column)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def week_starts_in_range(start: date, end: date) -> list[str]:
    """All Sunday week-start dates overlapping ``start``…``end``."""
    cursor = sunday_week_start(start)
    weeks: list[str] = []
    while cursor <= end:
        weeks.append(_format_iso_date(cursor))
        cursor += timedelta(days=7)
    return weeks


def _ga4_api_date_to_iso(value: str, *, reference: date | None = None) -> str:
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
    iso_start = _ga4_api_date_to_iso(start or DEFAULT_START)
    iso_end = _ga4_api_date_to_iso(end or DEFAULT_END)
    if iso_start > iso_end:
        raise ValueError(f"Start date {iso_start} is after end date {iso_end}")
    return iso_start, iso_end


def _env_iso_date(name: str, fallback: str) -> str:
    raw = (os.environ.get(name) or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    return fallback


def _year_chunks(start: date, end: date) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    for year in range(start.year, end.year + 1):
        chunk_start = max(start, date(year, 1, 1))
        chunk_end = min(end, date(year, 12, 31))
        chunks.append((_format_iso_date(chunk_start), _format_iso_date(chunk_end)))
    return chunks


def _is_branded_campaign(campaign_name: str) -> bool:
    return "brand" in (campaign_name or "").lower()


def _channel_filter_expression():
    from google.analytics.data_v1beta.types import Filter, FilterExpression

    return FilterExpression(
        filter=Filter(
            field_name=CHANNEL_DIMENSION,
            in_list_filter=Filter.InListFilter(values=list(PPC_CHANNEL_GROUPS)),
        )
    )


def _ga4_date_to_iso(raw: str) -> str:
    s = (raw or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def fetch_campaign_sessions_by_day(
    property_id: str,
    start_date: str,
    end_date: str,
) -> list[tuple[str, str, int]]:
    """Return rows of (iso_date, sessionCampaignName, sessions)."""
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )

    pid = normalize_property_id(property_id)
    prop = f"properties/{pid}"
    client = _ga4_client_with_scopes()
    dim_filter = _channel_filter_expression()

    start_d = _parse_iso_date(start_date)
    end_d = _parse_iso_date(end_date)
    chunks = _year_chunks(start_d, end_d)

    ga4_log(
        f"ppc_brand_export: property={pid} channels={PPC_CHANNEL_GROUPS!r} "
        f"{start_date}→{end_date} ({len(chunks)} year chunk(s))"
    )

    out: list[tuple[str, str, int]] = []

    for chunk_start, chunk_end in chunks:
        dr = [DateRange(start_date=chunk_start, end_date=chunk_end, name="range")]

        def request_factory(offset: int) -> RunReportRequest:
            return RunReportRequest(
                property=prop,
                date_ranges=dr,
                dimensions=[
                    Dimension(name="date"),
                    Dimension(name=CAMPAIGN_DIMENSION),
                ],
                metrics=[Metric(name="sessions")],
                dimension_filter=dim_filter,
                limit=100_000,
                offset=offset,
            )

        for row in _paginate_run_report(
            client,
            request_factory,
            label=f"ppc_brand_{chunk_start}_{chunk_end}",
        ):
            dims = [d.value for d in row.dimension_values]
            if len(dims) < 2:
                continue
            iso_date = _ga4_date_to_iso(dims[0])
            campaign = (dims[1] or "").strip() or "(not set)"
            sessions = _row_metric_int(row, 0)
            out.append((iso_date, campaign, sessions))

    ga4_log(f"ppc_brand_export: fetched {len(out)} daily row(s)")
    return out


def aggregate_weekly_branded_ppc(
    raw_rows: list[tuple[str, str, int]],
    *,
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, int]]:
    """Aggregate daily rows to Sunday-start weeks; fill missing weeks with zero."""
    start_d = _parse_iso_date(start_date)
    end_d = _parse_iso_date(end_date)
    weekly: dict[str, dict[str, int]] = {
        w: {"branded_ppc_sessions": 0, "non_branded_ppc_sessions": 0}
        for w in week_starts_in_range(start_d, end_d)
    }

    for iso_date, campaign, sessions in raw_rows:
        d = _parse_iso_date(iso_date)
        if d < start_d or d > end_d:
            continue
        week = _format_iso_date(sunday_week_start(d))
        if week not in weekly:
            weekly[week] = {"branded_ppc_sessions": 0, "non_branded_ppc_sessions": 0}
        key = (
            "branded_ppc_sessions"
            if _is_branded_campaign(campaign)
            else "non_branded_ppc_sessions"
        )
        weekly[week][key] += sessions

    return weekly


def weekly_rows_for_property(
    spec: PropertySpec,
    weekly: dict[str, dict[str, int]],
) -> list[dict[str, int | str]]:
    pid = normalize_property_id(spec.property_id)
    rows: list[dict[str, int | str]] = []
    for week in sorted(weekly):
        b = weekly[week]["branded_ppc_sessions"]
        nb = weekly[week]["non_branded_ppc_sessions"]
        rows.append(
            {
                "property_name": spec.property_name,
                "property_id": pid,
                "week": week,
                "branded_ppc_sessions": b,
                "non_branded_ppc_sessions": nb,
                "total_ppc_sessions": b + nb,
            }
        )
    return rows


def load_properties_file(path: Path) -> list[PropertySpec]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"Properties file not found: {path}")

    specs: list[PropertySpec] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise SystemExit(f"Properties file is empty: {path}")

        fields = {f.strip().lower(): f for f in reader.fieldnames if f}
        name_col = fields.get("property_name") or fields.get("name") or fields.get("site")
        id_col = (
            fields.get("ga4_property_id")
            or fields.get("property_id")
            or fields.get("ga4_property")
        )
        if not name_col or not id_col:
            raise SystemExit(
                f"{path} needs columns property_name and ga4_property_id "
                f"(found: {reader.fieldnames})"
            )

        for i, row in enumerate(reader, start=2):
            name = (row.get(name_col) or "").strip()
            pid = (row.get(id_col) or "").strip()
            if not name and not pid:
                continue
            if not name or not pid:
                raise SystemExit(f"{path}:{i}: both property_name and ga4_property_id required")
            specs.append(PropertySpec(property_name=name, property_id=normalize_property_id(pid)))

    if not specs:
        raise SystemExit(f"No properties in {path}")
    return specs


def write_weekly_csv(path: Path, rows: list[dict[str, int | str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=WEEKLY_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    ga4_log(f"ppc_brand_export: wrote {path} ({len(rows)} row(s))")


def weekly_output_path(output_dir: Path, spec: PropertySpec) -> Path:
    slug = _filename_slug(spec.property_name)
    pid = normalize_property_id(spec.property_id)
    return output_dir / f"ga4_ppc_brand_weekly_{slug}_{pid}.csv"


def fetch_weekly_for_property(
    spec: PropertySpec,
    *,
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, int]]:
    raw = fetch_campaign_sessions_by_day(spec.property_id, start_date, end_date)
    return aggregate_weekly_branded_ppc(raw, start_date=start_date, end_date=end_date)


def run_exports(
    specs: list[PropertySpec],
    *,
    start_date: str = DEFAULT_START,
    end_date: str = DEFAULT_END,
    output_dir: Path | None = None,
    token_path: Path | None = None,
    force_login: bool = False,
    auth_code: str | None = None,
    combined: bool = True,
) -> list[Path]:
    out_dir = (output_dir or GA4_PPC).resolve()
    token_file = (token_path or _DEFAULT_CLI_TOKEN_PATH).expanduser().resolve()

    creds = acquire_cli_credentials(
        token_path=token_file,
        force_login=force_login,
        auth_code=auth_code,
    )
    install_oauth_application_default_credentials(creds, token_path=token_file)

    iso_start, iso_end = resolve_export_dates(start_date, end_date)
    written: list[Path] = []
    combined_rows: list[dict[str, int | str]] = []

    for spec in specs:
        print(f"\n── {spec.property_name} ({normalize_property_id(spec.property_id)}) ──")
        weekly = fetch_weekly_for_property(spec, start_date=iso_start, end_date=iso_end)
        rows = weekly_rows_for_property(spec, weekly)
        path = weekly_output_path(out_dir, spec)
        write_weekly_csv(path, rows)
        written.append(path)

        branded = sum(int(r["branded_ppc_sessions"]) for r in rows)
        non_branded = sum(int(r["non_branded_ppc_sessions"]) for r in rows)
        print(f"  Weeks: {len(rows)}  |  Branded PPC: {branded:,}  |  Non-Branded PPC: {non_branded:,}")
        print(f"  {path}")

        combined_rows.extend(rows)

    if combined and len(specs) > 1:
        combined_path = out_dir / "ga4_ppc_brand_weekly_combined.csv"
        write_weekly_csv(combined_path, combined_rows)
        written.append(combined_path)
        print(f"\nCombined CSV: {combined_path} ({len(combined_rows)} row(s))")

    print(f"\nDate range: {iso_start} → {iso_end}  (week starts Sunday, Google Trends convention)")
    print(f"Channel groups: {', '.join(PPC_CHANNEL_GROUPS)}")
    return written


def main() -> None:
    load_app_environment()

    parser = argparse.ArgumentParser(
        description="Export weekly GA4 Branded vs Non-Branded PPC sessions (Sunday week start)."
    )
    parser.add_argument(
        "--property-id",
        default=None,
        metavar="ID",
        help="GA4 numeric property id (single-property mode)",
    )
    parser.add_argument(
        "--property-name",
        default=None,
        metavar="NAME",
        help="Property label (single-property mode)",
    )
    parser.add_argument(
        "--properties-file",
        type=Path,
        default=None,
        metavar="CSV",
        help=f"CSV with property_name, ga4_property_id (default when batch: {GA4_PROPERTIES})",
    )
    parser.add_argument(
        "--start-date",
        default=_env_iso_date("GA4_START_DATE", DEFAULT_START),
        help=f"Start date YYYY-MM-DD (default: {DEFAULT_START})",
    )
    parser.add_argument(
        "--end-date",
        default=_env_iso_date("GA4_END_DATE", DEFAULT_END),
        help=f"End date YYYY-MM-DD (default: {DEFAULT_END})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=GA4_PPC,
        help="Directory for output CSV files",
    )
    parser.add_argument(
        "--no-combined",
        action="store_true",
        help="With --properties-file, skip writing ga4_ppc_brand_weekly_combined.csv",
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

    properties_file = args.properties_file
    if (
        properties_file is None
        and not (args.property_id or os.environ.get("GA4_PROPERTY_ID"))
        and GA4_PROPERTIES.is_file()
    ):
        properties_file = GA4_PROPERTIES

    if properties_file:
        specs = load_properties_file(properties_file)
    elif args.property_id or os.environ.get("GA4_PROPERTY_ID"):
        pid = resolve_property_id(args.property_id)
        name = resolve_property_name(args.property_name, pid)
        specs = [PropertySpec(property_name=name, property_id=pid)]
    else:
        raise SystemExit(
            "Provide --properties-file CSV or both --property-id and --property-name"
        )

    run_exports(
        specs,
        start_date=args.start_date,
        end_date=args.end_date,
        output_dir=args.output_dir,
        token_path=args.token_path,
        force_login=args.login,
        auth_code=args.auth_code,
        combined=not args.no_combined,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Pull weekly GA4 SEO sessions, Direct sessions, and ecommerce purchases per property.

Metrics (Sunday week start, same convention as Google Trends / ``ga4_ppc_brand_export.py``):
  - **seo_sessions** — ``Organic Search`` default channel group sessions
  - **direct_sessions** — ``Direct`` default channel group sessions
  - **ecommerce_purchases** — total ``ecommercePurchases`` count (summed across channels)

Auth: same OAuth CLI flow as ``ga4_channel_export.py`` (token cache: ``research/.ga4_oauth_token.json``).

Usage::

    # Single property
    python research/ga4/ga4_channel_weekly_export.py --property-id 250460017 --property-name good_food

    # Batch from CSV (columns: property_name, ga4_property_id)
    python research/ga4/ga4_channel_weekly_export.py --properties-file research/config/ga4_ppc_properties.csv

Outputs (default: ``research/ga4/exports/weekly/``):
  ga4_channel_weekly_{name}_{id}.csv              — one property
  ga4_channel_weekly_combined.csv                 — all properties from --properties-file
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_GA4_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _GA4_ROOT.parent.parent
_RESEARCH_ROOT = _GA4_ROOT.parent
_BACKEND_ROOT = _REPO_ROOT / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))
if str(_GA4_ROOT) not in sys.path:
    sys.path.insert(0, str(_GA4_ROOT))

from paths import GA4_WEEKLY, GA4_PROPERTIES  # noqa: E402

from ga4_data_api import ga4_log  # noqa: E402
from ga4_fetch import (  # noqa: E402
    _ga4_client_with_scopes,
    _paginate_run_report,
    _row_metric_int,
    normalize_property_id,
)
from ga4_oauth import (  # noqa: E402
    _DEFAULT_CLI_TOKEN_PATH,
    acquire_cli_credentials,
    install_oauth_application_default_credentials,
)
from geo_app_env import load_app_environment  # noqa: E402

from ga4_ppc_brand_export import (  # noqa: E402
    DEFAULT_END,
    DEFAULT_START,
    PropertySpec,
    _env_iso_date,
    _filename_slug,
    _format_iso_date,
    _ga4_date_to_iso,
    _parse_iso_date,
    _year_chunks,
    load_properties_file,
    resolve_export_dates,
    resolve_property_id,
    resolve_property_name,
    sunday_week_start,
    week_starts_in_range,
)

CHANNEL_DIMENSION = "sessionDefaultChannelGroup"
SEO_CHANNEL = "Organic Search"
DIRECT_CHANNEL = "Direct"

WEEKLY_FIELDNAMES = [
    "property_name",
    "property_id",
    "week",
    "seo_sessions",
    "direct_sessions",
    "ecommerce_purchases",
]


@dataclass(frozen=True)
class DailyChannelRow:
    iso_date: str
    channel: str
    sessions: int
    purchases: int


def fetch_channel_metrics_by_day(
    property_id: str,
    start_date: str,
    end_date: str,
) -> list[DailyChannelRow]:
    """Return daily rows by default channel group with sessions and purchases."""
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
        f"channel_weekly_export: property={pid} "
        f"{start_date}→{end_date} ({len(chunks)} year chunk(s))"
    )

    out: list[DailyChannelRow] = []

    for chunk_start, chunk_end in chunks:
        dr = [DateRange(start_date=chunk_start, end_date=chunk_end, name="range")]

        def request_factory(offset: int) -> RunReportRequest:
            return RunReportRequest(
                property=prop,
                date_ranges=dr,
                dimensions=[
                    Dimension(name="date"),
                    Dimension(name=CHANNEL_DIMENSION),
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
            label=f"channel_weekly_{chunk_start}_{chunk_end}",
        ):
            dims = [d.value for d in row.dimension_values]
            if len(dims) < 2:
                continue
            iso_date = _ga4_date_to_iso(dims[0])
            channel = (dims[1] or "").strip() or "(not set)"
            sessions = _row_metric_int(row, 0)
            purchases = _row_metric_int(row, 1)
            out.append(
                DailyChannelRow(
                    iso_date=iso_date,
                    channel=channel,
                    sessions=sessions,
                    purchases=purchases,
                )
            )

    ga4_log(f"channel_weekly_export: fetched {len(out)} daily row(s)")
    return out


def aggregate_weekly_channel_metrics(
    raw_rows: list[DailyChannelRow],
    *,
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, int]]:
    """Aggregate daily channel rows to Sunday-start weeks; fill missing weeks with zero."""
    start_d = _parse_iso_date(start_date)
    end_d = _parse_iso_date(end_date)
    weekly: dict[str, dict[str, int]] = {
        w: {"seo_sessions": 0, "direct_sessions": 0, "ecommerce_purchases": 0}
        for w in week_starts_in_range(start_d, end_d)
    }

    daily_purchases: dict[str, int] = defaultdict(int)

    for row in raw_rows:
        d = _parse_iso_date(row.iso_date)
        if d < start_d or d > end_d:
            continue

        week = _format_iso_date(sunday_week_start(d))
        if week not in weekly:
            weekly[week] = {
                "seo_sessions": 0,
                "direct_sessions": 0,
                "ecommerce_purchases": 0,
            }

        if row.channel == SEO_CHANNEL:
            weekly[week]["seo_sessions"] += row.sessions
        elif row.channel == DIRECT_CHANNEL:
            weekly[week]["direct_sessions"] += row.sessions

        daily_purchases[row.iso_date] += row.purchases

    for iso_date, purchases in daily_purchases.items():
        d = _parse_iso_date(iso_date)
        if d < start_d or d > end_d:
            continue
        week = _format_iso_date(sunday_week_start(d))
        if week in weekly:
            weekly[week]["ecommerce_purchases"] += purchases

    return weekly


def weekly_rows_for_property(
    spec: PropertySpec,
    weekly: dict[str, dict[str, int]],
) -> list[dict[str, int | str]]:
    pid = normalize_property_id(spec.property_id)
    rows: list[dict[str, int | str]] = []
    for week in sorted(weekly):
        bucket = weekly[week]
        rows.append(
            {
                "property_name": spec.property_name,
                "property_id": pid,
                "week": week,
                "seo_sessions": bucket["seo_sessions"],
                "direct_sessions": bucket["direct_sessions"],
                "ecommerce_purchases": bucket["ecommerce_purchases"],
            }
        )
    return rows


def write_weekly_csv(path: Path, rows: list[dict[str, int | str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=WEEKLY_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    ga4_log(f"channel_weekly_export: wrote {path} ({len(rows)} row(s))")


def weekly_output_path(output_dir: Path, spec: PropertySpec) -> Path:
    slug = _filename_slug(spec.property_name)
    pid = normalize_property_id(spec.property_id)
    return output_dir / f"ga4_channel_weekly_{slug}_{pid}.csv"


def fetch_weekly_for_property(
    spec: PropertySpec,
    *,
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, int]]:
    raw = fetch_channel_metrics_by_day(spec.property_id, start_date, end_date)
    return aggregate_weekly_channel_metrics(raw, start_date=start_date, end_date=end_date)


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
    out_dir = (output_dir or GA4_WEEKLY).resolve()
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

        seo = sum(int(r["seo_sessions"]) for r in rows)
        direct = sum(int(r["direct_sessions"]) for r in rows)
        purchases = sum(int(r["ecommerce_purchases"]) for r in rows)
        print(
            f"  Weeks: {len(rows)}  |  SEO: {seo:,}  |  Direct: {direct:,}  "
            f"|  Purchases: {purchases:,}"
        )
        print(f"  {path}")

        combined_rows.extend(rows)

    if combined and len(specs) > 1:
        combined_path = out_dir / "ga4_channel_weekly_combined.csv"
        write_weekly_csv(combined_path, combined_rows)
        written.append(combined_path)
        print(f"\nCombined CSV: {combined_path} ({len(combined_rows)} row(s))")

    print(f"\nDate range: {iso_start} → {iso_end}  (week starts Sunday, Google Trends convention)")
    print(f"Channels: {SEO_CHANNEL} (SEO), {DIRECT_CHANNEL} (Direct)")
    return written


def main() -> None:
    load_app_environment()

    parser = argparse.ArgumentParser(
        description="Export weekly GA4 SEO/Direct sessions and ecommerce purchases (Sunday week start)."
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
        default=GA4_WEEKLY,
        help="Directory for output CSV files",
    )
    parser.add_argument(
        "--no-combined",
        action="store_true",
        help="With --properties-file, skip writing ga4_channel_weekly_combined.csv",
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

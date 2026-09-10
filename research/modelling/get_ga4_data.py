"""
Authenticate with: gcloud auth application-default login --scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/analytics.readonly"
"""

import argparse
import csv
from datetime import date, datetime, timedelta
from pathlib import Path
import random
import re
import time
import pandas as pd
from google.analytics.admin import AnalyticsAdminServiceClient
from google.analytics.admin_v1alpha.types import ListPropertiesRequest
from google.api_core.exceptions import (
    DeadlineExceeded,
    InternalServerError,
    ResourceExhausted,
    ServiceUnavailable,
    TooManyRequests,
)
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Filter,
    FilterExpression,
    Metric,
    MetricType,
    RunReportRequest,
)


SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/business.manage",
]

ADMIN_BETA_API_NAME = 'analyticsadmin'
ADMIN_BETA_API_VERSION = 'v1beta'
ADMIN_BETA_DISCOVERY_DOC = 'https://analyticsadmin.googleapis.com/$discovery/rest?version=v1beta'

ADMIN_ALPHA_API_NAME = 'analyticsadmin'
ADMIN_ALPHA_API_VERSION = 'v1alpha'
ADMIN_ALPHA_DISCOVERY_DOC = 'https://analyticsadmin.googleapis.com/$discovery/rest?version=v1alpha'

DATA_API_NAME = 'analyticsdata'
DATA_API_VERSION = 'v1beta'
DATA_API_DISCOVERY_DOC = 'https://analyticsdata.googleapis.com/$discovery/rest?version=v1beta'

DEFAULT_START_DATE = "2023-06-01"
PAGE_SIZE = 100_000
AI_SOURCE_PATTERN = "(chatgpt|gemini|perplexity|copilot|claude)"
TRANSIENT_API_ERRORS = (
    DeadlineExceeded,
    InternalServerError,
    ResourceExhausted,
    ServiceUnavailable,
    TooManyRequests,
)


def run_report_with_retry(
    client: BetaAnalyticsDataClient,
    request: RunReportRequest,
    *,
    attempts: int = 6,
):
    """Run a GA4 report with bounded retries for transient API failures."""
    for attempt in range(attempts):
        try:
            return client.run_report(request=request, timeout=120)
        except TRANSIENT_API_ERRORS as error:
            if attempt == attempts - 1:
                raise
            delay = min(30.0, 2 ** attempt) + random.uniform(0, 0.5)
            print(
                f"  transient GA4 error ({type(error).__name__}); "
                f"retrying in {delay:.1f}s",
                flush=True,
            )
            time.sleep(delay)
    raise RuntimeError("GA4 retry loop ended unexpectedly")


def latest_complete_week_end(today: date | None = None) -> date:
    """Return the Saturday ending the latest fully completed Sunday-start week."""
    current = today or date.today()
    current_week_start = current - timedelta(days=(current.weekday() + 1) % 7)
    return current_week_start - timedelta(days=1)


def calendar_month_ranges(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """Split an inclusive date range into low-cardinality calendar-month requests."""
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if start > end:
        raise ValueError(f"Start date {start_date} is after end date {end_date}")

    ranges: list[tuple[str, str]] = []
    chunk_start = start
    while chunk_start <= end:
        if chunk_start.month == 12:
            next_month = date(chunk_start.year + 1, 1, 1)
        else:
            next_month = date(chunk_start.year, chunk_start.month + 1, 1)
        chunk_end = min(end, next_month - timedelta(days=1))
        ranges.append((chunk_start.isoformat(), chunk_end.isoformat()))
        chunk_start = next_month
    return ranges


def week_start_from_ga4_year_week(value: str) -> date:
    """Convert GA4 yearWeek (week 01 contains Jan 1; Sunday start) to a date."""
    year = int(value[:4])
    week = int(value[4:])
    jan_1 = date(year, 1, 1)
    week_one_start = jan_1 - timedelta(days=(jan_1.weekday() + 1) % 7)
    return week_one_start + timedelta(weeks=week - 1)


def get_weekly_total_rows(
    client: BetaAnalyticsDataClient,
    property_name: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, object]]:
    """Fetch authoritative GA4 session totals at the model's weekly grain."""
    response = run_report_with_retry(
        client,
        RunReportRequest(
            property=property_name,
            dimensions=[Dimension(name="yearWeek")],
            metrics=[Metric(name="sessions")],
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            limit=PAGE_SIZE,
        ),
    )
    rows = [
        {
            "date": week_start_from_ga4_year_week(
                row.dimension_values[0].value
            ).strftime("%Y%m%d"),
            "name": property_name,
            "default_channel_group": "",
            "session_source": "",
            "session_medium": "",
            "custom_channel_grouping": "All Sessions",
            "sessions": int(row.metric_values[0].value),
        }
        for row in response.rows
    ]
    if rows:
        latest = max(rows, key=lambda row: str(row["date"]))
        latest_start = datetime.strptime(str(latest["date"]), "%Y%m%d").date()
        explicit_latest = get_total_sessions(
            client,
            property_name,
            max(latest_start, datetime.strptime(start_date, "%Y-%m-%d").date()).isoformat(),
            end_date,
        )
        if int(latest["sessions"]) != explicit_latest:
            raise RuntimeError(
                f"{property_name}: latest GA4 yearWeek reports "
                f"{int(latest['sessions']):,} sessions, but the explicit week reports "
                f"{explicit_latest:,}"
            )
    return rows


def list_accounts(transport: str = None):
    client = AnalyticsAdminServiceClient(transport=transport)
    results = client.list_accounts()
    return results

def accounts_to_csv(accounts):
    with open('accounts_original.csv', 'w') as f:
        writer = csv.writer(f)
        writer.writerow(['name', 'display_name', 'region_code'])
        for account in accounts:
            writer.writerow([account.name, account.display_name, account.region_code])

def get_ga4_accounts():
    accounts = list_accounts()
    accounts_to_csv(accounts)

def read_accounts_csv(file_path):
    account_df = pd.read_csv(file_path)
    accounts = {}
    for _, row in account_df.iterrows():
        accounts[row['name']] = {
            'website_type': row['website_type'],
            'vertical': row['vertical'],
        }
    print("Got all account names")
    return accounts

def list_properties(account_name):
    client = AnalyticsAdminServiceClient()
    properties = client.list_properties(
    ListPropertiesRequest(
        filter=f"parent:{account_name}",
        show_deleted=False,
        )
    )
    print(f"Got properties for account: {account_name}")
    return properties

def get_total_sessions(
    client: BetaAnalyticsDataClient,
    property_name: str,
    start_date: str,
    end_date: str,
) -> int:
    """Fetch the unsegmented GA4 session total used to validate pagination."""
    request = RunReportRequest(
        property=property_name,
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        metrics=[Metric(name='sessions')],
    )
    response = run_report_with_retry(client, request)
    if not response.rows or not response.rows[0].metric_values:
        return 0
    return int(response.rows[0].metric_values[0].value)

def get_properties_csv(property_list):
    with open('properties_missing.csv', 'w') as f:
        writer = csv.writer(f)
        writer.writerow([
            'name',
            'display_name',
            'industry_category',
            'time_zone',
            'currency_code',
            'website_type',
            'vertical',
        ])
        for property, account_metadata in property_list:
            writer.writerow([
                property.name,
                property.display_name,
                property.industry_category,
                property.time_zone,
                property.currency_code,
                account_metadata['website_type'],
                account_metadata['vertical'],
            ])
    print("Saved to CSV")

def get_sessions_by_channel(
    client: BetaAnalyticsDataClient,
    property_name: str,
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, int]:
    """
    Fetch exact daily default-channel totals plus narrowly filtered AI sources.

    A full date × channel × source × medium report is subject to GA4's
    high-cardinality data loss, even when paginated. The low-cardinality base is
    exact; matching AI-source sessions outside GA4's native AI Assistants channel
    are stored as an overlay. Downstream totals must use the separate
    ``All Sessions`` rows rather than summing channel rows.
    """
    rows = []
    for chunk_start, chunk_end in calendar_month_ranges(start_date, end_date):
        base_rows = []
        offset = 0
        while True:
            request = RunReportRequest(
                property=property_name,
                dimensions=[
                    Dimension(name="date"),
                    Dimension(name="sessionDefaultChannelGroup"),
                ],
                metrics=[Metric(name="sessions")],
                date_ranges=[DateRange(start_date=chunk_start, end_date=chunk_end)],
                limit=PAGE_SIZE,
                offset=offset,
            )
            ga4_data = run_report_with_retry(client, request)
            for row in ga4_data.rows:
                base_rows.append({
                    "date": row.dimension_values[0].value,
                    "name": property_name,
                    "default_channel_group": row.dimension_values[1].value,
                    "session_source": "",
                    "session_medium": "",
                    "sessions": int(row.metric_values[0].value),
                })
            fetched = len(ga4_data.rows)
            offset += fetched
            if fetched == 0 or offset >= ga4_data.row_count:
                break

        ai_rows = []
        offset = 0
        while True:
            request = RunReportRequest(
                property=property_name,
                dimensions=[
                    Dimension(name="date"),
                    Dimension(name="sessionDefaultChannelGroup"),
                    Dimension(name="sessionSource"),
                ],
                metrics=[Metric(name="sessions")],
                date_ranges=[DateRange(start_date=chunk_start, end_date=chunk_end)],
                dimension_filter=FilterExpression(
                    filter=Filter(
                        field_name="sessionSource",
                        string_filter=Filter.StringFilter(
                            match_type=Filter.StringFilter.MatchType.PARTIAL_REGEXP,
                            value=AI_SOURCE_PATTERN,
                            case_sensitive=False,
                        ),
                    )
                ),
                limit=PAGE_SIZE,
                offset=offset,
            )
            ga4_data = run_report_with_retry(client, request)
            for row in ga4_data.rows:
                ai_rows.append({
                    "date": row.dimension_values[0].value,
                    "name": property_name,
                    "default_channel_group": row.dimension_values[1].value,
                    "session_source": row.dimension_values[2].value,
                    "session_medium": "",
                    "sessions": int(row.metric_values[0].value),
                })
            fetched = len(ga4_data.rows)
            offset += fetched
            if fetched == 0 or offset >= ga4_data.row_count:
                break

        base = pd.DataFrame(base_rows)
        if base.empty:
            base = pd.DataFrame(
                columns=[
                    "date",
                    "name",
                    "default_channel_group",
                    "session_source",
                    "session_medium",
                    "sessions",
                    "custom_channel_grouping",
                ]
            )
        else:
            base["custom_channel_grouping"] = base.apply(classify_channel, axis=1)
        if ai_rows:
            ai = pd.DataFrame(ai_rows)
            # Native AI Assistants sessions are already present in ``base``.
            # Source-detected AI outside that channel is an overlay because GA4's
            # Sessions metric is not additive across channel/source dimensions.
            ai = ai[
                ~ai["default_channel_group"].str.strip().str.lower().isin(
                    {"ai assistant", "ai assistants"}
                )
            ].copy()
            if not ai.empty:
                ai["custom_channel_grouping"] = "AI Chatbots"
                chunk = pd.concat([base, ai], ignore_index=True)
            else:
                chunk = base
        else:
            chunk = base

        rows.extend(chunk.to_dict("records"))
        print(
            f"  {chunk_start}–{chunk_end}: {len(base_rows):,} base rows, "
            f"{len(ai_rows):,} AI-source rows",
            flush=True,
        )

    weekly_total_rows = get_weekly_total_rows(
        client,
        property_name,
        start_date,
        end_date,
    )
    validated_total = sum(int(row["sessions"]) for row in weekly_total_rows)
    rows.extend(weekly_total_rows)
    print(
        f"  {len(weekly_total_rows):,} authoritative weekly totals; "
        f"latest week validated independently",
        flush=True,
    )

    df = pd.DataFrame(rows)
    if df.empty:
        return df, validated_total
    return df, validated_total

def classify_channel(row):
    channel = str(row["default_channel_group"] or "").strip()
    source = str(row["session_source"] or "").strip()
    medium = str(row["session_medium"] or "").strip()
    channel_lower = channel.lower()
    source_lower = source.lower()
    medium_lower = medium.lower()
    if (
        channel_lower in {"ai assistant", "ai assistants"}
        or re.search(r"(chatgpt|gemini|perplexity|copilot|claude)", source_lower)
    ):
        return "AI Chatbots"
    if (
        channel_lower == "affiliates"
        or re.search(r"(affiliate|awin)", medium_lower)
    ):
        return "Affiliates"
    if channel_lower == "email":
        return "Email"
    if (
        channel_lower in {"paid social", "organic social", "organic video"}
        or re.search(r"(facebook|t\.co|reddit|snapchat|instagram|linkedin|trustpilot|glassdoor|messenger|yelp|pinterest|skype|fb|ig)", source_lower)
        or re.search(r"(social|social-network|social-media|sm|social network|social media)", medium_lower)
    ):
        return "Social"
    if (
        channel_lower in {"paid search", "cross-network", "paid shopping", "paid video", "display", "paid other"}
        or medium_lower in {"cpc", "ppc", "paidsearch"}
    ):
        return "PPC"
    if (
        channel_lower in {"organic search", "google places", "organic shopping"}
        or re.search(r"(google places|google\+places|search\.brave\.com)", source_lower)
        or medium_lower == "organic"
    ):
        return "SEO"
    if channel_lower == "direct":
        return "Direct"
    if (
        channel_lower == "referral"
        or medium_lower == "referral"
    ):
        return "Referral"
    return ", ".join((channel, source, medium))

def create_sessions_csv(df, properties_df):
    result = (
        df
        .merge(properties_df, on="name", how="left")
        .assign(date=lambda df: pd.to_datetime(df["date"], format="%Y%m%d"))
        .groupby([
            "date",
            "name",
            "display_name",
            "industry_category",
            "time_zone",
            "currency_code",
            "website_type",
            "vertical",
            "custom_channel_grouping",
        ], as_index=False)["sessions"]
        .sum()
    )
    return result

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export complete GA4 session rows by custom channel."
    )
    parser.add_argument(
        "--property",
        dest="properties",
        action="append",
        help="Anonymised property ID to fetch, e.g. property_0012. Repeatable. "
        "Omit to fetch every property in properties.csv.",
    )
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument(
        "--end-date",
        default=None,
        help="Inclusive YYYY-MM-DD. Defaults to the latest completed Saturday.",
    )
    parser.add_argument(
        "--config-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory containing properties.csv and anonymization_mapping.csv.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Session output directory. Defaults to <config-root>/sessions.",
    )
    parser.add_argument(
        "--expected-property-count",
        type=int,
        default=61,
        help="Fail a full-portfolio pull unless this many non-app properties are eligible.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    start_date = args.start_date
    end_date = args.end_date or latest_complete_week_end().isoformat()
    config_root = args.config_root.expanduser().resolve()
    output_dir = (
        args.output_root.expanduser().resolve()
        if args.output_root is not None
        else config_root / "sessions"
    )

    property_mapping = pd.read_csv(config_root / "anonymization_mapping.csv")
    property_names = property_mapping[
        (property_mapping["entity"] == "properties")
        & (property_mapping["field"] == "name")
    ][["identifier", "original_value"]]
    properties_df = pd.read_csv(config_root / "properties.csv")
    app_property_ids = set(
        properties_df.loc[
            properties_df["website_type"].astype(str).str.lower() == "app",
            "name",
        ].astype(str)
    )
    active_properties_df = properties_df[
        ~properties_df["name"].astype(str).isin(app_property_ids)
    ]
    properties_to_fetch = property_names[
        property_names["identifier"].isin(active_properties_df["name"])
    ]
    if not args.properties and len(properties_to_fetch) != args.expected_property_count:
        raise ValueError(
            f"Expected {args.expected_property_count} eligible web properties, found "
            f"{len(properties_to_fetch)}"
        )
    if args.properties:
        requested = set(args.properties)
        excluded_apps = requested & app_property_ids
        if excluded_apps:
            raise ValueError(
                "App properties are excluded from GA4 pulls: "
                f"{sorted(excluded_apps)}"
            )
        unknown = requested - set(property_names["identifier"])
        if unknown:
            raise ValueError(f"Unknown property identifier(s): {sorted(unknown)}")
        properties_to_fetch = properties_to_fetch[
            properties_to_fetch["identifier"].isin(requested)
        ]

    print(
        f"Fetching {len(properties_to_fetch)} properties from {start_date} "
        f"through {end_date} (latest complete week only); "
        f"excluding {len(app_property_ids)} app properties"
    )
    client = BetaAnalyticsDataClient()

    for _, row in properties_to_fetch.iterrows():
        property_name = row['original_value']
        property_identifier = row['identifier']
        property_metadata = properties_df[properties_df['name'] == property_identifier]
        vertical = property_metadata['vertical'].iloc[0]
        website_type = property_metadata['website_type'].iloc[0]
        output_dir.mkdir(parents=True, exist_ok=True)
        print(
            f"Getting sessions for {property_name} ({property_identifier}), "
            f"{website_type}/{vertical}"
        )
        df, expected_total = get_sessions_by_channel(
            client,
            property_name,
            start_date,
            end_date,
        )
        if df.empty:
            print(f"Skipping {property_name}: no session rows returned")
            continue

        detailed_total = int(
            df.loc[
                df["custom_channel_grouping"] == "All Sessions",
                "sessions",
            ].sum()
        )
        if detailed_total != expected_total:
            raise RuntimeError(
                f"{property_name}: detailed rows sum to {detailed_total:,} sessions, "
                f"but GA4 reports {expected_total:,}; existing CSV was not replaced"
            )
        print(f"  validated {detailed_total:,} sessions against GA4 total")

        # The GA4 report is keyed by the real resource name, but properties.csv is
        # anonymised. Without this the merge below misses, the metadata columns come
        # back NaN, and groupby silently drops every row.
        df["name"] = property_identifier
        result = create_sessions_csv(df, properties_df)
        if result.empty:
            print(f"Skipping {property_name}: {len(df)} rows fetched but none survived "
                  f"the merge with properties.csv on {property_identifier!r}")
            continue
        property_output_dir = output_dir / str(website_type) / str(vertical)
        property_output_dir.mkdir(parents=True, exist_ok=True)
        output_path = property_output_dir / f"sessions_{property_identifier}.csv"
        temporary_path = output_path.with_suffix(".csv.tmp")
        result.to_csv(temporary_path, index=False)
        temporary_path.replace(output_path)
        print(f"Saved sessions for {property_name} as sessions_{property_identifier}.csv "
              f"({len(result):,} rows)")

if __name__ == "__main__":
    main()
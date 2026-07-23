"""Build weekly analysis panel from GA4 and optional GSC extracts."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence, TypeVar

import numpy as np
import pandas as pd

from .holidays import festive_mask_bf_to_twelfth_night

SESSION_COLS = (
    "seo_sessions",
    "direct_sessions",
    "branded_ppc_sessions",
    "non_branded_ppc_sessions",
    "ai_sessions",
    "other_sessions",
)

TWeekPoint = TypeVar("TWeekPoint", bound=dict[str, Any])


def _as_of_date(as_of: date | datetime | None = None) -> date:
    if as_of is None:
        return datetime.now(timezone.utc).date()
    if isinstance(as_of, datetime):
        return as_of.date()
    return as_of


def sunday_week_start(value: date | datetime | pd.Timestamp | str) -> date:
    """Sunday-start week label (matches Google Trends / GA4 weekly exports)."""
    if isinstance(value, str):
        parsed = pd.Timestamp(value).date()
    elif isinstance(value, pd.Timestamp):
        parsed = value.date()
    elif isinstance(value, datetime):
        parsed = value.date()
    else:
        parsed = value
    return parsed - timedelta(days=(parsed.weekday() + 1) % 7)


def is_completed_sunday_week(
    week_start: date | datetime | pd.Timestamp | str,
    *,
    as_of: date | datetime | None = None,
) -> bool:
    """
    True when the Sunday-start week has fully ended.

    A week labeled ``week_start`` (Sunday) covers Sunday–Saturday. It is complete
    once ``as_of`` is strictly after that Saturday (i.e. the following Sunday or later).
    """
    start = sunday_week_start(week_start)
    week_end = start + timedelta(days=6)
    return _as_of_date(as_of) > week_end


def filter_completed_weeks(
    points: Sequence[TWeekPoint],
    *,
    as_of: date | datetime | None = None,
    week_key: str = "week",
) -> list[TWeekPoint]:
    """Drop in-progress Sunday weeks from a weekly series (chart / export)."""
    as_of_d = _as_of_date(as_of)
    out: list[TWeekPoint] = []
    for point in points:
        week = point.get(week_key)
        if week is None:
            continue
        if is_completed_sunday_week(week, as_of=as_of_d):
            out.append(point)
    return out


def normalize_ga4_channel_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map ``ga4_channel_export`` weekly wide CSV into estimate panel columns.

    Export uses ``other_traffic_*`` and combined ``ppc_*`` (Direct is inside Other).
    """
    out = df.copy()
    renames = {
        "sessions": "total_sessions",
        "purchases": "total_purchases",
        "other_traffic_sessions": "other_sessions",
        "other_traffic_purchases": "other_purchases",
        "ppc_sessions": "branded_ppc_sessions",
        "ppc_purchases": "branded_ppc_purchases",
    }
    for src, dst in renames.items():
        if src in out.columns and dst not in out.columns:
            out = out.rename(columns={src: dst})
    if "non_branded_ppc_sessions" not in out.columns:
        out["non_branded_ppc_sessions"] = 0.0
    if "non_branded_ppc_purchases" not in out.columns:
        out["non_branded_ppc_purchases"] = 0.0
    if "direct_sessions" not in out.columns:
        out["direct_sessions"] = 0.0
    if "direct_purchases" not in out.columns:
        out["direct_purchases"] = 0.0
    return out


def _sunday_week_start(dates: pd.Series) -> pd.Series:
    dates = pd.to_datetime(dates)
    return dates - pd.to_timedelta((dates.dt.dayofweek + 1) % 7, unit="D")


def load_gsc_weekly(gsc_csv: Path | pd.DataFrame) -> pd.DataFrame:
    df = gsc_csv if isinstance(gsc_csv, pd.DataFrame) else pd.read_csv(gsc_csv)
    df = df.rename(
        columns={
            "Date": "date",
            "Clicks": "gsc_clicks",
            "Impressions": "gsc_impressions",
            "CTR": "gsc_ctr",
            "Position": "gsc_position",
            "clicks": "gsc_clicks",
            "impressions": "gsc_impressions",
            "ctr": "gsc_ctr",
            "position": "gsc_position",
        }
    )
    if "date" not in df.columns and "week" in df.columns:
        return df.rename(columns={"week": "week"})
    df["date"] = pd.to_datetime(df["date"])
    if df["gsc_ctr"].dtype == object:
        df["gsc_ctr"] = (
            df["gsc_ctr"].astype(str).str.replace("%", "", regex=False).astype(float) / 100.0
        )
    df["week"] = _sunday_week_start(df["date"])
    weekly = (
        df.groupby("week", as_index=False)
        .agg(
            gsc_clicks=("gsc_clicks", "sum"),
            gsc_impressions=("gsc_impressions", "sum"),
            gsc_position=("gsc_position", "mean"),
        )
        .sort_values("week")
    )
    weekly["gsc_ctr"] = weekly["gsc_clicks"] / weekly["gsc_impressions"].replace(0, np.nan)
    weekly["gsc_post_impression_break"] = weekly["week"] >= "2025-09-01"
    return weekly


def load_trends_weekly(trends_csv: Path | pd.DataFrame) -> pd.DataFrame:
    frame = trends_csv if isinstance(trends_csv, pd.DataFrame) else pd.read_csv(trends_csv)
    frame = frame.copy()
    if "week" not in frame.columns:
        raise ValueError("Normalized Google Trends data must include a week column")
    trend_columns = [column for column in frame.columns if column.startswith("trends_")]
    if not trend_columns:
        raise ValueError("Normalized Google Trends data has no interest columns")
    frame["week"] = pd.to_datetime(frame["week"])
    for column in trend_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[["week", *trend_columns]].sort_values("week")


def build_weekly_panel(
    ga4: Path | pd.DataFrame,
    *,
    trends: Path | pd.DataFrame | None = None,
    gsc: Path | pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Merge GA4 weekly channels with optional manual Trends and GSC metrics.
    Adds ``mask_bf_twelfth``, ``non_ai_sessions``, ``ai_adstock`` (decay 0.7).
    """
    df = ga4 if isinstance(ga4, pd.DataFrame) else pd.read_csv(ga4)
    df = normalize_ga4_channel_weekly(df)
    df["week"] = pd.to_datetime(df["week"])
    df = df.sort_values("week").reset_index(drop=True)

    for c in SESSION_COLS:
        if c not in df.columns:
            df[c] = 0.0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    if "total_sessions" not in df.columns:
        df["total_sessions"] = df[list(SESSION_COLS)].sum(axis=1)
    if "total_purchases" not in df.columns:
        purch_cols = [c for c in df.columns if c.endswith("_purchases")]
        df["total_purchases"] = (
            df[purch_cols].sum(axis=1) if purch_cols else 0.0
        )

    df["non_ai_sessions"] = (df["total_sessions"] - df["ai_sessions"]).clip(lower=0)
    ad = 0.0
    ads: list[float] = []
    for x in df["ai_sessions"]:
        ad = float(x) + 0.7 * ad
        ads.append(ad)
    df["ai_adstock"] = ads
    df["mask_bf_twelfth"] = festive_mask_bf_to_twelfth_night(df["week"])

    if trends is not None:
        trend_frame = load_trends_weekly(trends)
        df = df.merge(trend_frame, on="week", how="left")
        trend_columns = [column for column in df.columns if column.startswith("trends_")]
        df[trend_columns] = df[trend_columns].interpolate(limit_direction="both")

    if gsc is not None:
        g = load_gsc_weekly(gsc)
        df = df.merge(g, on="week", how="left")

    return df

#!/usr/bin/env python3
"""
Build a weekly combined GA4 + Google Trends panel for one property.

Merges:
  - New channel weekly (AI / SEO / Other Traffic + purchases)
  - Direct from legacy default-channel weekly (or long daily)
  - Branded / non-branded PPC sessions from ``ga4_ppc_brand_export``
  - Mean branded / non-branded Google Trends from ``trends_manual``

``other_*`` = Other Traffic − Direct (so Direct is not double-counted).

Branded / non-branded PPC purchases are not in the brand export; they are
allocated from channel ``ppc_purchases`` by that week's branded/non-branded
PPC session share.

Holiday flags (week contains the date; Sunday-start weeks)::

  bank_holiday, black_friday, cyber_monday, is_holiday, holiday_names
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_GA4_ROOT = Path(__file__).resolve().parent
_RESEARCH_ROOT = _GA4_ROOT.parent
_REPO_ROOT = _RESEARCH_ROOT.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from paths import GA4_DAILY, GA4_PPC, GA4_WEEKLY, TRENDS_ROOT  # noqa: E402

DEFAULT_ENTITY = "euro_car_parts"
DEFAULT_PROPERTY_ID = "241379560"


def _term_slug(term: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", term.strip()).strip("_").lower() or "term"


def _filename_slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").lower() or "property"


def sunday_week_start(ts: pd.Timestamp) -> pd.Timestamp:
    d = ts.normalize()
    return d - pd.Timedelta(days=(d.weekday() + 1) % 7)


# Explicit UK bank holidays (England/Wales) for the export window, as provided.
UK_BANK_HOLIDAYS: tuple[tuple[date, str], ...] = (
    # 2022
    (date(2022, 1, 3), "New Year’s Day (substitute day)"),
    (date(2022, 4, 15), "Good Friday"),
    (date(2022, 4, 18), "Easter Monday"),
    (date(2022, 5, 2), "Early May bank holiday"),
    (date(2022, 6, 2), "Spring bank holiday"),
    (date(2022, 6, 3), "Platinum Jubilee bank holiday"),
    (date(2022, 8, 29), "Summer bank holiday"),
    (date(2022, 9, 19), "Bank Holiday for the State Funeral of Queen Elizabeth II"),
    (date(2022, 12, 26), "Boxing Day"),
    (date(2022, 12, 27), "Christmas Day (substitute day)"),
    # 2023
    (date(2023, 1, 2), "New Year’s Day (substitute day)"),
    (date(2023, 4, 7), "Good Friday"),
    (date(2023, 4, 10), "Easter Monday"),
    (date(2023, 5, 1), "Early May bank holiday"),
    (date(2023, 5, 8), "Bank holiday for the coronation of King Charles III"),
    (date(2023, 5, 29), "Spring bank holiday"),
    (date(2023, 8, 28), "Summer bank holiday"),
    (date(2023, 12, 25), "Christmas Day"),
    (date(2023, 12, 26), "Boxing Day"),
    # 2024
    (date(2024, 1, 1), "New Year’s Day"),
    (date(2024, 3, 29), "Good Friday"),
    (date(2024, 4, 1), "Easter Monday"),
    (date(2024, 5, 6), "Early May bank holiday"),
    (date(2024, 5, 27), "Spring bank holiday"),
    (date(2024, 8, 26), "Summer bank holiday"),
    (date(2024, 12, 25), "Christmas Day"),
    (date(2024, 12, 26), "Boxing Day"),
    # 2025
    (date(2025, 1, 1), "New Year’s Day"),
    (date(2025, 4, 18), "Good Friday"),
    (date(2025, 4, 21), "Easter Monday"),
    (date(2025, 5, 5), "Early May bank holiday"),
    (date(2025, 5, 26), "Spring bank holiday"),
    (date(2025, 8, 25), "Summer bank holiday"),
    (date(2025, 12, 25), "Christmas Day"),
    (date(2025, 12, 26), "Boxing Day"),
    # 2026
    (date(2026, 1, 1), "New Year’s Day"),
    (date(2026, 4, 3), "Good Friday"),
    (date(2026, 4, 6), "Easter Monday"),
    (date(2026, 5, 4), "Early May bank holiday"),
    (date(2026, 5, 25), "Spring bank holiday"),
)


def us_thanksgiving(year: int) -> date:
    """Fourth Thursday of November."""
    nov1 = date(year, 11, 1)
    first_thu = nov1 + timedelta(days=(3 - nov1.weekday()) % 7)
    return first_thu + timedelta(days=21)


def black_friday_cyber_monday(year: int) -> tuple[tuple[date, str], tuple[date, str]]:
    tg = us_thanksgiving(year)
    return (
        (tg + timedelta(days=1), "Black Friday"),
        (tg + timedelta(days=4), "Cyber Monday"),
    )


def holiday_calendar(
    *,
    year_min: int,
    year_max: int,
) -> list[tuple[date, str, str]]:
    """
    Return (date, name, kind) where kind is bank_holiday | black_friday | cyber_monday.
    """
    rows: list[tuple[date, str, str]] = []
    for d, name in UK_BANK_HOLIDAYS:
        if year_min <= d.year <= year_max:
            rows.append((d, name, "bank_holiday"))
    for year in range(year_min, year_max + 1):
        bf, cm = black_friday_cyber_monday(year)
        rows.append((bf[0], bf[1], "black_friday"))
        rows.append((cm[0], cm[1], "cyber_monday"))
    rows.sort(key=lambda x: x[0])
    return rows


def week_holiday_flags(weeks: pd.Series) -> pd.DataFrame:
    """Flag Sunday-start weeks that contain any listed holiday date."""
    weeks = pd.to_datetime(weeks)
    if weeks.empty:
        return pd.DataFrame(
            {
                "bank_holiday": [],
                "black_friday": [],
                "cyber_monday": [],
                "is_holiday": [],
                "holiday_names": [],
            }
        )

    year_min = int(weeks.min().year) - 1
    year_max = int(weeks.max().year) + 1
    holidays = holiday_calendar(year_min=year_min, year_max=year_max)

    bank_flags: list[int] = []
    bf_flags: list[int] = []
    cm_flags: list[int] = []
    any_flags: list[int] = []
    names: list[str] = []

    for w in weeks:
        w_date = w.date() if hasattr(w, "date") else pd.Timestamp(w).date()
        week_days = {w_date + timedelta(days=i) for i in range(7)}
        hit_bank = False
        hit_bf = False
        hit_cm = False
        hit_names: list[str] = []
        for d, name, kind in holidays:
            if d not in week_days:
                continue
            hit_names.append(name)
            if kind == "bank_holiday":
                hit_bank = True
            elif kind == "black_friday":
                hit_bf = True
            elif kind == "cyber_monday":
                hit_cm = True
        bank_flags.append(int(hit_bank))
        bf_flags.append(int(hit_bf))
        cm_flags.append(int(hit_cm))
        any_flags.append(int(bool(hit_names)))
        names.append("; ".join(hit_names))

    return pd.DataFrame(
        {
            "bank_holiday": bank_flags,
            "black_friday": bf_flags,
            "cyber_monday": cm_flags,
            "is_holiday": any_flags,
            "holiday_names": names,
        },
        index=weeks.index,
    )


def load_channel_weekly(property_name: str, property_id: str) -> pd.DataFrame | None:
    """New AI/PPC/SEO/Other weekly export, if present."""
    path = GA4_DAILY / f"ga4_channel_weekly_{_filename_slug(property_name)}_{property_id}.csv"
    if not path.is_file():
        return None
    df = pd.read_csv(path)
    df["week"] = pd.to_datetime(df["week"])
    return df


def load_legacy_weekly(property_name: str, property_id: str) -> pd.DataFrame | None:
    path = GA4_WEEKLY / f"ga4_channel_weekly_{_filename_slug(property_name)}_{property_id}.csv"
    if not path.is_file():
        return None
    df = pd.read_csv(path)
    df["week"] = pd.to_datetime(df["week"])
    return df


def load_ppc_brand_weekly(property_name: str, property_id: str) -> pd.DataFrame:
    path = GA4_PPC / f"ga4_ppc_brand_weekly_{_filename_slug(property_name)}_{property_id}.csv"
    if not path.is_file():
        raise SystemExit(f"Missing PPC brand weekly CSV: {path}")
    df = pd.read_csv(path)
    df["week"] = pd.to_datetime(df["week"])
    return df[
        [
            "week",
            "branded_ppc_sessions",
            "non_branded_ppc_sessions",
            "total_ppc_sessions",
        ]
    ]


def load_direct_weekly(property_name: str, property_id: str) -> pd.DataFrame:
    """Prefer legacy weekly export; fall back to aggregating legacy long daily."""
    weekly_path = (
        GA4_WEEKLY / f"ga4_channel_weekly_{_filename_slug(property_name)}_{property_id}.csv"
    )
    if weekly_path.is_file():
        df = pd.read_csv(weekly_path)
        df["week"] = pd.to_datetime(df["week"])
        out = df[["week", "direct_sessions"]].copy()
        out["direct_purchases"] = np.nan
    else:
        out = pd.DataFrame(columns=["week", "direct_sessions", "direct_purchases"])

    candidates = list(GA4_DAILY.glob(f"ga4_channel_long_*_{property_id}.csv"))
    for path in candidates:
        long = pd.read_csv(path)
        if "channel_group" not in long.columns:
            continue
        if "Direct" not in set(long["channel_group"].astype(str)):
            continue
        long["date"] = pd.to_datetime(long["date"])
        long["week"] = long["date"].map(sunday_week_start)
        direct = (
            long[long["channel_group"] == "Direct"]
            .groupby("week", as_index=False)
            .agg(direct_sessions=("sessions", "sum"), direct_purchases=("purchases", "sum"))
        )
        if out.empty:
            return direct
        merged = out.merge(direct, on="week", how="outer", suffixes=("_wk", "_long"))
        merged["direct_sessions"] = merged["direct_sessions_wk"].fillna(
            merged["direct_sessions_long"]
        )
        merged["direct_purchases"] = merged["direct_purchases_long"].fillna(
            merged["direct_purchases_wk"]
        )
        return merged[["week", "direct_sessions", "direct_purchases"]]

    if out.empty:
        raise SystemExit(
            f"Could not find Direct sessions for {property_name} ({property_id})."
        )
    return out


def mean_trends_weekly(entity_id: str, bucket: str, terms: list[str]) -> pd.Series:
    """Mean google_trends_value across terms for each week_start."""
    stacks: dict[str, list[float]] = {}
    kind_plural = "advertisers"  # euro_car_parts; overridden by caller if needed
    # Detect kind from registry path existence
    for kind in ("advertisers", "publishers"):
        probe = TRENDS_ROOT / kind / entity_id / bucket
        if probe.is_dir():
            kind_plural = kind
            break

    for term in terms:
        path = (
            TRENDS_ROOT
            / kind_plural
            / entity_id
            / bucket
            / f"{_term_slug(str(term))}_20220601"
            / "trends_weekly.csv"
        )
        if not path.is_file():
            print(f"  warn: missing trends file {path}")
            continue
        tdf = pd.read_csv(path)
        tdf["week_start"] = pd.to_datetime(tdf["week_start"])
        for _, r in tdf.iterrows():
            w = r["week_start"].strftime("%Y-%m-%d")
            stacks.setdefault(w, []).append(float(r["google_trends_value"]))

    return pd.Series(
        {pd.to_datetime(w): float(np.mean(vals)) for w, vals in stacks.items()},
        name=bucket,
    ).sort_index()


def load_trends_means(entity_id: str) -> pd.DataFrame:
    registry = json.loads((TRENDS_ROOT / "term_registry.json").read_text(encoding="utf-8"))
    spec = (registry.get("entities") or {}).get(entity_id)
    if not spec:
        raise SystemExit(f"Entity {entity_id!r} not in term_registry.json")

    brand = mean_trends_weekly(entity_id, "branded", spec.get("branded") or [])
    nonbrand = mean_trends_weekly(entity_id, "non_branded", spec.get("non_branded") or [])
    out = pd.DataFrame(
        {
            "week": brand.index.union(nonbrand.index),
        }
    )
    out = out.set_index("week")
    out["branded_trends_mean"] = brand.reindex(out.index)
    out["nonbranded_trends_mean"] = nonbrand.reindex(out.index)
    return out.reset_index()


def build_combined(
    *,
    property_name: str,
    property_id: str,
    entity_id: str,
) -> pd.DataFrame:
    channel = load_channel_weekly(property_name, property_id)
    legacy = load_legacy_weekly(property_name, property_id)
    ppc = load_ppc_brand_weekly(property_name, property_id)
    direct = load_direct_weekly(property_name, property_id)
    trends = load_trends_means(entity_id)

    if channel is not None:
        df = channel.copy()
        source_note = "new_channel_rules"
    elif legacy is not None:
        # Legacy: Organic Search as SEO; total purchases only (no channel split).
        df = legacy.rename(
            columns={
                "seo_sessions": "seo_sessions",
                "ecommerce_purchases": "total_purchases_legacy",
            }
        )[["week", "seo_sessions"]].copy()
        df["seo_purchases"] = 0
        df["ai_sessions"] = 0
        df["ai_purchases"] = 0
        df["other_traffic_sessions"] = 0
        df["other_traffic_purchases"] = 0
        df["ppc_purchases"] = 0
        source_note = "legacy_weekly"
    else:
        raise SystemExit(
            f"No channel weekly (new or legacy) for {property_name} ({property_id})"
        )

    df = df.merge(ppc, on="week", how="outer")
    df = df.merge(direct, on="week", how="outer")
    df = df.merge(trends, on="week", how="left")
    if legacy is not None and "total_purchases_legacy" not in df.columns:
        # Attach legacy total purchases when using new channel for SEO but need total
        pass
    if legacy is not None:
        leg = legacy[["week"]].copy()
        if "ecommerce_purchases" in legacy.columns:
            leg["total_purchases_legacy"] = legacy["ecommerce_purchases"]
            df = df.merge(leg, on="week", how="left")

    df = df.sort_values("week").reset_index(drop=True)

    df["direct_sessions"] = df["direct_sessions"].fillna(0).astype(float)
    df["direct_purchases"] = df["direct_purchases"].fillna(0).astype(float)
    for col in (
        "seo_sessions",
        "seo_purchases",
        "ai_sessions",
        "ai_purchases",
        "other_traffic_sessions",
        "other_traffic_purchases",
        "ppc_purchases",
    ):
        if col not in df.columns:
            df[col] = 0
        df[col] = df[col].fillna(0).astype(float)

    if source_note == "new_channel_rules":
        df["other_sessions"] = (
            df["other_traffic_sessions"] - df["direct_sessions"]
        ).clip(lower=0)
        df["other_purchases"] = (
            df["other_traffic_purchases"] - df["direct_purchases"]
        ).clip(lower=0)
    else:
        # No "other" residual under legacy-only; leave zero
        df["other_sessions"] = 0
        df["other_purchases"] = 0
        # Attribute legacy total purchases to SEO+Direct+PPC by session share later if needed
        if "total_purchases_legacy" in df.columns:
            # Keep seo_purchases at 0; store total in a helper used by CF for purchases outcome
            pass

    branded = df["branded_ppc_sessions"].fillna(0).astype(float)
    nonbranded = df["non_branded_ppc_sessions"].fillna(0).astype(float)
    ppc_sess = branded + nonbranded
    ppc_purch = df["ppc_purchases"].fillna(0).astype(float)
    share_brand = np.where(ppc_sess > 0, branded / ppc_sess, 0.0)
    share_non = np.where(ppc_sess > 0, nonbranded / ppc_sess, 0.0)
    df["branded_ppc_purchases"] = (ppc_purch * share_brand).round().astype(int)
    df["non_branded_ppc_purchases"] = (ppc_purch * share_non).round().astype(int)

    # Total sessions / purchases for modelling
    df["total_sessions"] = (
        df["seo_sessions"].fillna(0)
        + df["direct_sessions"]
        + branded
        + nonbranded
        + df["ai_sessions"].fillna(0)
        + df["other_sessions"]
    )
    if source_note == "new_channel_rules":
        df["total_purchases"] = (
            df["seo_purchases"]
            + df["direct_purchases"]
            + df["branded_ppc_purchases"]
            + df["non_branded_ppc_purchases"]
            + df["ai_purchases"]
            + df["other_purchases"]
        )
    else:
        df["total_purchases"] = (
            df["total_purchases_legacy"].fillna(0)
            if "total_purchases_legacy" in df.columns
            else 0
        )

    holidays = week_holiday_flags(df["week"])

    out = pd.DataFrame(
        {
            "week": df["week"].dt.strftime("%Y-%m-%d"),
            "seo_sessions": df["seo_sessions"].fillna(0).astype(int),
            "direct_sessions": df["direct_sessions"].astype(int),
            "branded_ppc_sessions": branded.astype(int),
            "non_branded_ppc_sessions": nonbranded.astype(int),
            "ai_sessions": df["ai_sessions"].fillna(0).astype(int),
            "other_sessions": df["other_sessions"].astype(int),
            "total_sessions": df["total_sessions"].astype(int),
            "branded_trends_mean": df["branded_trends_mean"].round(2),
            "nonbranded_trends_mean": df["nonbranded_trends_mean"].round(2),
            "seo_purchases": df["seo_purchases"].fillna(0).astype(int),
            "direct_purchases": df["direct_purchases"].astype(int),
            "branded_ppc_purchases": df["branded_ppc_purchases"],
            "non_branded_ppc_purchases": df["non_branded_ppc_purchases"],
            "ai_purchases": df["ai_purchases"].fillna(0).astype(int),
            "other_purchases": df["other_purchases"].astype(int),
            "total_purchases": df["total_purchases"].astype(int),
            "bank_holiday": holidays["bank_holiday"].astype(int),
            "black_friday": holidays["black_friday"].astype(int),
            "cyber_monday": holidays["cyber_monday"].astype(int),
            "is_holiday": holidays["is_holiday"].astype(int),
            "holiday_names": holidays["holiday_names"],
            "channel_source": source_note,
        }
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Build weekly GA4 + Trends combined CSV")
    parser.add_argument("--property-name", default=None)
    parser.add_argument("--property-id", default=None)
    parser.add_argument("--entity-id", default=None, help="trends_manual entity id (default: property-name)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Build for all rows in research/config/ga4_ppc_properties.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (single-property mode)",
    )
    args = parser.parse_args()

    from paths import GA4_PROPERTIES

    jobs: list[tuple[str, str, str]] = []
    if args.all:
        props = pd.read_csv(GA4_PROPERTIES)
        for _, r in props.iterrows():
            name = str(r["property_name"]).strip()
            pid = str(r["ga4_property_id"]).strip()
            jobs.append((name, pid, name))
    else:
        name = args.property_name or DEFAULT_ENTITY
        pid = args.property_id or DEFAULT_PROPERTY_ID
        entity = args.entity_id or name
        jobs.append((name, pid, entity))

    for property_name, property_id, entity_id in jobs:
        out_path = args.output if (args.output and len(jobs) == 1) else (
            GA4_DAILY.parent
            / "combined"
            / f"ga4_trends_weekly_{_filename_slug(property_name)}_{property_id}.csv"
        )
        print(f"Building combined weekly panel for {property_name} ({property_id})…")
        try:
            combined = build_combined(
                property_name=property_name,
                property_id=property_id,
                entity_id=entity_id,
            )
        except SystemExit as exc:
            print(f"  SKIP: {exc}")
            continue
        out_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(out_path, index=False)
        print(
            f"  Wrote {out_path} ({len(combined)} weeks, "
            f"source={combined['channel_source'].iloc[0]})"
        )


if __name__ == "__main__":
    main()

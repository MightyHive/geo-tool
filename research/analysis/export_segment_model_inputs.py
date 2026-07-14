#!/usr/bin/env python3
"""
Build site-level long-panel input CSVs for external modelling tools.

One row per site × week. Metrics are **normalised to each site's own baseline
mean** (= 1.0), so brands are on a comparable scale (no summing across brands).

Seasonality / calendar trend columns are omitted — add those in your tool.

Segments
--------
S1 Publishers · Non-Branded · Informational — good_food, radio_times, what_car
S2 Publishers · Branded                     — same sites
S3 Advertisers · Non-Branded · Commercial   — wickes, starbucks, euro_car_parts
S4 Advertisers · Branded                    — same sites

Google Trends (per site):
  - brand_trends — mean of branded term values (equal weight across terms)
  - nonbrand_info_trends — publishers: mean of own non-branded terms;
    advertisers: publisher-market non-branded index (mean across publisher sites'
    non-branded means, then shared)
  - nonbrand_commercial_trends — advertisers: mean of own non-branded terms;
    publishers: 0

Normalisation:
  For each site and metric M, M_norm = M / mean(M over baseline weeks).
  Baseline default: 2022-06-01 → 2025-03-31.

Columns::

    site, week,
    SEO_sessions, brand_trends, nonbrand_info_trends, nonbrand_commercial_trends,
    SEO_sessions_norm, brand_trends_norm, nonbrand_info_trends_norm,
    nonbrand_commercial_trends_norm,
    log_SEO_sessions_norm, log_brand_trends_norm, log_nonbrand_info_trends_norm,
    log_nonbrand_commercial_trends_norm

Use the ``*_norm`` / ``log_*_norm`` columns in your tool so sites are comparable.
``C(site)`` still works with site-level rows.

Usage::

    python research/analysis/export_segment_model_inputs.py
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

_ANALYSIS_ROOT = Path(__file__).resolve().parent
_RESEARCH_ROOT = _ANALYSIS_ROOT.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from paths import ANALYSIS_OUTPUTS, GA4_WEEKLY, TRENDS_ROOT  # noqa: E402

OUT_DIR = ANALYSIS_OUTPUTS / "segment_model_inputs"

BASELINE_START = "2022-06-01"
BASELINE_END = "2025-03-31"

METRIC_COLS = [
    "SEO_sessions",
    "brand_trends",
    "nonbrand_info_trends",
    "nonbrand_commercial_trends",
]


@dataclass(frozen=True)
class SegmentSpec:
    key: str
    slug: str
    label: str
    sites: tuple[str, ...]


SEGMENTS: tuple[SegmentSpec, ...] = (
    SegmentSpec(
        "S1",
        "publishers_non_branded",
        "Publishers · Non-Branded · Informational",
        ("good_food", "radio_times", "what_car"),
    ),
    SegmentSpec(
        "S2",
        "publishers_branded",
        "Publishers · Branded",
        ("good_food", "radio_times", "what_car"),
    ),
    SegmentSpec(
        "S3",
        "advertisers_non_branded",
        "Advertisers · Non-Branded · Commercial",
        ("wickes", "starbucks", "euro_car_parts"),
    ),
    SegmentSpec(
        "S4",
        "advertisers_branded",
        "Advertisers · Branded",
        ("wickes", "starbucks", "euro_car_parts"),
    ),
)


def _term_slug(term: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", term.strip()).strip("_").lower()
    return slug or "term"


def safe_log1p(x: pd.Series) -> pd.Series:
    x = pd.to_numeric(x, errors="coerce").replace([np.inf, -np.inf], np.nan).clip(lower=0)
    return np.log1p(x)


def load_registry() -> dict:
    return json.loads((TRENDS_ROOT / "term_registry.json").read_text(encoding="utf-8"))


def _read_trends_weekly(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    df = pd.read_csv(path)
    df["week_start"] = pd.to_datetime(df["week_start"])
    return {
        r["week_start"].strftime("%Y-%m-%d"): float(r["google_trends_value"])
        for _, r in df.iterrows()
    }


def _mean_terms_weekly(
    entity_id: str,
    kind_plural: str,
    bucket: str,
    terms: list[str],
) -> dict[str, float]:
    """Equal-weight mean across terms (not sum) for each week."""
    if not terms:
        return {}

    stacks: dict[str, list[float]] = {}
    for term in terms:
        slug = _term_slug(str(term))
        path = (
            TRENDS_ROOT
            / kind_plural
            / entity_id
            / bucket
            / f"{slug}_20220601"
            / "trends_weekly.csv"
        )
        for week, value in _read_trends_weekly(path).items():
            stacks.setdefault(week, []).append(value)

    return {week: float(np.mean(vals)) for week, vals in stacks.items()}


def build_market_publisher_nonbrand_info() -> dict[str, float]:
    """Equal-weight mean of publisher sites' non-branded (term-mean) indices."""
    registry = load_registry()
    site_weeks: dict[str, dict[str, float]] = {}
    for entity_id, spec in (registry.get("entities") or {}).items():
        if str(spec.get("kind") or "publisher").strip().lower() != "publisher":
            continue
        site_weeks[entity_id] = _mean_terms_weekly(
            entity_id, "publishers", "non_branded", spec.get("non_branded") or []
        )

    all_weeks = sorted({w for series in site_weeks.values() for w in series})
    market: dict[str, float] = {}
    for week in all_weeks:
        vals = [series[week] for series in site_weeks.values() if week in series]
        if vals:
            market[week] = float(np.mean(vals))
    return market


def load_seo_sessions(sites: list[str]) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for path in sorted(GA4_WEEKLY.glob("ga4_channel_weekly_*_*.csv")):
        if path.name == "ga4_channel_weekly_combined.csv":
            continue
        df = pd.read_csv(path)
        if "property_name" not in df.columns or "seo_sessions" not in df.columns:
            continue
        site = str(df["property_name"].iloc[0])
        if site not in sites:
            continue
        rows.append(
            df[["property_name", "week", "seo_sessions"]].rename(
                columns={"property_name": "site", "seo_sessions": "SEO_sessions"}
            )
        )
    if not rows:
        raise SystemExit(f"No GA4 weekly CSVs in {GA4_WEEKLY}")
    out = pd.concat(rows, ignore_index=True)
    out = out.drop_duplicates(subset=["site", "week"], keep="first")
    out["week"] = pd.to_datetime(out["week"])
    return out


def build_trends_panel(sites: list[str]) -> pd.DataFrame:
    registry = load_registry()
    entities = registry.get("entities") or {}
    market_info = build_market_publisher_nonbrand_info()
    parts: list[pd.DataFrame] = []

    for entity_id in sites:
        spec = entities[entity_id]
        kind = str(spec.get("kind") or "publisher").strip().lower()
        kind_plural = "advertisers" if kind == "advertiser" else "publishers"
        brand = _mean_terms_weekly(entity_id, kind_plural, "branded", spec.get("branded") or [])
        non_branded = _mean_terms_weekly(
            entity_id, kind_plural, "non_branded", spec.get("non_branded") or []
        )
        if kind == "publisher":
            info = non_branded
            commercial: dict[str, float] = {}
        else:
            info = market_info
            commercial = non_branded

        weeks = sorted(set(brand) | set(info) | set(commercial))
        parts.append(
            pd.DataFrame(
                [
                    {
                        "site": entity_id,
                        "week": week,
                        "brand_trends": brand.get(week, 0.0),
                        "nonbrand_info_trends": info.get(week, 0.0),
                        "nonbrand_commercial_trends": commercial.get(week, 0.0),
                    }
                    for week in weeks
                ]
            )
        )

    out = pd.concat(parts, ignore_index=True)
    out["week"] = pd.to_datetime(out["week"])
    return out


def load_model_panel(sites: list[str]) -> pd.DataFrame:
    return load_seo_sessions(sites).merge(
        build_trends_panel(sites), on=["site", "week"], how="inner"
    )


def add_site_normalisation(
    panel: pd.DataFrame,
    *,
    baseline_start: str = BASELINE_START,
    baseline_end: str = BASELINE_END,
) -> pd.DataFrame:
    """Index each metric to the site's mean over the baseline window (= 1.0)."""
    out = panel.copy()
    base = out[
        (out["week"] >= pd.to_datetime(baseline_start))
        & (out["week"] <= pd.to_datetime(baseline_end))
    ]
    site_means = base.groupby("site", as_index=True)[METRIC_COLS].mean()

    for col in METRIC_COLS:
        mean_col = f"{col}_baseline_mean"
        out = out.merge(
            site_means[[col]].rename(columns={col: mean_col}),
            left_on="site",
            right_index=True,
            how="left",
        )
        denom = out[mean_col].replace(0, np.nan)
        out[f"{col}_norm"] = out[col] / denom
        out[f"log_{col}_norm"] = safe_log1p(out[f"{col}_norm"].clip(lower=0))
        out.drop(columns=[mean_col], inplace=True)

    return out


def segment_long_panel(panel: pd.DataFrame, sites: tuple[str, ...]) -> pd.DataFrame:
    sub = panel[panel["site"].isin(sites)].copy()
    if sub.empty:
        raise SystemExit(f"No rows for sites {sites}")

    cols = [
        "site",
        "week",
        *METRIC_COLS,
        *[f"{c}_norm" for c in METRIC_COLS],
        *[f"log_{c}_norm" for c in METRIC_COLS],
    ]
    return sub[cols].sort_values(["site", "week"]).reset_index(drop=True)


def main() -> None:
    sites = sorted({s for seg in SEGMENTS for s in seg.sites})
    panel = load_model_panel(sites)
    panel = add_site_normalisation(panel)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Panel: {len(panel)} site-weeks | sites={sites}")
    print(
        f"Normalisation: each metric / site mean over "
        f"{BASELINE_START} → {BASELINE_END} (baseline = 1.0)"
    )
    print(f"Trends: mean across terms within brand (not sum)")
    print(f"Writing to: {OUT_DIR}\n")

    for seg in SEGMENTS:
        long = segment_long_panel(panel, seg.sites)
        out = long.copy()
        out["week"] = out["week"].dt.strftime("%Y-%m-%d")
        path = OUT_DIR / f"{seg.key}_{seg.slug}_site_long.csv"
        out.to_csv(path, index=False)

        print(f"{seg.key}  {seg.label}")
        print(f"  sites: {', '.join(seg.sites)}")
        print(f"  rows: {len(out)}  ({out['site'].nunique()} sites × weeks)")
        print(
            f"  SEO_sessions_norm mean: {out['SEO_sessions_norm'].mean():.3f}  "
            f"(≈1.0 if baseline covers most of sample)"
        )
        print(f"  → {path}\n")

    print(
        "Note: S1/S2 share publisher sites (identical long panels); "
        "S3/S4 share advertiser sites."
    )
    print("Done.")


if __name__ == "__main__":
    main()

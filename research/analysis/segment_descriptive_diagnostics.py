#!/usr/bin/env python3
"""
Descriptive diagnostics by segment: Google Trends, sessions, sessions/trend, CVR.

Segments (see user definitions):
  S1 — Publishers, non-branded informational (good_food, radio_times, what_car)
  S2 — Publishers, branded
  S3 — Advertisers, non-branded commercial (wickes, starbucks, euro_car_parts)
  S4 — Advertisers, branded

Sessions = seo + direct + total PPC (branded + non-branded).
Sessions per Trends point = sessions / aggregated Google Trends interest for the
matching branded or non-branded term bucket.

Period change uses long baseline vs evaluation windows from smf.py:
  baseline 2023-01-30 → 2025-03-31
  evaluation 2025-05-26 → 2026-06-14

Outputs under ``research/analysis/outputs/segment_diagnostics/``:
  panel_weekly.csv
  site_metrics.csv
  segment_summary.csv
  charts/*.png
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

_ANALYSIS_ROOT = Path(__file__).resolve().parent
_RESEARCH_ROOT = _ANALYSIS_ROOT.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from paths import GA4_PPC, GA4_WEEKLY, SEGMENT_DIAGNOSTICS, TRENDS_ROOT  # noqa: E402

RESEARCH = _RESEARCH_ROOT
OUT_DIR = SEGMENT_DIAGNOSTICS
CHART_DIR = OUT_DIR / "charts"

BASELINE_START = "2023-01-30"
BASELINE_END = "2025-03-31"
EVAL_START = "2025-05-26"
EVAL_END = "2026-06-14"

METRICS = [
    "google_trends",
    "sessions",
    "sessions_per_trend",
    "purchases",
    "cvr",
]


@dataclass(frozen=True)
class SegmentSpec:
    key: str
    label: str
    sites: tuple[str, ...]
    trends_bucket: str  # branded | non_branded


SEGMENTS: tuple[SegmentSpec, ...] = (
    SegmentSpec(
        "S1",
        "Publishers · Non-Branded · Informational",
        ("good_food", "radio_times", "what_car"),
        "non_branded",
    ),
    SegmentSpec(
        "S2",
        "Publishers · Branded",
        ("good_food", "radio_times", "what_car"),
        "branded",
    ),
    SegmentSpec(
        "S3",
        "Advertisers · Non-Branded · Commercial",
        ("wickes", "starbucks", "euro_car_parts"),
        "non_branded",
    ),
    SegmentSpec(
        "S4",
        "Advertisers · Branded",
        ("wickes", "starbucks", "euro_car_parts"),
        "branded",
    ),
)


def load_registry() -> dict:
    return json.loads((TRENDS_ROOT / "term_registry.json").read_text(encoding="utf-8"))


def _term_slug(term: str) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9]+", "_", term.strip()).strip("_").lower()
    return slug or "term"


def aggregate_trends_by_entity_bucket() -> pd.DataFrame:
    """Sum weekly Google Trends values across all terms in each entity × bucket."""
    registry = load_registry()
    rows: list[dict] = []

    for entity_id, spec in (registry.get("entities") or {}).items():
        kind = str(spec.get("kind") or "publisher").strip().lower()
        kind_plural = "advertisers" if kind == "advertiser" else "publishers"
        for bucket in ("branded", "non_branded"):
            terms = spec.get(bucket) or []
            if not terms:
                continue
            weekly_sum: dict[str, float] = {}
            for term in terms:
                slug = _term_slug(str(term))
                data_dir = TRENDS_ROOT / kind_plural / entity_id / bucket / f"{slug}_20220601"
                trends_path = data_dir / "trends_weekly.csv"
                if not trends_path.is_file():
                    continue
                df = pd.read_csv(trends_path)
                df["week_start"] = pd.to_datetime(df["week_start"])
                for _, r in df.iterrows():
                    w = r["week_start"].strftime("%Y-%m-%d")
                    weekly_sum[w] = weekly_sum.get(w, 0.0) + float(r["google_trends_value"])

            for week, value in sorted(weekly_sum.items()):
                rows.append(
                    {
                        "site": entity_id,
                        "trends_bucket": bucket,
                        "week": week,
                        "google_trends": value,
                    }
                )

    out = pd.DataFrame(rows)
    if out.empty:
        raise SystemExit("No Google Trends data found under trends_manual/")
    out["week"] = pd.to_datetime(out["week"])
    return out.sort_values(["site", "trends_bucket", "week"]).reset_index(drop=True)


CANONICAL_SITES = {
    "good_food",
    "radio_times",
    "what_car",
    "wickes",
    "starbucks",
    "euro_car_parts",
}


def load_ga4_weekly() -> pd.DataFrame:
    """Merge channel weekly (SEO, direct, purchases) with PPC weekly totals."""
    channel_rows: list[pd.DataFrame] = []
    for path in sorted(GA4_WEEKLY.glob("ga4_channel_weekly_*_*.csv")):
        if path.name == "ga4_channel_weekly_combined.csv":
            continue
        df = pd.read_csv(path)
        if "property_name" not in df.columns:
            continue
        site = str(df["property_name"].iloc[0])
        if site not in CANONICAL_SITES:
            continue
        channel_rows.append(
            df[
                ["property_name", "week", "seo_sessions", "direct_sessions", "ecommerce_purchases"]
            ].rename(columns={"property_name": "site", "ecommerce_purchases": "purchases"})
        )

    ppc_rows: list[pd.DataFrame] = []
    for path in sorted(GA4_PPC.glob("ga4_ppc_brand_weekly_*_*.csv")):
        if path.name == "ga4_ppc_brand_weekly_combined.csv":
            continue
        df = pd.read_csv(path)
        if "property_name" not in df.columns:
            continue
        site = str(df["property_name"].iloc[0])
        if site not in CANONICAL_SITES:
            continue
        ppc_rows.append(
            df[["property_name", "week", "total_ppc_sessions"]].rename(
                columns={"property_name": "site"}
            )
        )

    if not channel_rows:
        raise SystemExit(f"No ga4_channel_weekly_*.csv files found in {GA4_WEEKLY}")
    channel = pd.concat(channel_rows, ignore_index=True)
    ppc = pd.concat(ppc_rows, ignore_index=True) if ppc_rows else pd.DataFrame()

    channel = channel.drop_duplicates(subset=["site", "week"], keep="first")
    if not ppc.empty:
        ppc = ppc.drop_duplicates(subset=["site", "week"], keep="first")
        merged = channel.merge(ppc, on=["site", "week"], how="left")
    else:
        merged = channel.copy()
        merged["total_ppc_sessions"] = 0

    merged["total_ppc_sessions"] = merged["total_ppc_sessions"].fillna(0)
    merged["sessions"] = (
        merged["seo_sessions"].fillna(0)
        + merged["direct_sessions"].fillna(0)
        + merged["total_ppc_sessions"]
    )
    merged["week"] = pd.to_datetime(merged["week"])
    return merged.sort_values(["site", "week"]).reset_index(drop=True)


def build_panel(trends: pd.DataFrame, ga4: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for seg in SEGMENTS:
        t = trends[
            (trends["site"].isin(seg.sites)) & (trends["trends_bucket"] == seg.trends_bucket)
        ][["site", "week", "google_trends"]]
        g = ga4[ga4["site"].isin(seg.sites)][
            ["site", "week", "sessions", "purchases", "seo_sessions", "direct_sessions"]
        ]
        part = t.merge(g, on=["site", "week"], how="inner")
        part["segment"] = seg.key
        part["segment_label"] = seg.label
        part["trends_bucket"] = seg.trends_bucket
        part["sessions_per_trend"] = part["sessions"] / part["google_trends"].replace(0, np.nan)
        part["cvr"] = part["purchases"] / part["sessions"].replace(0, np.nan)
        rows.append(part)
    panel = pd.concat(rows, ignore_index=True)
    panel["period"] = np.select(
        [
            (panel["week"] >= pd.to_datetime(BASELINE_START))
            & (panel["week"] <= pd.to_datetime(BASELINE_END)),
            (panel["week"] >= pd.to_datetime(EVAL_START))
            & (panel["week"] <= pd.to_datetime(EVAL_END)),
        ],
        ["baseline", "evaluation"],
        default="other",
    )
    return panel.sort_values(["segment", "site", "week"]).reset_index(drop=True)


def period_means(panel: pd.DataFrame) -> pd.DataFrame:
    return (
        panel[panel["period"].isin(["baseline", "evaluation"])]
        .groupby(["segment", "segment_label", "site", "period"], as_index=False)[METRICS]
        .mean()
    )


def compute_changes(means: pd.DataFrame) -> pd.DataFrame:
    baseline = means[means["period"] == "baseline"].drop(columns=["period"])
    baseline = baseline.rename(columns={m: f"{m}_baseline" for m in METRICS})
    evaluation = means[means["period"] == "evaluation"].drop(columns=["period"])
    evaluation = evaluation.rename(columns={m: f"{m}_eval" for m in METRICS})

    merged = evaluation.merge(baseline, on=["segment", "segment_label", "site"], how="inner")
    for m in METRICS:
        merged[f"{m}_change"] = (
            merged[f"{m}_eval"] / merged[f"{m}_baseline"].replace(0, np.nan) - 1
        )
    return merged


def segment_summary(site_changes: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for seg in SEGMENTS:
        sub = site_changes[site_changes["segment"] == seg.key]
        for m in METRICS:
            col = f"{m}_change"
            valid = sub[col].replace([np.inf, -np.inf], np.nan).dropna()
            if valid.empty:
                continue
            rows.append(
                {
                    "segment": seg.key,
                    "segment_label": seg.label,
                    "metric": m,
                    "median_change_pct": valid.median() * 100,
                    "mean_change_pct": valid.mean() * 100,
                    "sites_increasing": int((valid > 0).sum()),
                    "sites_decreasing": int((valid < 0).sum()),
                    "sites_flat": int((valid == 0).sum()),
                    "n_sites": len(valid),
                }
            )
    return pd.DataFrame(rows)


def indexed_series(panel: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Index metric to each site-segment baseline mean (=1 at baseline)."""
    base = (
        panel[panel["period"] == "baseline"]
        .groupby(["segment", "site"], as_index=False)[metric]
        .mean()
        .rename(columns={metric: "baseline_avg"})
    )
    temp = panel.merge(base, on=["segment", "site"], how="left")
    temp[f"{metric}_index"] = temp[metric] / temp["baseline_avg"].replace(0, np.nan)
    return temp


def plot_segment_charts(panel: pd.DataFrame) -> None:
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    metric_labels = {
        "google_trends": "Google Trends (sum of terms)",
        "sessions": "Sessions (SEO + Direct + PPC)",
        "sessions_per_trend": "Sessions per Trends point",
        "purchases": "Ecommerce purchases",
        "cvr": "CVR (purchases / sessions)",
    }

    for seg in SEGMENTS:
        seg_panel = panel[panel["segment"] == seg.key]
        for metric in METRICS:
            temp = indexed_series(seg_panel, metric)
            weekly_median = (
                temp.groupby("week")[f"{metric}_index"].median().reset_index()
            )

            fig, ax = plt.subplots(figsize=(14, 5))
            for site, site_df in temp.groupby("site"):
                ax.plot(
                    site_df["week"],
                    site_df[f"{metric}_index"],
                    alpha=0.35,
                    linewidth=1,
                    label=site,
                )
            ax.plot(
                weekly_median["week"],
                weekly_median[f"{metric}_index"],
                color="black",
                linewidth=2.5,
                label="Median across sites",
            )
            ax.axhline(1.0, color="grey", linestyle="--", linewidth=1)
            ax.axvline(pd.to_datetime(BASELINE_END), color="grey", linestyle=":", alpha=0.7)
            ax.axvline(pd.to_datetime(EVAL_START), color="crimson", linestyle="--", alpha=0.8)
            ax.set_title(f"{seg.label}\n{metric_labels[metric]} (indexed to baseline mean)")
            ax.set_xlabel("Week")
            ax.set_ylabel("Index (baseline = 1.0)")
            ax.legend(loc="upper left", fontsize=8, ncol=2)
            fig.autofmt_xdate()
            fig.tight_layout()
            out = CHART_DIR / f"{seg.key}_{metric}_indexed.png"
            fig.savefig(out, dpi=120)
            plt.close(fig)

        # Combined overlay for key metrics
        fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True)
        axes = axes.flatten()
        for ax, metric in zip(
            axes, ["google_trends", "sessions", "sessions_per_trend", "cvr"]
        ):
            temp = indexed_series(seg_panel, metric)
            weekly_median = (
                temp.groupby("week")[f"{metric}_index"].median().reset_index()
            )
            for site, site_df in temp.groupby("site"):
                ax.plot(site_df["week"], site_df[f"{metric}_index"], alpha=0.25, linewidth=1)
            ax.plot(
                weekly_median["week"],
                weekly_median[f"{metric}_index"],
                color="black",
                linewidth=2,
                label="Median",
            )
            ax.axhline(1.0, color="grey", linestyle="--", linewidth=0.8)
            ax.axvline(pd.to_datetime(EVAL_START), color="crimson", linestyle="--", alpha=0.7)
            ax.set_title(metric_labels[metric])
            ax.set_ylabel("Index")
        axes[-1].set_xlabel("Week")
        fig.suptitle(f"{seg.label} — metrics indexed to baseline ({BASELINE_START} → {BASELINE_END})")
        fig.tight_layout()
        fig.savefig(CHART_DIR / f"{seg.key}_dashboard.png", dpi=120)
        plt.close(fig)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading Google Trends…")
    trends = aggregate_trends_by_entity_bucket()
    print("Loading GA4 weekly…")
    ga4 = load_ga4_weekly()

    panel = build_panel(trends, ga4)
    panel.to_csv(OUT_DIR / "panel_weekly.csv", index=False)

    means = period_means(panel)
    site_changes = compute_changes(means)
    site_changes.to_csv(OUT_DIR / "site_metrics.csv", index=False)

    summary = segment_summary(site_changes)
    summary.to_csv(OUT_DIR / "segment_summary.csv", index=False)

    print("\n=== Segment summary (eval vs baseline % change) ===")
    for seg in SEGMENTS:
        print(f"\n{seg.key}: {seg.label}")
        sub = summary[summary["segment"] == seg.key]
        if sub.empty:
            print("  (no data)")
            continue
        for _, row in sub.iterrows():
            print(
                f"  {row['metric']:22s}  median {row['median_change_pct']:+.1f}%  "
                f"mean {row['mean_change_pct']:+.1f}%  "
                f"↑{row['sites_increasing']} ↓{row['sites_decreasing']} "
                f"(n={row['n_sites']})"
            )

    print("\nWriting charts…")
    plot_segment_charts(panel)
    print(f"Done. Outputs in {OUT_DIR}")


if __name__ == "__main__":
    main()

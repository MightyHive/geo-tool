#!/usr/bin/env python3
"""
Run segment counterfactuals for SEO, PPC, Direct, and Purchases.

Produces summary CSVs + PNG charts under::

    research/analysis/outputs/segment_counterfactuals/

Spend is not available in GA4 exports. **Lagged PPC sessions** are used as a
spend proxy (avoids contemporaneous circularity with PPC outcomes)::

  brand_PPC_spend_t   ≈ brand_PPC_sessions_{t-1}
  nonbrand_PPC_spend_t ≈ nonbrand_PPC_sessions_{t-1}
  spend_t             ≈ total_PPC_sessions_{t-1}

Defaults match ``seo-sessions-counterfactual.py``:
  - train from 2024-09-01 (+ transition)
  - mask Christmas / Easter weeks
  - sin/cos seasonality; linear trend optional (on by default here for user's specs)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

_ANALYSIS_ROOT = Path(__file__).resolve().parent
_RESEARCH_ROOT = _ANALYSIS_ROOT.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from paths import ANALYSIS_OUTPUTS, GA4_PPC, GA4_WEEKLY, TRENDS_ROOT  # noqa: E402

OUT_DIR = ANALYSIS_OUTPUTS / "segment_counterfactuals"
CHART_DIR = OUT_DIR / "charts"

BASELINE_START = "2022-06-01"
BASELINE_END = "2025-03-31"
TRANSITION_START = "2025-04-01"
TRANSITION_END = "2025-05-25"
EVAL_START = "2025-05-26"
DEFAULT_TRAIN_START = "2024-09-01"


@dataclass(frozen=True)
class SegmentSpec:
    key: str
    slug: str
    label: str
    kind: str  # publisher | advertiser
    sites: tuple[str, ...]


SEGMENTS: tuple[SegmentSpec, ...] = (
    SegmentSpec("S1", "publishers_non_branded", "Publishers · Non-Branded", "publisher",
                ("good_food", "radio_times", "what_car")),
    SegmentSpec("S2", "publishers_branded", "Publishers · Branded", "publisher",
                ("good_food", "radio_times", "what_car")),
    SegmentSpec("S3", "advertisers_non_branded", "Advertisers · Non-Branded", "advertiser",
                ("wickes", "starbucks", "euro_car_parts")),
    SegmentSpec("S4", "advertisers_branded", "Advertisers · Branded", "advertiser",
                ("wickes", "starbucks", "euro_car_parts")),
)


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    label: str
    outcome: str
    predictors: tuple[str, ...]  # level cols that become log_*
    segments: tuple[str, ...]  # segment keys where applicable
    requires_spend: bool = False
    spend_cols: tuple[str, ...] = ()


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        "seo_sessions",
        "1. SEO sessions",
        "SEO_sessions",
        ("brand_trends", "nonbrand_info_trends", "nonbrand_commercial_trends"),
        ("S1", "S2", "S3", "S4"),
    ),
    ModelSpec(
        "ppc_brand",
        "2a. Branded PPC sessions",
        "brand_PPC_sessions",
        ("brand_trends", "brand_PPC_spend"),
        ("S1", "S2", "S3", "S4"),
        requires_spend=True,
        spend_cols=("brand_PPC_spend",),
    ),
    ModelSpec(
        "ppc_nonbrand",
        "2b. Non-branded PPC sessions",
        "nonbrand_PPC_sessions",
        ("nonbrand_trends", "nonbrand_PPC_spend"),
        ("S1", "S2", "S3", "S4"),
        requires_spend=True,
        spend_cols=("nonbrand_PPC_spend",),
    ),
    ModelSpec(
        "direct_sessions",
        "3. Direct sessions (advertisers)",
        "Direct_sessions",
        ("brand_trends",),
        ("S3", "S4"),
    ),
    ModelSpec(
        "purchases",
        "4. Purchases (advertisers)",
        "purchases",
        ("sessions", "trends_volume", "spend"),
        ("S3", "S4"),
        requires_spend=True,
        spend_cols=("spend",),
    ),
)


def _term_slug(term: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", term.strip()).strip("_").lower() or "term"


def safe_log1p(x: pd.Series) -> pd.Series:
    x = pd.to_numeric(x, errors="coerce").replace([np.inf, -np.inf], np.nan).clip(lower=0)
    return np.log1p(x)


def easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def week_holiday_flags(weeks: pd.Series) -> pd.DataFrame:
    weeks = pd.to_datetime(weeks)
    year_min, year_max = int(weeks.min().year), int(weeks.max().year)
    christmas: set[date] = set()
    easter: set[date] = set()
    for year in range(year_min - 1, year_max + 2):
        christmas |= {date(year, 12, d) for d in range(24, 32)}
        christmas.add(date(year + 1, 1, 1))
        es = easter_sunday(year)
        easter |= {es + timedelta(days=o) for o in (-2, -1, 0, 1)}

    c_flags, e_flags = [], []
    for w in weeks:
        days = {w.date() + timedelta(days=i) for i in range(7)}
        c_flags.append(int(bool(days & christmas)))
        e_flags.append(int(bool(days & easter)))
    return pd.DataFrame({"christmas": c_flags, "easter": e_flags}, index=weeks.index)


def load_registry() -> dict:
    return json.loads((TRENDS_ROOT / "term_registry.json").read_text(encoding="utf-8"))


def _mean_terms_weekly(entity_id: str, kind_plural: str, bucket: str, terms: list[str]) -> dict[str, float]:
    if not terms:
        return {}
    stacks: dict[str, list[float]] = {}
    for term in terms:
        path = (
            TRENDS_ROOT / kind_plural / entity_id / bucket
            / f"{_term_slug(str(term))}_20220601" / "trends_weekly.csv"
        )
        if not path.is_file():
            continue
        tdf = pd.read_csv(path)
        tdf["week_start"] = pd.to_datetime(tdf["week_start"])
        for _, r in tdf.iterrows():
            w = r["week_start"].strftime("%Y-%m-%d")
            stacks.setdefault(w, []).append(float(r["google_trends_value"]))
    return {w: float(np.mean(v)) for w, v in stacks.items()}


def build_market_publisher_nonbrand_info() -> dict[str, float]:
    registry = load_registry()
    site_weeks: dict[str, dict[str, float]] = {}
    for entity_id, spec in (registry.get("entities") or {}).items():
        if str(spec.get("kind") or "").lower() != "publisher":
            continue
        site_weeks[entity_id] = _mean_terms_weekly(
            entity_id, "publishers", "non_branded", spec.get("non_branded") or []
        )
    all_weeks = sorted({w for s in site_weeks.values() for w in s})
    return {
        w: float(np.mean([s[w] for s in site_weeks.values() if w in s]))
        for w in all_weeks
    }


def load_ga4_channel(sites: set[str]) -> pd.DataFrame:
    rows = []
    for path in sorted(GA4_WEEKLY.glob("ga4_channel_weekly_*_*.csv")):
        if "combined" in path.name:
            continue
        df = pd.read_csv(path)
        site = str(df["property_name"].iloc[0])
        if site not in sites:
            continue
        rows.append(
            df.rename(columns={
                "property_name": "site",
                "seo_sessions": "SEO_sessions",
                "direct_sessions": "Direct_sessions",
                "ecommerce_purchases": "purchases",
            })[["site", "week", "SEO_sessions", "Direct_sessions", "purchases"]]
        )
    out = pd.concat(rows, ignore_index=True).drop_duplicates(["site", "week"])
    out["week"] = pd.to_datetime(out["week"])
    return out


def load_ga4_ppc(sites: set[str]) -> pd.DataFrame:
    rows = []
    for path in sorted(GA4_PPC.glob("ga4_ppc_brand_weekly_*_*.csv")):
        if "combined" in path.name:
            continue
        df = pd.read_csv(path)
        site = str(df["property_name"].iloc[0])
        if site not in sites:
            continue
        rows.append(
            df.rename(columns={
                "property_name": "site",
                "branded_ppc_sessions": "brand_PPC_sessions",
                "non_branded_ppc_sessions": "nonbrand_PPC_sessions",
                "total_ppc_sessions": "total_PPC_sessions",
            })[["site", "week", "brand_PPC_sessions", "nonbrand_PPC_sessions", "total_PPC_sessions"]]
        )
    if not rows:
        return pd.DataFrame(columns=["site", "week", "brand_PPC_sessions", "nonbrand_PPC_sessions", "total_PPC_sessions"])
    out = pd.concat(rows, ignore_index=True).drop_duplicates(["site", "week"], keep="first")
    out["week"] = pd.to_datetime(out["week"])
    return out


def build_trends_panel(sites: list[str]) -> pd.DataFrame:
    registry = load_registry()
    entities = registry.get("entities") or {}
    market_info = build_market_publisher_nonbrand_info()
    parts = []
    for entity_id in sites:
        spec = entities[entity_id]
        kind = str(spec.get("kind") or "publisher").lower()
        kind_plural = "advertisers" if kind == "advertiser" else "publishers"
        brand = _mean_terms_weekly(entity_id, kind_plural, "branded", spec.get("branded") or [])
        non_branded = _mean_terms_weekly(entity_id, kind_plural, "non_branded", spec.get("non_branded") or [])
        if kind == "publisher":
            info, commercial = non_branded, {}
        else:
            info, commercial = market_info, non_branded
        weeks = sorted(set(brand) | set(info) | set(commercial))
        parts.append(pd.DataFrame([
            {
                "site": entity_id,
                "week": w,
                "brand_trends": brand.get(w, 0.0),
                "nonbrand_info_trends": info.get(w, 0.0),
                "nonbrand_commercial_trends": commercial.get(w, 0.0),
            }
            for w in weeks
        ]))
    out = pd.concat(parts, ignore_index=True)
    out["week"] = pd.to_datetime(out["week"])
    return out


def build_full_panel() -> pd.DataFrame:
    sites = sorted({s for seg in SEGMENTS for s in seg.sites})
    channel = load_ga4_channel(set(sites))
    ppc = load_ga4_ppc(set(sites))
    trends = build_trends_panel(sites)
    panel = channel.merge(ppc, on=["site", "week"], how="left").merge(trends, on=["site", "week"], how="inner")
    for c in ("brand_PPC_sessions", "nonbrand_PPC_sessions", "total_PPC_sessions"):
        panel[c] = panel[c].fillna(0)
    panel["sessions"] = (
        panel["SEO_sessions"].fillna(0)
        + panel["Direct_sessions"].fillna(0)
        + panel["total_PPC_sessions"].fillna(0)
    )
    # Segment-relevant nonbrand trends shortcut
    registry = load_registry()
    kind_map = {
        e: str((registry.get("entities") or {}).get(e, {}).get("kind") or "publisher").lower()
        for e in sites
    }
    panel["nonbrand_trends"] = np.where(
        panel["site"].map(kind_map) == "publisher",
        panel["nonbrand_info_trends"],
        panel["nonbrand_commercial_trends"],
    )
    panel["trends_volume"] = panel["brand_trends"]  # brand demand for purchases model

    # Spend proxy = lagged PPC sessions (by site), to avoid y ~ y circularity
    panel = panel.sort_values(["site", "week"])
    panel["brand_PPC_spend"] = panel.groupby("site")["brand_PPC_sessions"].shift(1)
    panel["nonbrand_PPC_spend"] = panel.groupby("site")["nonbrand_PPC_sessions"].shift(1)
    panel["spend"] = panel.groupby("site")["total_PPC_sessions"].shift(1)

    panel["site_type"] = panel["site"].map(kind_map)
    origin = pd.to_datetime(DEFAULT_TRAIN_START)
    panel["trend"] = (panel["week"] - origin).dt.days / 7
    panel["sin_annual"] = np.sin(2 * np.pi * panel["week"].dt.dayofyear / 365.25)
    panel["cos_annual"] = np.cos(2 * np.pi * panel["week"].dt.dayofyear / 365.25)
    holidays = week_holiday_flags(panel["week"])
    panel["christmas"] = holidays["christmas"].to_numpy()
    panel["easter"] = holidays["easter"].to_numpy()

    panel["period"] = "other"
    panel.loc[
        (panel["week"] >= BASELINE_START) & (panel["week"] <= BASELINE_END), "period"
    ] = "baseline"
    panel.loc[
        (panel["week"] >= TRANSITION_START) & (panel["week"] <= TRANSITION_END), "period"
    ] = "transition"
    panel.loc[panel["week"] >= EVAL_START, "period"] = "evaluation"
    return panel.sort_values(["site", "week"]).reset_index(drop=True)


def _holiday_mask(df: pd.DataFrame) -> pd.Series:
    return (df["christmas"].fillna(0).astype(int) == 1) | (df["easter"].fillna(0).astype(int) == 1)


def run_panel_cf(
    data: pd.DataFrame,
    *,
    outcome: str,
    predictors: list[str],
    train_start: str = DEFAULT_TRAIN_START,
    train_end: str = BASELINE_END,
    include_transition: bool = True,
    include_trend: bool = True,
    mask_holidays: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, dict, object] | tuple[None, None, dict, None]:
    """Fit baseline OLS on log_outcome ~ log_predictors + C(site) + seasonality [+ trend]."""
    df = data.copy()
    log_outcome = f"log_{outcome}"
    df[log_outcome] = safe_log1p(df[outcome])
    log_preds = []
    for p in predictors:
        lp = f"log_{p}"
        df[lp] = safe_log1p(df[p])
        log_preds.append(lp)

    controls = ["sin_annual", "cos_annual"]
    if include_trend:
        controls.append("trend")

    model_cols = [log_outcome, *log_preds, "site", *controls]
    train = (df["week"] >= train_start) & (df["week"] <= train_end)
    if include_transition:
        train = train | (df["period"] == "transition")
    if mask_holidays:
        train = train & ~_holiday_mask(df)

    baseline = df.loc[train].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)
    eval_mask = df["period"] == "evaluation"
    if mask_holidays:
        eval_mask = eval_mask & ~_holiday_mask(df)
    evaluation = df.loc[eval_mask].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)

    notes = []
    if baseline["site"].nunique() < 2:
        notes.append("fewer than 2 sites with complete rows")
    if len(baseline) < 30:
        return None, None, {
            "status": "skipped",
            "reason": f"training rows={len(baseline)} (<30)",
            "notes": "; ".join(notes),
        }, None
    if evaluation.empty:
        return None, None, {
            "status": "skipped",
            "reason": "no evaluation rows",
            "notes": "; ".join(notes),
        }, None

    formula = f"{log_outcome} ~ {' + '.join(log_preds)} + C(site) + {' + '.join(controls)}"
    try:
        model = smf.ols(formula, data=baseline).fit(
            cov_type="cluster", cov_kwds={"groups": baseline["site"]}
        )
    except Exception as exc:  # noqa: BLE001
        return None, None, {"status": "skipped", "reason": f"fit failed: {exc}"}, None

    expected_col = f"expected_{outcome}"
    timeline = df.copy()
    timeline[expected_col] = np.nan
    pred_idx = evaluation.index
    timeline.loc[pred_idx, expected_col] = np.expm1(model.predict(evaluation)).clip(lower=0)
    timeline[f"{outcome}_gap"] = timeline[outcome] - timeline[expected_col]
    timeline[f"{outcome}_gap_pct"] = timeline[f"{outcome}_gap"] / timeline[expected_col].replace(0, np.nan)

    eval_out = timeline.loc[pred_idx].copy()
    actual = float(eval_out[outcome].sum())
    expected = float(eval_out[expected_col].sum())
    gap = actual - expected
    summary = {
        "status": "ok",
        "outcome": outcome,
        "predictors": ",".join(predictors),
        "formula": formula,
        "training_rows": len(baseline),
        "evaluation_rows": len(eval_out),
        "n_sites": int(baseline["site"].nunique()),
        "actual": actual,
        "expected": expected,
        "gap": gap,
        "gap_pct": gap / expected if expected else np.nan,
        "model_r2": float(model.rsquared),
        "notes": "; ".join(notes),
    }
    for p in log_preds + controls:
        summary[f"{p}_coef"] = float(model.params.get(p, np.nan))
        summary[f"{p}_p"] = float(model.pvalues.get(p, np.nan))
    return eval_out, timeline, summary, model


def plot_segment_chart(
    timeline: pd.DataFrame,
    *,
    outcome: str,
    title: str,
    out_path: Path,
) -> None:
    expected_col = f"expected_{outcome}"
    fig, ax = plt.subplots(figsize=(14, 5))
    # Segment median actual / expected across sites
    weekly_actual = timeline.groupby("week")[outcome].median()
    weekly_expected = (
        timeline.dropna(subset=[expected_col]).groupby("week")[expected_col].median()
    )
    ax.axvspan(pd.to_datetime(DEFAULT_TRAIN_START), pd.to_datetime(BASELINE_END),
               color="tab:blue", alpha=0.06, label="Training")
    ax.axvspan(pd.to_datetime(TRANSITION_START), pd.to_datetime(TRANSITION_END),
               color="tab:orange", alpha=0.08, label="Transition")
    ax.axvspan(pd.to_datetime(EVAL_START), timeline["week"].max(),
               color="tab:green", alpha=0.06, label="Evaluation")

    for site, g in timeline.groupby("site"):
        ax.plot(g["week"], g[outcome], alpha=0.25, linewidth=1, label=f"{site} actual")
    ax.plot(weekly_actual.index, weekly_actual.values, color="tab:blue", linewidth=2, label="Median actual")
    if not weekly_expected.empty:
        ax.plot(
            weekly_expected.index,
            weekly_expected.values,
            color="tab:red",
            linestyle="--",
            linewidth=2,
            label="Median expected (eval)",
        )
    ax.axvline(pd.to_datetime(EVAL_START), color="black", linestyle="--", linewidth=0.9)
    ax.set_title(title)
    ax.set_xlabel("Week")
    ax.set_ylabel(outcome)
    ax.legend(loc="upper left", fontsize=7, ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def applicable_segments(model: ModelSpec) -> list[SegmentSpec]:
    return [s for s in SEGMENTS if s.key in model.segments]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run segment counterfactuals")
    parser.add_argument("--no-trend", action="store_true", help="Omit linear trend")
    parser.add_argument("--no-mask-holidays", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    out_dir = args.output_dir.resolve()
    chart_dir = out_dir / "charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    chart_dir.mkdir(parents=True, exist_ok=True)

    print("Building full site×week panel…")
    panel = build_full_panel()
    panel.to_csv(out_dir / "panel_site_week.csv", index=False)
    print(f"  {len(panel)} rows | sites={sorted(panel['site'].unique())}")

    summaries: list[dict] = []

    for model in MODELS:
        print(f"\n===== {model.label} ({model.model_id}) =====")
        predictors = list(model.predictors)
        spend_note = ""
        if model.requires_spend:
            spend_note = (
                "spend proxy = lagged PPC sessions "
                f"({', '.join(model.spend_cols)} ← prior-week PPC sessions)"
            )
            print(f"  NOTE: {spend_note}")

        for seg in applicable_segments(model):
            seg_panel = panel[panel["site"].isin(seg.sites)].copy()
            # Skip outcome if all zero
            if seg_panel[model.outcome].fillna(0).sum() <= 0:
                summaries.append({
                    "model_id": model.model_id,
                    "model_label": model.label,
                    "segment": seg.key,
                    "segment_label": seg.label,
                    "status": "skipped",
                    "reason": "outcome all zero / missing",
                    "spend_note": spend_note,
                })
                print(f"  {seg.key}: skipped (no {model.outcome})")
                continue

            eval_df, timeline, summary, _model = run_panel_cf(
                seg_panel,
                outcome=model.outcome,
                predictors=predictors,
                include_trend=not args.no_trend,
                mask_holidays=not args.no_mask_holidays,
            )
            row = {
                "model_id": model.model_id,
                "model_label": model.label,
                "segment": seg.key,
                "segment_label": seg.label,
                "sites": ",".join(seg.sites),
                "spend_note": spend_note,
                **summary,
            }
            summaries.append(row)

            if summary.get("status") != "ok":
                print(f"  {seg.key}: SKIP — {summary.get('reason')}")
                continue

            stem = f"{model.model_id}_{seg.key}_{seg.slug}"
            eval_df.to_csv(out_dir / f"{stem}_predictions.csv", index=False)
            timeline.to_csv(out_dir / f"{stem}_timeline.csv", index=False)
            plot_segment_chart(
                timeline,
                outcome=model.outcome,
                title=(
                    f"{model.label} — {seg.label}\n"
                    f"Gap {summary['gap_pct']*100:+.1f}% | R²={summary['model_r2']:.2f} | "
                    f"holidays masked | train≥{DEFAULT_TRAIN_START}"
                ),
                out_path=chart_dir / f"{stem}.png",
            )
            print(
                f"  {seg.key}: gap={summary['gap_pct']*100:+.1f}%  "
                f"R²={summary['model_r2']:.2f}  "
                f"actual={summary['actual']:,.0f}  expected={summary['expected']:,.0f}"
            )

    summary_df = pd.DataFrame(summaries)
    summary_path = out_dir / "summary_all_models.csv"
    summary_df.to_csv(summary_path, index=False)

    # Compact markdown findings
    findings = out_dir / "FINDINGS.md"
    lines = [
        "# Segment counterfactual findings",
        "",
        f"Training: `{DEFAULT_TRAIN_START}` → `{BASELINE_END}` (+ transition). "
        f"Evaluation from `{EVAL_START}`. Christmas/Easter weeks masked.",
        "",
        "**Spend proxy:** Real £ spend unavailable. Used **lagged PPC sessions** "
        "(prior week, by site) so PPC outcomes are not regressed on themselves:",
        "",
        "- `brand_PPC_spend_t` ≈ `brand_PPC_sessions_{t-1}`",
        "- `nonbrand_PPC_spend_t` ≈ `nonbrand_PPC_sessions_{t-1}`",
        "- `spend_t` (purchases) ≈ `total_PPC_sessions_{t-1}`",
        "",
        "## Summary table",
        "",
        "| Model | Segment | Status | Gap % | R² | Actual | Expected |",
        "|-------|---------|--------|------:|---:|-------:|---------:|",
    ]
    for _, r in summary_df.iterrows():
        if r.get("status") != "ok":
            lines.append(
                f"| {r.get('model_label','')} | {r.get('segment_label','')} | "
                f"{r.get('status')} — {r.get('reason','')} |  |  |  |  |"
            )
            continue
        lines.append(
            f"| {r['model_label']} | {r['segment_label']} | ok | "
            f"{r['gap_pct']*100:+.1f}% | {r['model_r2']:.2f} | "
            f"{r['actual']:,.0f} | {r['expected']:,.0f} |"
        )
    lines += [
        "",
        "## Interpretation notes",
        "",
        "- Gap % = (actual − expected) / expected over the evaluation window (non-holiday weeks).",
        "- Positive gap ⇒ sessions/purchases **above** the pre-intervention relationship with demand/seasonality.",
        "- S1/S2 share publisher sites (same SEO panel). S3/S4 share advertiser sites.",
        "- For PPC non-brand, `nonbrand_trends` = info trends (publishers) or commercial trends (advertisers).",
        "- Spend proxy is intensity/activity, not £; coefficients are not true ROI.",
        "- Charts: `charts/{model}_{segment}_*.png`",
        "",
    ]
    findings.write_text("\n".join(lines), encoding="utf-8")

    print(f"\nWrote {summary_path}")
    print(f"Wrote {findings}")
    print(f"Charts in {chart_dir}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Site-level AI-era counterfactuals from combined weekly panels.

Models (log1p outcomes; sin/cos seasonality + linear trend; holidays masked)::

  SEO sessions          ~ branded_trends + nonbranded_trends
  Branded PPC sessions  ~ branded_trends + branded_ppc_spend_proxy
  Non-branded PPC       ~ nonbranded_trends + nonbrand_ppc_spend_proxy
  Direct sessions       ~ branded_trends
  Total sessions        ~ branded_trends + nonbranded_trends
  Purchases             ~ total_sessions + traffic mix + branded/nonbranded trends

Spend proxy = lagged PPC sessions (branded / non-branded) by site.

Training: 2024-09-01 → 2025-03-31 (+ transition to 2025-05-25).
Evaluation: from 2025-05-26. Weeks with is_holiday=1 are excluded.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

_ANALYSIS_ROOT = Path(__file__).resolve().parent
_RESEARCH_ROOT = _ANALYSIS_ROOT.parent
_GA4_ROOT = _RESEARCH_ROOT / "ga4"
for _p in (_ANALYSIS_ROOT, _RESEARCH_ROOT, _GA4_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from paths import ANALYSIS_OUTPUTS, GA4_EXPORTS, GA4_PROPERTIES  # noqa: E402

OUT_DIR = ANALYSIS_OUTPUTS / "site_channel_counterfactuals"
COMBINED_DIR = GA4_EXPORTS / "combined"

DEFAULT_TRAIN_START = "2024-09-01"
BASELINE_END = "2025-03-31"
TRANSITION_START = "2025-04-01"
TRANSITION_END = "2025-05-25"
EVAL_START = "2025-05-26"


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    label: str
    outcome: str
    predictors: tuple[str, ...]


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        "seo_sessions",
        "SEO sessions",
        "seo_sessions",
        ("branded_trends_mean", "nonbranded_trends_mean"),
    ),
    ModelSpec(
        "branded_ppc",
        "Branded PPC sessions",
        "branded_ppc_sessions",
        ("branded_trends_mean", "branded_ppc_spend"),
    ),
    ModelSpec(
        "nonbranded_ppc",
        "Non-branded PPC sessions",
        "non_branded_ppc_sessions",
        ("nonbranded_trends_mean", "nonbranded_ppc_spend"),
    ),
    ModelSpec(
        "direct_sessions",
        "Direct sessions",
        "direct_sessions",
        ("branded_trends_mean",),
    ),
    ModelSpec(
        "total_sessions",
        "Total sessions",
        "total_sessions",
        ("branded_trends_mean", "nonbranded_trends_mean"),
    ),
    ModelSpec(
        "purchases",
        "Purchases",
        "total_purchases",
        (
            "total_sessions",
            "seo_share",
            "direct_share",
            "branded_ppc_share",
            "branded_trends_mean",
            "nonbranded_trends_mean",
        ),
    ),
)


def safe_log1p(x: pd.Series) -> pd.Series:
    x = pd.to_numeric(x, errors="coerce").replace([np.inf, -np.inf], np.nan).clip(lower=0)
    return np.log1p(x)


def prepare_panel(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["week"] = pd.to_datetime(out["week"])
    out = out.sort_values("week").reset_index(drop=True)

    # Spend proxies
    out["branded_ppc_spend"] = out["branded_ppc_sessions"].shift(1)
    out["nonbranded_ppc_spend"] = out["non_branded_ppc_sessions"].shift(1)

    # Traffic mix shares (of total_sessions)
    tot = out["total_sessions"].replace(0, np.nan)
    out["seo_share"] = out["seo_sessions"] / tot
    out["direct_share"] = out["direct_sessions"] / tot
    out["branded_ppc_share"] = out["branded_ppc_sessions"] / tot
    out["nonbranded_ppc_share"] = out["non_branded_ppc_sessions"] / tot

    origin = pd.to_datetime(DEFAULT_TRAIN_START)
    out["trend"] = (out["week"] - origin).dt.days / 7.0
    out["sin_annual"] = np.sin(2 * np.pi * out["week"].dt.dayofyear / 365.25)
    out["cos_annual"] = np.cos(2 * np.pi * out["week"].dt.dayofyear / 365.25)

    out["period"] = "other"
    out.loc[
        (out["week"] >= "2022-06-01") & (out["week"] <= BASELINE_END), "period"
    ] = "baseline"
    out.loc[
        (out["week"] >= TRANSITION_START) & (out["week"] <= TRANSITION_END), "period"
    ] = "transition"
    out.loc[out["week"] >= EVAL_START, "period"] = "evaluation"

    if "is_holiday" not in out.columns:
        out["is_holiday"] = 0
    return out


def run_site_cf(
    data: pd.DataFrame,
    *,
    outcome: str,
    predictors: list[str],
    min_train: int = 20,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, dict]:
    df = data.copy()
    log_y = f"log_{outcome}"
    df[log_y] = safe_log1p(df[outcome])

    log_preds: list[str] = []
    level_preds: list[str] = []  # shares stay in levels
    for p in predictors:
        if p.endswith("_share"):
            level_preds.append(p)
        else:
            lp = f"log_{p}"
            df[lp] = safe_log1p(df[p])
            log_preds.append(lp)

    controls = ["sin_annual", "cos_annual", "trend"]
    rhs_terms = log_preds + level_preds + controls
    model_cols = [log_y, *rhs_terms]

    train = ((df["week"] >= DEFAULT_TRAIN_START) & (df["week"] <= BASELINE_END)) | (
        df["period"] == "transition"
    )
    train = train & (df["is_holiday"].fillna(0).astype(int) == 0)
    eval_mask = (df["period"] == "evaluation") & (
        df["is_holiday"].fillna(0).astype(int) == 0
    )

    baseline = df.loc[train].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)
    evaluation = (
        df.loc[eval_mask].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)
    )

    if len(baseline) < min_train:
        return None, None, {
            "status": "skipped",
            "reason": f"training rows={len(baseline)} (<{min_train})",
        }
    if evaluation.empty:
        return None, None, {"status": "skipped", "reason": "no evaluation rows"}

    # Drop near-constant predictors
    usable = []
    for t in rhs_terms:
        s = baseline[t]
        if s.nunique(dropna=True) < 2 or float(s.std() or 0) <= 1e-12:
            continue
        usable.append(t)
    if not usable:
        return None, None, {"status": "skipped", "reason": "no usable predictors"}

    formula = f"{log_y} ~ {' + '.join(usable)}"
    try:
        model = smf.ols(formula, data=baseline).fit(cov_type="HC3")
    except Exception as exc:  # noqa: BLE001
        return None, None, {"status": "skipped", "reason": f"fit failed: {exc}"}

    expected_col = f"expected_{outcome}"
    timeline = df.copy()
    timeline[expected_col] = np.nan
    pred_idx = evaluation.index
    timeline.loc[pred_idx, expected_col] = np.expm1(model.predict(evaluation)).clip(lower=0)
    timeline[f"{outcome}_gap"] = timeline[outcome] - timeline[expected_col]
    timeline[f"{outcome}_gap_pct"] = timeline[f"{outcome}_gap"] / timeline[
        expected_col
    ].replace(0, np.nan)

    eval_out = timeline.loc[pred_idx].copy()
    actual = float(eval_out[outcome].sum())
    expected = float(eval_out[expected_col].sum())
    gap = actual - expected
    gap_pct = gap / expected if expected and abs(expected) > 1e-9 else np.nan
    r2 = float(model.rsquared)
    if not np.isfinite(r2):
        r2 = np.nan

    summary = {
        "status": "ok" if np.isfinite(gap_pct) else "unreliable",
        "outcome": outcome,
        "formula": formula,
        "predictors": ",".join(predictors),
        "training_rows": len(baseline),
        "evaluation_rows": len(eval_out),
        "actual": actual,
        "expected": expected,
        "gap": gap,
        "gap_pct": gap_pct,
        "model_r2": r2,
    }
    for t in usable:
        summary[f"{t}_coef"] = float(model.params.get(t, np.nan))
        summary[f"{t}_p"] = float(model.pvalues.get(t, np.nan))
    return eval_out, timeline, summary


def plot_cf(
    timeline: pd.DataFrame,
    *,
    outcome: str,
    title: str,
    out_path: Path,
) -> None:
    expected_col = f"expected_{outcome}"
    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.axvspan(
        pd.to_datetime(DEFAULT_TRAIN_START),
        pd.to_datetime(BASELINE_END),
        color="tab:blue",
        alpha=0.06,
        label="Training",
    )
    ax.axvspan(
        pd.to_datetime(TRANSITION_START),
        pd.to_datetime(TRANSITION_END),
        color="tab:orange",
        alpha=0.08,
        label="Transition",
    )
    ax.axvspan(
        pd.to_datetime(EVAL_START),
        timeline["week"].max(),
        color="tab:green",
        alpha=0.06,
        label="Evaluation",
    )
    ax.plot(timeline["week"], timeline[outcome], color="tab:blue", linewidth=1.6, label="Actual")
    ge = timeline.dropna(subset=[expected_col])
    if not ge.empty:
        ax.plot(
            ge["week"],
            ge[expected_col],
            color="tab:red",
            linestyle="--",
            linewidth=1.6,
            label="Expected (eval)",
        )
    ax.axvline(pd.to_datetime(EVAL_START), color="black", linestyle="--", linewidth=0.9)
    ax.set_title(title)
    ax.set_xlabel("Week")
    ax.set_ylabel(outcome)
    ax.legend(loc="upper left", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def discover_combined_csvs() -> list[Path]:
    return sorted(COMBINED_DIR.glob("ga4_trends_weekly_*.csv"))


def site_from_path(path: Path) -> tuple[str, str]:
    # ga4_trends_weekly_{name}_{id}.csv
    stem = path.stem.replace("ga4_trends_weekly_", "")
    parts = stem.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0], parts[1]
    return stem, ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Site channel counterfactuals")
    parser.add_argument(
        "--combined-dir",
        type=Path,
        default=COMBINED_DIR,
        help="Directory of ga4_trends_weekly_*.csv panels",
    )
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    parser.add_argument(
        "--build-panels",
        action="store_true",
        help="Run build_weekly_combined_panel.py --all first",
    )
    args = parser.parse_args()

    if args.build_panels:
        from build_weekly_combined_panel import main as build_main

        sys.argv = ["build_weekly_combined_panel.py", "--all"]
        build_main()

    out_dir = args.output_dir.resolve()
    chart_dir = out_dir / "charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    chart_dir.mkdir(parents=True, exist_ok=True)

    paths = sorted(args.combined_dir.glob("ga4_trends_weekly_*.csv"))
    if not paths:
        raise SystemExit(f"No combined CSVs in {args.combined_dir}")

    summaries: list[dict] = []

    for path in paths:
        site_slug, prop_id = site_from_path(path)
        print(f"\n===== {site_slug} ({prop_id}) =====")
        raw = pd.read_csv(path)
        panel = prepare_panel(raw)

        for model in MODELS:
            # Skip purchases if all zero
            if panel[model.outcome].fillna(0).sum() <= 0:
                summaries.append(
                    {
                        "site": site_slug,
                        "property_id": prop_id,
                        "model_id": model.model_id,
                        "model_label": model.label,
                        "status": "skipped",
                        "reason": "outcome all zero",
                    }
                )
                print(f"  {model.model_id}: skipped (zero outcome)")
                continue

            preds = list(model.predictors)
            # Drop spend if all NA
            preds = [p for p in preds if p in panel.columns and panel[p].notna().sum() > 5]
            if not preds and model.predictors:
                summaries.append(
                    {
                        "site": site_slug,
                        "property_id": prop_id,
                        "model_id": model.model_id,
                        "model_label": model.label,
                        "status": "skipped",
                        "reason": "no usable predictors",
                    }
                )
                continue

            eval_df, timeline, summary = run_site_cf(
                panel, outcome=model.outcome, predictors=preds
            )
            row = {
                "site": site_slug,
                "property_id": prop_id,
                "model_id": model.model_id,
                "model_label": model.label,
                **summary,
            }
            summaries.append(row)

            if summary.get("status") not in ("ok", "unreliable"):
                print(f"  {model.model_id}: SKIP — {summary.get('reason')}")
                continue

            stem = f"{site_slug}_{model.model_id}"
            if eval_df is not None:
                eval_df.to_csv(out_dir / f"{stem}_predictions.csv", index=False)
            if timeline is not None:
                timeline.to_csv(out_dir / f"{stem}_timeline.csv", index=False)
                gap = summary.get("gap_pct")
                gap_txt = f"{gap*100:+.1f}%" if gap is not None and np.isfinite(gap) else "n/a"
                r2 = summary.get("model_r2")
                r2_txt = f"{r2:.2f}" if r2 is not None and np.isfinite(r2) else "n/a"
                plot_cf(
                    timeline,
                    outcome=model.outcome,
                    title=(
                        f"{model.label} — {site_slug}\n"
                        f"Gap {gap_txt} | R²={r2_txt} | holidays masked"
                    ),
                    out_path=chart_dir / f"{stem}.png",
                )
            gap = summary.get("gap_pct")
            r2 = summary.get("model_r2")
            gap_txt = f"{gap*100:+.1f}%" if gap is not None and np.isfinite(gap) else "n/a"
            r2_txt = f"{r2:.2f}" if r2 is not None and np.isfinite(r2) else "n/a"
            print(f"  {model.model_id}: gap={gap_txt}  R²={r2_txt}")

    summary_df = pd.DataFrame(summaries)
    summary_path = out_dir / "summary_by_site_model.csv"
    summary_df.to_csv(summary_path, index=False)

    # Pivot gap table
    ok = summary_df[summary_df["status"].isin(["ok", "unreliable"])].copy()
    if not ok.empty:
        pivot = ok.pivot_table(
            index="site", columns="model_label", values="gap_pct", aggfunc="first"
        )
        pivot = (pivot * 100).round(1)
        pivot.to_csv(out_dir / "gap_pct_pivot.csv")
        print("\nGap % pivot (eval, holidays masked):")
        print(pivot.to_string())

    findings = out_dir / "FINDINGS.md"
    lines = [
        "# Site channel counterfactuals",
        "",
        f"Train `{DEFAULT_TRAIN_START}`→`{BASELINE_END}` (+ transition). "
        f"Eval from `{EVAL_START}`. Holidays masked (`is_holiday`).",
        "",
        "Spend proxy: lagged branded / non-branded PPC sessions.",
        "",
        "## Gap % by site × model",
        "",
    ]
    if not ok.empty:
        # Avoid pandas.to_markdown (needs tabulate)
        cols = ["site", *list(pivot.columns)]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for site, row in pivot.iterrows():
            cells = [str(site)] + [
                f"{v:.1f}" if pd.notna(v) else "n/a" for v in row.values
            ]
            lines.append("| " + " | ".join(cells) + " |")
    lines += ["", f"Full summary: `{summary_path.name}`", ""]
    findings.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {summary_path}")
    print(f"Wrote {findings}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
AI traffic: direct measured impact + exposure-proxy counterfactuals.

A. Direct measured impact
   - Direct_AI_sessions = sum(AI_sessions) over evaluation weeks
   - non_AI_sessions = Total_sessions - AI_sessions
   - Counterfactual on non_AI_sessions only (avoids double-counting AI)

B. AI exposure proxy (broader adoption / visibility)
   - AI_share = AI_sessions / Total_sessions
   - AI_growth = Δ log(1 + AI_sessions)
   - AI_adstock[t] = AI_sessions[t] + decay * AI_adstock[t-1]
     decays tried: 0.3, 0.5, 0.7, 0.9

For B, outcome models for non_AI / SEO / Direct / Purchases are fit with
Trends + seasonality + trend + chosen AI exposure. An \"AI-held\" counterfactual
sets exposure to the training-window mean during evaluation (what non-AI traffic
would look like if AI exposure stayed at pre-eval levels).

Default site: euro_car_parts (only property with new-channel AI sessions so far).
"""

from __future__ import annotations

import argparse
import sys
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

from paths import ANALYSIS_OUTPUTS, GA4_EXPORTS  # noqa: E402
from run_site_channel_counterfactuals import (  # noqa: E402
    BASELINE_END,
    DEFAULT_TRAIN_START,
    EVAL_START,
    TRANSITION_END,
    TRANSITION_START,
    prepare_panel,
    safe_log1p,
)

OUT_DIR = ANALYSIS_OUTPUTS / "ai_exposure_counterfactuals"
COMBINED_DIR = GA4_EXPORTS / "combined"
ADSTOCK_DECAYS = (0.3, 0.5, 0.7, 0.9)


def add_ai_features(df: pd.DataFrame, decays: tuple[float, ...] = ADSTOCK_DECAYS) -> pd.DataFrame:
    out = df.copy()
    out["ai_sessions"] = out["ai_sessions"].fillna(0).astype(float)
    out["total_sessions"] = out["total_sessions"].fillna(0).astype(float)
    out["non_ai_sessions"] = (out["total_sessions"] - out["ai_sessions"]).clip(lower=0)

    tot = out["total_sessions"].replace(0, np.nan)
    out["ai_share"] = (out["ai_sessions"] / tot).fillna(0.0)
    out["log_ai"] = safe_log1p(out["ai_sessions"])
    out["ai_growth"] = out["log_ai"].diff()

    for d in decays:
        col = f"ai_adstock_{str(d).replace('.', '')}"
        vals = []
        prev = 0.0
        for x in out["ai_sessions"].tolist():
            prev = float(x) + d * prev
            vals.append(prev)
        out[col] = vals
        # Lag exposure one week for outcome models (reduces same-week reverse causality)
        out[f"{col}_lag"] = out[col].shift(1)
    out["ai_share_lag"] = out["ai_share"].shift(1)
    out["ai_growth_lag"] = out["ai_growth"].shift(1)
    return out


def _masks(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    train = ((df["week"] >= DEFAULT_TRAIN_START) & (df["week"] <= BASELINE_END)) | (
        df["period"] == "transition"
    )
    train = train & (df["is_holiday"].fillna(0).astype(int) == 0)
    eval_mask = (df["period"] == "evaluation") & (
        df["is_holiday"].fillna(0).astype(int) == 0
    )
    return train, eval_mask


def fit_cf(
    df: pd.DataFrame,
    *,
    outcome: str,
    predictors: list[str],
    hold_predictors: dict[str, float] | None = None,
    min_train: int = 20,
) -> dict:
    """
    Fit log1p OLS. If hold_predictors is set, eval expected uses those fixed values
    (e.g. AI exposure held at train mean).
    """
    data = df.copy()
    log_y = f"log_{outcome}"
    data[log_y] = safe_log1p(data[outcome])

    rhs: list[str] = []
    for p in predictors:
        if p.endswith("_share") or p.endswith("_growth") or p.endswith("_growth_lag"):
            # already approximately log-ish / bounded — keep level
            if p not in data.columns:
                continue
            rhs.append(p)
        else:
            lp = f"log_{p}" if not p.startswith("log_") else p
            src = p[4:] if p.startswith("log_") else p
            if src not in data.columns and p not in data.columns:
                continue
            if not p.startswith("log_"):
                data[lp] = safe_log1p(data[p])
            else:
                data[lp] = data[p] if p in data.columns else safe_log1p(data[src])
                lp = p if p.startswith("log_") else lp
            rhs.append(lp if not p.startswith("log_") else p)

    controls = ["sin_annual", "cos_annual", "trend"]
    rhs = [t for t in rhs + controls if t in data.columns]

    train, eval_mask = _masks(data)
    model_cols = [log_y, *rhs]
    baseline = data.loc[train].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)
    evaluation = (
        data.loc[eval_mask].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)
    )

    usable = []
    for t in rhs:
        s = baseline[t]
        if s.nunique(dropna=True) < 2 or float(s.std() or 0) <= 1e-12:
            continue
        usable.append(t)
    if len(baseline) < min_train or evaluation.empty or not usable:
        return {
            "status": "skipped",
            "reason": (
                f"train={len(baseline)} eval={len(evaluation)} usable={usable}"
            ),
        }

    formula = f"{log_y} ~ {' + '.join(usable)}"
    model = smf.ols(formula, data=baseline).fit(cov_type="HC3")

    pred_frame = evaluation.copy()
    if hold_predictors:
        for raw_name, value in hold_predictors.items():
            # Map to the column used in the formula
            if raw_name in pred_frame.columns:
                pred_frame[raw_name] = value
            log_name = f"log_{raw_name}"
            if log_name in usable:
                pred_frame[log_name] = np.log1p(max(value, 0.0))

    expected = np.clip(np.expm1(np.asarray(model.predict(pred_frame), dtype=float)), 0, None)
    actual = float(evaluation[outcome].sum())
    expected_sum = float(np.sum(expected))
    gap = actual - expected_sum
    gap_pct = gap / expected_sum if expected_sum else np.nan

    coefs = {t: float(model.params.get(t, np.nan)) for t in usable}
    pvals = {t: float(model.pvalues.get(t, np.nan)) for t in usable}

    return {
        "status": "ok",
        "outcome": outcome,
        "formula": formula,
        "training_rows": len(baseline),
        "evaluation_rows": len(evaluation),
        "actual": actual,
        "expected": expected_sum,
        "gap": gap,
        "gap_pct": gap_pct,
        "model_r2": float(model.rsquared),
        "coefs": coefs,
        "pvalues": pvals,
        "hold_predictors": hold_predictors or {},
    }


def pick_best_adstock(df: pd.DataFrame, outcome: str, base_preds: list[str]) -> tuple[str, dict]:
    """Choose adstock decay by training R² when added to base model."""
    best_col = ""
    best = {"model_r2": -np.inf}
    for d in ADSTOCK_DECAYS:
        col = f"ai_adstock_{str(d).replace('.', '')}_lag"
        res = fit_cf(df, outcome=outcome, predictors=base_preds + [col])
        if res.get("status") != "ok":
            continue
        if res["model_r2"] > best["model_r2"]:
            best = res
            best_col = col
    return best_col, best


def plot_actual_expected(
    df: pd.DataFrame,
    *,
    outcome: str,
    expected: pd.Series,
    title: str,
    path: Path,
) -> None:
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
        df["week"].max(),
        color="tab:green",
        alpha=0.06,
        label="Evaluation",
    )
    ax.plot(df["week"], df[outcome], color="tab:blue", label="Actual")
    ax.plot(df["week"], expected, color="tab:red", linestyle="--", label="Expected")
    ax.axvline(pd.to_datetime(EVAL_START), color="black", linestyle="--", linewidth=0.9)
    ax.set_title(title)
    ax.legend(fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def run_site(path: Path, out_dir: Path) -> None:
    site = path.stem.replace("ga4_trends_weekly_", "")
    raw = pd.read_csv(path)
    if raw.get("ai_sessions", pd.Series(dtype=float)).fillna(0).sum() <= 0:
        print(f"{site}: no AI sessions — skip")
        return

    panel = add_ai_features(prepare_panel(raw))
    train, eval_mask = _masks(panel)
    eval_df = panel.loc[eval_mask]

    # ----- A. Direct measured impact -----
    direct_ai = float(eval_df["ai_sessions"].sum())
    non_ai_actual = float(eval_df["non_ai_sessions"].sum())
    total_actual = float(eval_df["total_sessions"].sum())

    base_trends = ["branded_trends_mean", "nonbranded_trends_mean"]
    non_ai_base = fit_cf(panel, outcome="non_ai_sessions", predictors=base_trends)
    total_base = fit_cf(panel, outcome="total_sessions", predictors=base_trends)
    ai_sessions_model = fit_cf(
        panel, outcome="ai_sessions", predictors=["branded_trends_mean", "nonbranded_trends_mean"]
    )

    rows: list[dict] = []
    rows.append(
        {
            "block": "A_direct",
            "metric": "direct_ai_sessions_eval_sum",
            "value": direct_ai,
            "note": "Observed AI referral sessions in eval (holidays masked)",
        }
    )
    rows.append(
        {
            "block": "A_direct",
            "metric": "non_ai_sessions_eval_sum",
            "value": non_ai_actual,
        }
    )
    rows.append(
        {
            "block": "A_direct",
            "metric": "ai_share_of_total_eval",
            "value": direct_ai / total_actual if total_actual else np.nan,
        }
    )

    def _ai_stats(res: dict) -> tuple[float, float]:
        coefs = res.get("coefs") or {}
        pvals = res.get("pvalues") or {}
        keys = [
            k
            for k in coefs
            if k.startswith("log_ai_")
            or k.startswith("ai_adstock")
            or k.startswith("ai_share")
            or k.startswith("ai_growth")
        ]
        if not keys:
            return np.nan, np.nan
        k = keys[0]
        return float(coefs.get(k, np.nan)), float(pvals.get(k, np.nan))

    def pack(block: str, label: str, res: dict) -> None:
        ai_coef, ai_p = _ai_stats(res)
        rows.append(
            {
                "block": block,
                "model": label,
                "status": res.get("status"),
                "gap_pct": res.get("gap_pct"),
                "model_r2": res.get("model_r2"),
                "actual": res.get("actual"),
                "expected": res.get("expected"),
                "formula": res.get("formula"),
                "ai_coef": ai_coef,
                "ai_p": ai_p,
                "hold": str(res.get("hold_predictors") or {}),
                "reason": res.get("reason", ""),
            }
        )

    pack("A_indirect", "non_ai_sessions (trends only)", non_ai_base)
    pack("A_indirect", "total_sessions (trends only)", total_base)
    pack("A_direct", "ai_sessions (trends only CF)", ai_sessions_model)

    # ----- B. Exposure proxy -----
    outcomes = {
        "non_ai_sessions": base_trends,
        "seo_sessions": base_trends,
        "direct_sessions": ["branded_trends_mean"],
        "total_purchases": [
            "non_ai_sessions",
            "seo_share",
            "direct_share",
            "branded_ppc_share",
            "branded_trends_mean",
            "nonbranded_trends_mean",
        ],
    }

    adstock_choice: dict[str, str] = {}
    for outcome, preds in outcomes.items():
        best_col, best_res = pick_best_adstock(panel, outcome, preds)
        adstock_choice[outcome] = best_col
        pack("B_select_adstock", f"{outcome} + best adstock", best_res)

        # Compare exposure specs
        for spec_name, extra in [
            ("ai_share_lag", ["ai_share_lag"]),
            ("ai_growth_lag", ["ai_growth_lag"]),
            *[(f"adstock_{d}", [f"ai_adstock_{str(d).replace('.', '')}_lag"]) for d in ADSTOCK_DECAYS],
        ]:
            res = fit_cf(panel, outcome=outcome, predictors=preds + extra)
            pack("B_exposure_compare", f"{outcome} | {spec_name}", res)

        # Held-AI CF using best adstock (exposure fixed at train mean)
        if best_col:
            train_mean = float(panel.loc[train, best_col].mean())
            held = fit_cf(
                panel,
                outcome=outcome,
                predictors=preds + [best_col],
                hold_predictors={best_col: train_mean},
            )
            pack(
                "B_held_exposure",
                f"{outcome} | {best_col} held@train_mean={train_mean:.2f}",
                held,
            )

    summary = pd.DataFrame(rows)
    site_dir = out_dir / site
    site_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(site_dir / "ai_analysis_summary.csv", index=False)

    # Findings markdown
    lines = [
        f"# AI direct + exposure analysis — {site}",
        "",
        f"Train `{DEFAULT_TRAIN_START}`→`{BASELINE_END}` (+ transition). "
        f"Eval from `{EVAL_START}`. Holidays masked.",
        "",
        "## A. Direct measured impact",
        "",
        f"- **Direct AI sessions (eval sum):** {direct_ai:,.0f}",
        f"- **Non-AI sessions (eval sum):** {non_ai_actual:,.0f}",
        f"- **AI share of total (eval):** {100 * direct_ai / total_actual:.2f}%",
        "",
        "Indirect effect estimated on `non_AI_sessions = Total − AI` (no double-counting).",
        "",
    ]
    if non_ai_base.get("status") == "ok":
        lines += [
            f"- **Non-AI sessions gap:** {non_ai_base['gap_pct']*100:+.1f}% "
            f"(R²={non_ai_base['model_r2']:.2f})",
            f"  - actual {non_ai_base['actual']:,.0f} vs expected {non_ai_base['expected']:,.0f}",
        ]
    if total_base.get("status") == "ok":
        lines.append(
            f"- **Total sessions gap (incl. AI):** {total_base['gap_pct']*100:+.1f}% "
            f"(R²={total_base['model_r2']:.2f})"
        )
    lines += ["", "## B. AI exposure proxy", ""]
    lines.append(
        "Exposure vars: `AI_share`, `AI_growth=Δlog1p(AI)`, "
        "`AI_adstock` with decay ∈ {0.3,0.5,0.7,0.9} (lagged 1 week in outcome models)."
    )
    lines.append("")
    lines.append("### Best adstock by outcome (train R²)")
    lines.append("")
    lines.append("| Outcome | Best exposure | Gap % | R² | AI coef | AI p |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
    for outcome in outcomes:
        sub = summary[
            (summary["block"] == "B_select_adstock")
            & (summary["model"].str.startswith(outcome))
        ]
        if sub.empty:
            continue
        r = sub.iloc[0]
        gap = r["gap_pct"]
        gap_s = f"{gap*100:+.1f}" if pd.notna(gap) else "n/a"
        coef = r["ai_coef"]
        coef_s = f"{coef:+.3f}" if pd.notna(coef) else "n/a"
        p = r["ai_p"]
        p_s = f"{p:.3f}" if pd.notna(p) else "n/a"
        lines.append(
            f"| {outcome} | {adstock_choice.get(outcome,'')} | {gap_s}% | "
            f"{r['model_r2']:.2f} | {coef_s} | {p_s} |"
        )

    lines += ["", "### Held-exposure CF (AI adstock fixed at train mean in eval)", ""]
    lines.append("| Outcome | Gap % vs held-AI expected | R² |")
    lines.append("| --- | ---: | ---: |")
    held = summary[summary["block"] == "B_held_exposure"]
    for _, r in held.iterrows():
        gap = r["gap_pct"]
        gap_s = f"{gap*100:+.1f}" if pd.notna(gap) else "n/a"
        lines.append(f"| {r['model']} | {gap_s}% | {r['model_r2']:.2f} |")

    lines += [
        "",
        "## Interpretation notes",
        "",
        "- **A direct:** AI sessions are a small observed referral volume; "
        "do not add them on top of a Total-sessions gap that already includes AI.",
        "- **A indirect:** gap on non_AI_sessions is the channel impact excluding measured AI referrals.",
        "- **B:** AI exposure proxies broader AI adoption. Positive AI coef ⇒ higher exposure "
        "associated with higher outcome (conditional on Trends/seasonality); held-AI CF asks "
        "what the outcome would be if exposure stayed at training-period levels.",
        "",
    ]
    (site_dir / "FINDINGS.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {site_dir / 'ai_analysis_summary.csv'}")
    print(f"Wrote {site_dir / 'FINDINGS.md'}")
    print("\n".join(lines[:40]))


def main() -> None:
    parser = argparse.ArgumentParser(description="AI direct + exposure counterfactuals")
    parser.add_argument(
        "--combined",
        type=Path,
        default=COMBINED_DIR / "ga4_trends_weekly_euro_car_parts_241379560.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    if not args.combined.is_file():
        raise SystemExit(f"Missing combined panel: {args.combined}")
    run_site(args.combined, args.output_dir.resolve())


if __name__ == "__main__":
    main()

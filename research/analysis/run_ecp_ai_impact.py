#!/usr/bin/env python3
"""
Euro Car Parts — site-level AI impact pipeline.

Process
-------
1. Weekly panel (pre-built combined export)
2. Features: non_AI_sessions, AI_share, AI_adstock, channel shares, CVR
3. Pre-AI counterfactual models (non-AI, SEO, Direct, branded/non-branded PPC, purchases)
4. AI-exposure models (AI_adstock + Trends + seasonality + trend)
5. Predict actual AI vs no-AI (train-mean AI_adstock baseline)
6. Placebo backtests on pre-AI windows
7. Duan smearing correction for log1p models
8. Block bootstrap (4–8 week blocks)
9. Site-level ranges (this script — ECP only)
10. Multi-site aggregation: deferred until other sites have measured AI

Outputs: research/analysis/outputs/ecp_ai_impact/
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

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
from run_ai_exposure_counterfactuals import ADSTOCK_DECAYS, add_ai_features  # noqa: E402
from run_site_channel_counterfactuals import (  # noqa: E402
    BASELINE_END,
    DEFAULT_TRAIN_START,
    EVAL_START,
    prepare_panel,
    safe_log1p,
)

OUT_DIR = ANALYSIS_OUTPUTS / "ecp_ai_impact"
COMBINED = GA4_EXPORTS / "combined" / "ga4_trends_weekly_euro_car_parts_241379560.csv"
DEFAULT_DECAY = 0.7
N_BOOT = 500
BLOCK_SIZES = (4, 5, 6, 7, 8)
RNG = np.random.default_rng(42)

# Placebo: rolling eval windows entirely before transition, matching real eval length
PLACEBO_TRAIN_WEEKS = 30
PLACEBO_MIN_GAP_WEEKS = 2  # gap between train end and placebo eval start


@dataclass
class FitBundle:
    status: str
    formula: str = ""
    actual: float = np.nan
    expected: float = np.nan
    gap: float = np.nan
    gap_pct: float = np.nan
    r2: float = np.nan
    n_train: int = 0
    n_eval: int = 0
    smear: float = np.nan
    params: dict = field(default_factory=dict)
    pvalues: dict = field(default_factory=dict)
    week_actual: np.ndarray = field(default_factory=lambda: np.array([]))
    week_expected: np.ndarray = field(default_factory=lambda: np.array([]))
    reason: str = ""


def train_eval_masks(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    train = ((df["week"] >= DEFAULT_TRAIN_START) & (df["week"] <= BASELINE_END)) | (
        df["period"] == "transition"
    )
    train = train & (df["is_holiday"].fillna(0).astype(int) == 0)
    ev = (df["period"] == "evaluation") & (df["is_holiday"].fillna(0).astype(int) == 0)
    return train, ev


def enrich(df: pd.DataFrame, decay: float = DEFAULT_DECAY) -> pd.DataFrame:
    """Step 2 — feature construction."""
    out = add_ai_features(prepare_panel(df), decays=ADSTOCK_DECAYS)
    base = f"ai_adstock_{str(decay).replace('.', '')}"
    out["ai_adstock"] = out[base]
    out["ai_adstock_l0"] = out["ai_adstock"]
    out["ai_adstock_l1"] = out["ai_adstock"].shift(1)
    out["ai_adstock_l2"] = out["ai_adstock"].shift(2)
    out["ai_adstock_l4"] = out["ai_adstock"].shift(4)

    denom = out["non_ai_sessions"].replace(0, np.nan)
    out["seo_share"] = out["seo_sessions"] / denom
    out["direct_share"] = out["direct_sessions"] / denom
    ppc = out["branded_ppc_sessions"].fillna(0) + out["non_branded_ppc_sessions"].fillna(0)
    out["ppc_sessions"] = ppc
    out["ppc_share"] = ppc / denom
    # aliases used by efficiency model
    out["seo_share_nonai"] = out["seo_share"]
    out["direct_share_nonai"] = out["direct_share"]
    out["ppc_share_nonai"] = out["ppc_share"]

    out["cvr"] = out["total_purchases"] / denom
    return out


def _build_design(
    df: pd.DataFrame, outcome: str, predictors: list[str]
) -> tuple[pd.DataFrame, str, list[str]]:
    data = df.copy()
    log_y = f"log_{outcome}"
    data[log_y] = safe_log1p(data[outcome])
    rhs: list[str] = []
    for p in predictors:
        if p.endswith("_share") or p.endswith("_share_nonai") or p == "ai_share":
            rhs.append(p)
            continue
        lp = f"log_{p}"
        data[lp] = safe_log1p(data[p])
        rhs.append(lp)
    controls = ["sin_annual", "cos_annual", "trend"]
    return data, log_y, rhs + controls


def _usable(baseline: pd.DataFrame, candidates: list[str]) -> list[str]:
    return [
        c
        for c in candidates
        if baseline[c].nunique(dropna=True) >= 2 and float(baseline[c].std() or 0) > 1e-12
    ]


def _smear_factor(resid: np.ndarray) -> float:
    """Duan smearing: mean(exp(e)) for log1p errors → E[y+1] ≈ exp(xb)*s."""
    return float(np.mean(np.exp(np.asarray(resid, dtype=float))))


def _level_from_xb(xb: np.ndarray, smear: float) -> np.ndarray:
    """Invert log1p with smearing: ŷ = exp(xb)*smear − 1."""
    return np.clip(np.exp(np.asarray(xb, dtype=float)) * smear - 1.0, 0, None)


def _fit_ols(
    data: pd.DataFrame,
    *,
    log_y: str,
    usable: list[str],
    fit_mask: pd.Series,
    eval_mask: pd.Series,
    outcome: str,
    overrides: dict[str, float] | None = None,
) -> FitBundle:
    model_cols = [log_y, *usable]
    baseline = data.loc[fit_mask].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)
    evaluation = data.loc[eval_mask].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)
    usable = _usable(baseline, usable)
    if len(baseline) < 20 or evaluation.empty or not usable:
        return FitBundle(
            status="skipped",
            reason=f"train={len(baseline)} eval={len(evaluation)} usable={len(usable)}",
        )

    formula = f"{log_y} ~ {' + '.join(usable)}"
    model = smf.ols(formula, data=baseline).fit(cov_type="HC3")
    smear = _smear_factor(np.asarray(model.resid, dtype=float))

    pred = evaluation.copy()
    if overrides:
        for name, val in overrides.items():
            if name in pred.columns:
                pred[name] = val
            lp = f"log_{name}"
            if lp in usable:
                pred[lp] = np.log1p(max(float(val), 0.0))

    xb = np.asarray(model.predict(pred), dtype=float)
    expected = _level_from_xb(xb, smear)
    actual_w = evaluation[outcome].to_numpy(dtype=float)
    actual = float(actual_w.sum())
    exp = float(expected.sum())
    gap = actual - exp
    return FitBundle(
        status="ok",
        formula=formula,
        actual=actual,
        expected=exp,
        gap=gap,
        gap_pct=gap / exp if exp else np.nan,
        r2=float(model.rsquared),
        n_train=len(baseline),
        n_eval=len(evaluation),
        smear=smear,
        params={k: float(v) for k, v in model.params.items()},
        pvalues={k: float(v) for k, v in model.pvalues.items()},
        week_actual=actual_w,
        week_expected=expected,
    )


def fit_preai_cf(
    df: pd.DataFrame,
    *,
    outcome: str,
    predictors: list[str],
    train_mask: pd.Series | None = None,
    eval_mask: pd.Series | None = None,
) -> FitBundle:
    """Step 3 — pre-AI counterfactual (train pre-AI, predict eval)."""
    data, log_y, candidates = _build_design(df, outcome, predictors)
    train, ev = train_eval_masks(data)
    if train_mask is not None:
        train = train_mask
    if eval_mask is not None:
        ev = eval_mask
    return _fit_ols(
        data,
        log_y=log_y,
        usable=candidates,
        fit_mask=train,
        eval_mask=ev,
        outcome=outcome,
    )


def fit_exposure_pair(
    df: pd.DataFrame,
    *,
    outcome: str,
    predictors: list[str],
    adstock_col: str,
    baseline_value: float,
) -> tuple[FitBundle, FitBundle, float]:
    """
    Steps 4–5 — fit on all non-holiday weeks; predict eval with actual AI vs baseline AI.
    Returns (fit_actual_summary, effect_as_FitBundle_on_diff, ai_coef).
    """
    data, log_y, candidates = _build_design(df, outcome, predictors)
    _, ev = train_eval_masks(data)
    fit_mask = data["is_holiday"].fillna(0).astype(int) == 0

    with_ai = _fit_ols(
        data,
        log_y=log_y,
        usable=candidates,
        fit_mask=fit_mask,
        eval_mask=ev,
        outcome=outcome,
        overrides=None,
    )
    without = _fit_ols(
        data,
        log_y=log_y,
        usable=candidates,
        fit_mask=fit_mask,
        eval_mask=ev,
        outcome=outcome,
        overrides={adstock_col: baseline_value},
    )
    if with_ai.status != "ok" or without.status != "ok":
        return with_ai, without, np.nan

    week_effect = with_ai.week_expected - without.week_expected
    point = float(week_effect.sum())
    ai_coef = np.nan
    for k, v in with_ai.params.items():
        if "ai_adstock" in k:
            ai_coef = v
            break

    effect = FitBundle(
        status="ok",
        formula=with_ai.formula,
        actual=with_ai.actual,
        expected=without.expected,  # no-AI counterfactual level
        gap=point,
        gap_pct=point / without.expected if without.expected else np.nan,
        r2=with_ai.r2,
        n_train=with_ai.n_train,
        n_eval=with_ai.n_eval,
        smear=with_ai.smear,
        params=with_ai.params,
        pvalues=with_ai.pvalues,
        week_actual=with_ai.week_expected,  # predicted with AI
        week_expected=without.week_expected,  # predicted without
    )
    return with_ai, effect, float(ai_coef) if np.isfinite(ai_coef) else np.nan


def block_bootstrap_sum(
    week_values: np.ndarray,
    *,
    n_boot: int = N_BOOT,
    block_sizes: tuple[int, ...] = BLOCK_SIZES,
) -> dict:
    """Step 8 — resample contiguous 4–8 week blocks with replacement."""
    n = len(week_values)
    if n < min(block_sizes):
        return {
            "point": float(week_values.sum()) if n else np.nan,
            "p10": np.nan,
            "p50": np.nan,
            "p90": np.nan,
            "p025": np.nan,
            "p975": np.nan,
        }

    draws = []
    for _ in range(n_boot):
        b = int(RNG.choice(block_sizes))
        b = min(b, n)
        n_blocks = int(np.ceil(n / b))
        starts = RNG.integers(0, n - b + 1, size=n_blocks)
        pieces = [week_values[s : s + b] for s in starts]
        sample = np.concatenate(pieces)[:n]
        draws.append(float(sample.sum()))
    arr = np.asarray(draws)
    return {
        "point": float(week_values.sum()),
        "p10": float(np.quantile(arr, 0.10)),
        "p50": float(np.quantile(arr, 0.50)),
        "p90": float(np.quantile(arr, 0.90)),
        "p025": float(np.quantile(arr, 0.025)),
        "p975": float(np.quantile(arr, 0.975)),
    }


def placebo_cf_backtest(
    df: pd.DataFrame,
    *,
    outcome: str,
    predictors: list[str],
    eval_len: int,
    stride: int = 4,
) -> pd.DataFrame:
    """
    Step 6 — pre-AI placebo windows.

    Slide a train → gap → eval block through non-holiday weeks that end before
    real EVAL_START (stride weeks apart to reduce overlap). Compare real CF
    gaps to this null on both level and %.
    """
    work = df.sort_values("week").reset_index(drop=True)
    work["week"] = pd.to_datetime(work["week"])
    nh = work[work["is_holiday"].fillna(0).astype(int) == 0].copy()
    cutoff = pd.Timestamp(EVAL_START)
    rows: list[dict] = []

    positions = nh.index.to_numpy()
    need = PLACEBO_TRAIN_WEEKS + PLACEBO_MIN_GAP_WEEKS + eval_len
    if len(positions) < need:
        return pd.DataFrame()

    for end_i in range(need - 1, len(positions), max(stride, 1)):
        eval_pos = positions[end_i - eval_len + 1 : end_i + 1]
        train_end = end_i - eval_len - PLACEBO_MIN_GAP_WEEKS
        train_pos = positions[train_end - PLACEBO_TRAIN_WEEKS + 1 : train_end + 1]
        if len(eval_pos) < eval_len or len(train_pos) < PLACEBO_TRAIN_WEEKS:
            continue
        eval_end = work.loc[eval_pos[-1], "week"]
        if eval_end >= cutoff:
            continue

        train_weeks = set(work.loc[train_pos, "week"])
        eval_weeks = set(work.loc[eval_pos, "week"])
        w = pd.to_datetime(df["week"])
        train_m = w.isin(train_weeks) & (df["is_holiday"].fillna(0).astype(int) == 0)
        eval_m = w.isin(eval_weeks) & (df["is_holiday"].fillna(0).astype(int) == 0)
        res = fit_preai_cf(
            df,
            outcome=outcome,
            predictors=predictors,
            train_mask=train_m,
            eval_mask=eval_m,
        )
        if res.status != "ok":
            continue
        rows.append(
            {
                "outcome": outcome,
                "placebo_eval_end": eval_end.date().isoformat(),
                "n_train": res.n_train,
                "n_eval": res.n_eval,
                "gap": res.gap,
                "gap_pct": res.gap_pct,
                "r2": res.r2,
                "smear": res.smear,
            }
        )
    return pd.DataFrame(rows)


def fmt(n: float) -> str:
    if n is None or not np.isfinite(n):
        return "n/a"
    if abs(n) >= 100:
        return f"{n:,.0f}"
    return f"{n:.3f}"


def pct(n: float) -> str:
    if n is None or not np.isfinite(n):
        return "n/a"
    return f"{n * 100:+.1f}%"


def ai_param(params: dict, pvalues: dict) -> tuple[float, float]:
    for k, v in params.items():
        if "ai_adstock" in k:
            return float(v), float(pvalues.get(k, np.nan))
    return np.nan, np.nan


def main() -> None:
    parser = argparse.ArgumentParser(description="ECP AI impact (new process)")
    parser.add_argument("--combined", type=Path, default=COMBINED)
    parser.add_argument("--decay", type=float, default=DEFAULT_DECAY)
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--n-boot", type=int, default=N_BOOT)
    parser.add_argument("--skip-placebo", action="store_true")
    args = parser.parse_args()

    if not args.combined.is_file():
        raise SystemExit(f"Missing {args.combined}")

    out_dir = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(args.combined)
    df = enrich(raw, decay=args.decay)
    train, ev = train_eval_masks(df)
    eval_df = df.loc[ev]
    direct_ai = float(eval_df["ai_sessions"].sum())
    eval_len = int(ev.sum())

    records: list[dict] = []

    # ---------- Step 3: pre-AI counterfactuals ----------
    cf_specs = {
        "non_ai_sessions": ["branded_trends_mean", "nonbranded_trends_mean"],
        "seo_sessions": ["branded_trends_mean", "nonbranded_trends_mean"],
        "direct_sessions": ["branded_trends_mean"],
        "branded_ppc_sessions": ["branded_trends_mean"],
        "non_branded_ppc_sessions": ["nonbranded_trends_mean"],
        "total_purchases": ["branded_trends_mean", "nonbranded_trends_mean"],
    }
    cf: dict[str, FitBundle] = {}
    cf_boot: dict[str, dict] = {}
    for outcome, preds in cf_specs.items():
        res = fit_preai_cf(df, outcome=outcome, predictors=preds)
        cf[outcome] = res
        records.append(
            {
                "section": f"cf_{outcome}",
                "status": res.status,
                "gap": res.gap,
                "gap_pct": res.gap_pct,
                "actual": res.actual,
                "expected": res.expected,
                "r2": res.r2,
                "smear": res.smear,
                "n_train": res.n_train,
                "n_eval": res.n_eval,
                "formula": res.formula,
            }
        )
        if res.status == "ok":
            week_gap = res.week_actual - res.week_expected
            cf_boot[outcome] = block_bootstrap_sum(week_gap, n_boot=args.n_boot)

    m1 = cf["non_ai_sessions"]
    purch_cf = cf["total_purchases"]

    # ---------- Steps 4–5: AI-exposure models ----------
    lag_specs = {
        "lag0": "ai_adstock_l0",
        "lag1": "ai_adstock_l1",
        "lag2": "ai_adstock_l2",
        "lag4": "ai_adstock_l4",
    }
    exposure: dict[str, dict] = {}
    for lag_name, col in lag_specs.items():
        preds = ["branded_trends_mean", "nonbranded_trends_mean", col]
        baseline_ai = float(df.loc[train, col].dropna().mean())
        with_ai, effect, coef = fit_exposure_pair(
            df,
            outcome="non_ai_sessions",
            predictors=preds,
            adstock_col=col,
            baseline_value=baseline_ai,
        )
        boot = (
            block_bootstrap_sum(
                effect.week_actual - effect.week_expected, n_boot=args.n_boot
            )
            if effect.status == "ok"
            else {}
        )
        pval = with_ai.pvalues.get(
            next((k for k in with_ai.params if "ai_adstock" in k), ""), np.nan
        )
        exposure[lag_name] = {
            "coef": coef,
            "p": pval,
            "baseline_ai": baseline_ai,
            "effect": effect.gap if effect.status == "ok" else np.nan,
            "effect_pct": effect.gap_pct if effect.status == "ok" else np.nan,
            "smear": with_ai.smear,
            "r2": with_ai.r2,
            "boot": boot,
            "with_ai": with_ai,
            "effect_bundle": effect,
        }
        records.append(
            {
                "section": f"exposure_{lag_name}",
                "coef": coef,
                "p": pval,
                "effect": exposure[lag_name]["effect"],
                "baseline_ai": baseline_ai,
                "smear": with_ai.smear,
                **{f"boot_{k}": v for k, v in boot.items()},
            }
        )

    central_lag = "lag1"
    if not np.isfinite(exposure[central_lag].get("effect", np.nan)):
        central_lag = next(k for k, v in exposure.items() if np.isfinite(v.get("effect", np.nan)))

    # Purchase efficiency + central exposure
    purch_eff_preds = [
        "non_ai_sessions",
        "seo_share_nonai",
        "direct_share_nonai",
        "ppc_share_nonai",
        "branded_trends_mean",
        "nonbranded_trends_mean",
        lag_specs[central_lag],
    ]
    purch_base = float(df.loc[train, lag_specs[central_lag]].dropna().mean())
    _, purch_eff_effect, purch_eff_coef = fit_exposure_pair(
        df,
        outcome="total_purchases",
        predictors=purch_eff_preds,
        adstock_col=lag_specs[central_lag],
        baseline_value=purch_base,
    )
    purch_eff_boot = (
        block_bootstrap_sum(
            purch_eff_effect.week_actual - purch_eff_effect.week_expected,
            n_boot=args.n_boot,
        )
        if purch_eff_effect.status == "ok"
        else {}
    )

    purch_central_preds = [
        "branded_trends_mean",
        "nonbranded_trends_mean",
        lag_specs[central_lag],
    ]
    _, purch_central_effect, _ = fit_exposure_pair(
        df,
        outcome="total_purchases",
        predictors=purch_central_preds,
        adstock_col=lag_specs[central_lag],
        baseline_value=purch_base,
    )

    # ---------- Purchase decomposition ----------
    act_sess, exp_sess = m1.actual, m1.expected
    act_purch, exp_purch = purch_cf.actual, purch_cf.expected
    exp_cvr = exp_purch / exp_sess if exp_sess else np.nan
    act_cvr = act_purch / act_sess if act_sess else np.nan
    volume_effect = (act_sess - exp_sess) * exp_cvr
    cvr_effect = act_sess * (act_cvr - exp_cvr)

    # ---------- Step 6: placebo backtests ----------
    placebo_frames = []
    if not args.skip_placebo:
        for outcome, preds in cf_specs.items():
            pdf = placebo_cf_backtest(
                df, outcome=outcome, predictors=preds, eval_len=eval_len
            )
            if not pdf.empty:
                placebo_frames.append(pdf)
        if placebo_frames:
            placebo_all = pd.concat(placebo_frames, ignore_index=True)
            placebo_all.to_csv(out_dir / "placebo_backtests.csv", index=False)
        else:
            placebo_all = pd.DataFrame()
    else:
        placebo_all = pd.DataFrame()

    # ---------- Step 9: site-level ranges ----------
    signed = []
    if m1.status == "ok":
        signed.append(("cf_non_ai", m1.gap))
    for lag_name, info in exposure.items():
        if np.isfinite(info.get("effect", np.nan)):
            signed.append((f"exposure_{lag_name}", info["effect"]))
    vals = [v for _, v in signed]
    low = float(np.min(vals)) if vals else np.nan
    high = float(np.max(vals)) if vals else np.nan
    central = float(np.median(vals)) if vals else np.nan
    central_pct = central / m1.expected if m1.expected else np.nan

    ci_sources = []
    if "non_ai_sessions" in cf_boot:
        ci_sources.append(cf_boot["non_ai_sessions"])
    if exposure[central_lag].get("boot"):
        ci_sources.append(exposure[central_lag]["boot"])
    if ci_sources:
        range_80 = (
            float(np.min([c["p10"] for c in ci_sources])),
            float(np.max([c["p90"] for c in ci_sources])),
        )
        range_95 = (
            float(np.min([c["p025"] for c in ci_sources])),
            float(np.max([c["p975"] for c in ci_sources])),
        )
    else:
        range_80 = (np.nan, np.nan)
        range_95 = (np.nan, np.nan)

    net_ai = direct_ai + central
    gross_ai = direct_ai + abs(central)

    purch_impacts = []
    if purch_cf.status == "ok":
        purch_impacts.append(purch_cf.gap)
    if purch_eff_effect.status == "ok":
        purch_impacts.append(purch_eff_effect.gap)
    if purch_central_effect.status == "ok":
        purch_impacts.append(purch_central_effect.gap)
    purch_low = float(np.min(purch_impacts)) if purch_impacts else np.nan
    purch_high = float(np.max(purch_impacts)) if purch_impacts else np.nan
    purch_central = float(np.median(purch_impacts)) if purch_impacts else np.nan

    # Placebo summary for non_AI (+ SEO diagnosis)
    placebo_note = "n/a"
    if not placebo_all.empty and m1.status == "ok":
        bits = []
        for outcome, label, real in (
            ("non_ai_sessions", "non_AI", m1),
            ("seo_sessions", "SEO", cf["seo_sessions"]),
            ("total_purchases", "purchases", purch_cf),
        ):
            p_o = placebo_all[placebo_all["outcome"] == outcome]
            if p_o.empty or real.status != "ok":
                continue
            p_left = float((p_o["gap_pct"] <= real.gap_pct).mean())
            p_abs = float((p_o["gap_pct"].abs() >= abs(real.gap_pct)).mean())
            bits.append(
                f"{label}: n={len(p_o)}, median {pct(p_o['gap_pct'].median())}, "
                f"p(≤real)={p_left:.2f}, share|%|≥|real|={p_abs:.2f}"
            )
        placebo_note = "; ".join(bits) if bits else "n/a"
        placebo_note += (
            ". If share|%|≥|real|≈1, the real CF gap is **not** unusual vs pre-AI forecast error."
        )

    # Save tables
    pd.DataFrame(
        [
            {"key": "direct_ai_sessions", "value": direct_ai},
            {"key": "indirect_central", "value": central},
            {"key": "indirect_low", "value": low},
            {"key": "indirect_high", "value": high},
            {"key": "net_ai_impact_sessions", "value": net_ai},
            {"key": "gross_ai_associated_sessions", "value": gross_ai},
            {"key": "cf_non_ai_gap", "value": m1.gap},
            {"key": "cf_non_ai_gap_pct", "value": m1.gap_pct},
            {"key": "cf_smear", "value": m1.smear},
            {"key": "sessions_80_low", "value": range_80[0]},
            {"key": "sessions_80_high", "value": range_80[1]},
            {"key": "sessions_95_low", "value": range_95[0]},
            {"key": "sessions_95_high", "value": range_95[1]},
            {"key": "purchases_cf_gap", "value": purch_cf.gap},
            {"key": "purchases_volume_effect", "value": volume_effect},
            {"key": "purchases_cvr_effect", "value": cvr_effect},
            {"key": "purchases_central", "value": purch_central},
            {"key": "purchases_low", "value": purch_low},
            {"key": "purchases_high", "value": purch_high},
            {"key": "adstock_decay", "value": args.decay},
            {"key": "central_lag", "value": float(central_lag.replace("lag", "") or 1)},
            {"key": "n_boot", "value": float(args.n_boot)},
        ]
    ).to_csv(out_dir / "summary_metrics.csv", index=False)

    pd.DataFrame(records).to_csv(out_dir / "model_runs.csv", index=False)

    ch_rows = []
    for name in (
        "seo_sessions",
        "direct_sessions",
        "branded_ppc_sessions",
        "non_branded_ppc_sessions",
    ):
        res = cf[name]
        boot = cf_boot.get(name, {})
        ch_rows.append(
            {
                "channel": name,
                "gap": res.gap,
                "gap_pct": res.gap_pct,
                "actual": res.actual,
                "expected": res.expected,
                "r2": res.r2,
                "smear": res.smear,
                "boot_p10": boot.get("p10"),
                "boot_p90": boot.get("p90"),
            }
        )
    pd.DataFrame(ch_rows).to_csv(out_dir / "channel_gaps.csv", index=False)

    # Feature snapshot for audit
    feat_cols = [
        "week",
        "period",
        "is_holiday",
        "total_sessions",
        "ai_sessions",
        "non_ai_sessions",
        "ai_share",
        "ai_adstock",
        "seo_sessions",
        "direct_sessions",
        "branded_ppc_sessions",
        "non_branded_ppc_sessions",
        "seo_share",
        "direct_share",
        "ppc_share",
        "cvr",
        "total_purchases",
        "branded_trends_mean",
        "nonbranded_trends_mean",
    ]
    df[feat_cols].to_csv(out_dir / "features_weekly.csv", index=False)

    # ----- FINDINGS.md -----
    coef = exposure[central_lag]["coef"]
    p_ai = exposure[central_lag]["p"]
    lines = [
        "# Euro Car Parts — AI impact (site-level)",
        "",
        f"Train `{DEFAULT_TRAIN_START}`→`{BASELINE_END}` (+ transition). "
        f"Eval from `{EVAL_START}`. Holidays masked. Adstock decay={args.decay}.",
        "",
        "Method: log1p OLS + **Duan smearing**; **4–8 week block bootstrap**; "
        "exposure no-AI = **train-mean AI_adstock**; full non-holiday fit for exposure.",
        "",
        "AI-attributed purchases unavailable → total purchases only (no double-count).",
        "",
        "## Sessions",
        "",
        "| Estimate | Sessions | % of expected |",
        "| --- | ---: | ---: |",
        f"| Direct measured AI sessions | {fmt(direct_ai)} | — |",
        f"| Indirect impact (central = median A+B) | {fmt(central)} | {pct(central_pct)} |",
        f"| **Total net AI impact** (direct + signed indirect) | **{fmt(net_ai)}** | — |",
        f"| Gross AI-associated traffic (direct + |indirect|) | {fmt(gross_ai)} | — |",
        f"| 80% block-bootstrap range | {fmt(range_80[0])} to {fmt(range_80[1])} | — |",
        f"| 95% block-bootstrap range | {fmt(range_95[0])} to {fmt(range_95[1])} | — |",
        "",
        f"Credible span: low={fmt(low)}, central={fmt(central)}, high={fmt(high)}.  ",
        f"Estimate A (pre-AI CF) alone: {fmt(m1.gap)} ({pct(m1.gap_pct)}), smear={m1.smear:.3f}.",
        "",
        "### Estimate A — Pre-AI counterfactual",
        "",
        f"- non_AI: actual {fmt(m1.actual)} vs expected {fmt(m1.expected)} "
        f"(R²={m1.r2:.2f}, smear={m1.smear:.3f})",
        f"- Block-bootstrap 80% CI: {fmt(cf_boot.get('non_ai_sessions', {}).get('p10'))} to "
        f"{fmt(cf_boot.get('non_ai_sessions', {}).get('p90'))}",
        "",
        "### Estimate B — AI-exposure (actual − train-mean AI)",
        "",
        f"Central lag **{central_lag}**: coef={coef:+.3f} (p={p_ai:.3f}).",
        "",
        "| Lag | Coef | p | Indirect sessions | 80% CI |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for lag_name, info in exposure.items():
        boot = info.get("boot") or {}
        lines.append(
            f"| {lag_name} | {info['coef']:+.3f} | {info['p']:.3f} | "
            f"{fmt(info['effect'])} | {fmt(boot.get('p10'))} to {fmt(boot.get('p90'))} |"
        )

    lines += [
        "",
        "### Channel counterfactuals (diagnosis — do not sum with total)",
        "",
        "| Channel | Gap | Gap % | R² | smear | 80% CI |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in ch_rows:
        lines.append(
            f"| {row['channel']} | {fmt(row['gap'])} | {pct(row['gap_pct'])} | "
            f"{row['r2']:.2f} | {row['smear']:.3f} | "
            f"{fmt(row['boot_p10'])} to {fmt(row['boot_p90'])} |"
        )

    lines += [
        "",
        "### Placebo backtest (pre-AI windows)",
        "",
        f"- {placebo_note}",
        "",
        "## Purchases",
        "",
        "| Estimate | Purchases |",
        "| --- | ---: |",
        f"| Total purchase counterfactual gap | {fmt(purch_cf.gap)} |",
        f"| Session-mediated (volume) component | {fmt(volume_effect)} |",
        f"| Conversion/quality (CVR) component | {fmt(cvr_effect)} |",
        f"| Central AI-associated impact | {fmt(purch_central)} |",
        f"| Range across specs | {fmt(purch_low)} to {fmt(purch_high)} |",
        "",
        "### Decomposition (Purchases ≈ Sessions × CVR)",
        "",
        f"- Expected CVR = {exp_cvr:.5f}; actual CVR = {act_cvr:.5f}",
        f"- Volume = (act − exp sessions) × exp CVR = **{fmt(volume_effect)}**",
        f"- CVR = act sessions × (act − exp CVR) = **{fmt(cvr_effect)}**",
        f"- Check: {fmt(volume_effect + cvr_effect)} vs gap {fmt(act_purch - exp_purch)}",
        "",
        "### Purchase efficiency (sessions + mix + AI adstock)",
        "",
        f"- AI_adstock coef: {purch_eff_coef:+.3f}"
        if np.isfinite(purch_eff_coef)
        else "- AI_adstock coef: n/a",
        f"- Estimated AI purchase impact: {fmt(purch_eff_effect.gap)} "
        f"[80% {fmt(purch_eff_boot.get('p10'))} to {fmt(purch_eff_boot.get('p90'))}]",
        "",
        "## Notes",
        "",
        "- Site-level only (euro_car_parts). Do not aggregate across sites yet.",
        "- Smearing: ŷ = exp(xβ)·mean(exp(e)) − 1 for log1p outcomes.",
        "- Block bootstrap resamples contiguous 4–8 week blocks of eval week contributions.",
        "- Exposure models are observational; CF gaps = all unexplained AI-period change.",
        "",
    ]
    text = "\n".join(lines)
    (out_dir / "FINDINGS.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"\nWrote outputs to {out_dir}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Euro Car Parts — SEO sessions counterfactual.

Important data note
-------------------
Google changed GSC impression measurement in **September 2025**.
Impressions before that date are **not comparable** to later weeks, so we do
**not** use GSC impressions (or any model that spans that break on impressions).

Primary robust covariates (full GA4 history)
-------------------------------------------
  Direct sessions + Other sessions  (site activity / demand proxies)
  + branded / non-branded Google Trends
  + seasonality + trend

Specs
-----
A. Trends only
   Train: panel start → pre-eval, mask BF → Twelfth Night
   Eval:  2025-01-01 onwards

B. Trends + Direct + Other   ← preferred
   Same train/eval as A

C. Post–Sep 2025 GSC (optional diagnostic)
   Train/eval only on weeks from 2025-09-01 (new impression method)
   Uses GSC clicks + position (not pre-break impressions)
   Short sample — interpret cautiously

Training mask: Black Friday → Twelfth Night (5 Jan) every year.

Outputs: research/analysis/outputs/ecp_seo_counterfactual/
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

_ANALYSIS_ROOT = Path(__file__).resolve().parent
_RESEARCH_ROOT = _ANALYSIS_ROOT.parent
for _p in (_ANALYSIS_ROOT, _RESEARCH_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from paths import ANALYSIS_OUTPUTS, GA4_EXPORTS, RESEARCH_ROOT  # noqa: E402

COMBINED = GA4_EXPORTS / "combined" / "ga4_trends_weekly_euro_car_parts_241379560.csv"
GSC_DIR = RESEARCH_ROOT / "gsc"
OUT_DIR = ANALYSIS_OUTPUTS / "ecp_seo_counterfactual"

EVAL_START = "2025-01-01"
GSC_METHOD_CHANGE = "2025-09-01"  # impressions not comparable before this
TWELFTH_NIGHT_DAY = 5


def us_thanksgiving(year: int) -> date:
    nov1 = date(year, 11, 1)
    first_thu = nov1 + timedelta(days=(3 - nov1.weekday()) % 7)
    return first_thu + timedelta(days=21)


def black_friday(year: int) -> date:
    return us_thanksgiving(year) + timedelta(days=1)


def bf_to_twelfth_night_dates(year: int) -> set[date]:
    start = black_friday(year)
    end = date(year + 1, 1, TWELFTH_NIGHT_DAY)
    out: set[date] = set()
    d = start
    while d <= end:
        out.add(d)
        d += timedelta(days=1)
    return out


def festive_mask(weeks: pd.Series) -> pd.Series:
    weeks = pd.to_datetime(weeks)
    year_min = int(weeks.min().year) - 1
    year_max = int(weeks.max().year) + 1
    festive: set[date] = set()
    for y in range(year_min, year_max + 1):
        festive |= bf_to_twelfth_night_dates(y)
    flags = []
    for w in weeks:
        w_date = w.date()
        week_days = {w_date + timedelta(days=i) for i in range(7)}
        flags.append(bool(week_days & festive))
    return pd.Series(flags, index=weeks.index)


def safe_log1p(x: pd.Series) -> pd.Series:
    x = pd.to_numeric(x, errors="coerce").replace([np.inf, -np.inf], np.nan).clip(lower=0)
    return np.log1p(x)


def load_gsc_weekly(gsc_dir: Path) -> pd.DataFrame | None:
    path = gsc_dir / "all_chart.csv"
    if not path.is_file():
        return None
    raw = pd.read_csv(path, parse_dates=["Date"]).rename(
        columns={
            "Date": "date",
            "Clicks": "gsc_clicks",
            "Impressions": "gsc_impressions",
            "CTR": "gsc_ctr",
            "Position": "gsc_position",
        }
    )
    if raw["gsc_ctr"].dtype == object:
        raw["gsc_ctr"] = (
            raw["gsc_ctr"].astype(str).str.replace("%", "", regex=False).astype(float) / 100.0
        )
    raw["week"] = raw["date"] - pd.to_timedelta((raw["date"].dt.dayofweek + 1) % 7, unit="D")
    weekly = (
        raw.groupby("week", as_index=False)
        .agg(
            gsc_clicks=("gsc_clicks", "sum"),
            gsc_impressions=("gsc_impressions", "sum"),
            gsc_position=("gsc_position", "mean"),
            gsc_days=("date", "nunique"),
        )
        .sort_values("week")
    )
    weekly["gsc_ctr"] = weekly["gsc_clicks"] / weekly["gsc_impressions"].replace(0, np.nan)
    # Flag post method-change only — safe to use impressions here if ever needed
    weekly["gsc_post_method_change"] = weekly["week"] >= GSC_METHOD_CHANGE
    return weekly


def prepare(df: pd.DataFrame, gsc: pd.DataFrame | None, gsc_train_end: str) -> pd.DataFrame:
    out = df.copy()
    out["week"] = pd.to_datetime(out["week"])
    out = out.sort_values("week").reset_index(drop=True)
    if gsc is not None:
        out = out.merge(gsc, on="week", how="left")
    else:
        out["gsc_clicks"] = np.nan
        out["gsc_impressions"] = np.nan
        out["gsc_position"] = np.nan
        out["gsc_days"] = np.nan
        out["gsc_post_method_change"] = False

    out["mask_bf_twelfth"] = festive_mask(out["week"])
    origin = pd.Timestamp(EVAL_START) - pd.Timedelta(days=365)
    out["trend"] = (out["week"] - origin).dt.days / 7.0
    out["sin_annual"] = np.sin(2 * np.pi * out["week"].dt.dayofyear / 365.25)
    out["cos_annual"] = np.cos(2 * np.pi * out["week"].dt.dayofyear / 365.25)

    out["log_seo"] = safe_log1p(out["seo_sessions"])
    out["log_branded_trends"] = safe_log1p(out["branded_trends_mean"])
    out["log_nonbranded_trends"] = safe_log1p(out["nonbranded_trends_mean"])
    out["log_direct"] = safe_log1p(out["direct_sessions"])
    out["log_other"] = safe_log1p(out["other_sessions"])
    # Lag 1 week: site activity as predetermined demand proxy (limits same-week reverse causality)
    out["log_direct_l1"] = out["log_direct"].shift(1)
    out["log_other_l1"] = out["log_other"].shift(1)
    out["log_gsc_clicks"] = safe_log1p(out["gsc_clicks"])

    # A/B: long-history train / eval from 2025-01-01
    out["train_main"] = (out["week"] < EVAL_START) & (~out["mask_bf_twelfth"])
    out["eval_main"] = out["week"] >= EVAL_START

    # C: post–Sep 2025 GSC only (comparable measurement regime)
    train_end = pd.Timestamp(gsc_train_end)
    post = out["week"] >= GSC_METHOD_CHANGE
    has_gsc = out["gsc_clicks"].notna() & (out["gsc_days"].fillna(0) >= 4)
    out["train_gsc_post"] = (
        post & has_gsc & (out["week"] <= train_end) & (~out["mask_bf_twelfth"])
    )
    out["eval_gsc_post"] = post & has_gsc & (out["week"] > train_end)
    return out


def fit_cf(
    df: pd.DataFrame,
    *,
    train_col: str,
    eval_col: str,
    predictors: list[str],
    expected_col: str,
) -> tuple[object | None, pd.DataFrame, dict]:
    model_cols = ["log_seo", *predictors]
    train = df.loc[df[train_col]].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)
    evaluation = (
        df.loc[df[eval_col]].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)
    )
    usable = [
        c
        for c in predictors
        if train[c].nunique(dropna=True) >= 2 and float(train[c].std() or 0) > 1e-12
    ]
    if len(train) < 10 or evaluation.empty or not usable:
        return None, df, {
            "status": "skipped",
            "reason": f"train={len(train)} eval={len(evaluation)} usable={usable}",
        }

    formula = f"log_seo ~ {' + '.join(usable)}"
    model = smf.ols(formula, data=train).fit(cov_type="HC3")
    smear = float(np.mean(np.exp(np.asarray(model.resid, dtype=float))))

    timeline = df.copy()
    timeline[expected_col] = np.nan
    xb = np.asarray(model.predict(evaluation), dtype=float)
    expected = np.clip(np.exp(xb) * smear - 1.0, 0, None)
    timeline.loc[evaluation.index, expected_col] = expected
    timeline[f"{expected_col}_gap"] = timeline["seo_sessions"] - timeline[expected_col]
    timeline[f"{expected_col}_gap_pct"] = timeline[f"{expected_col}_gap"] / timeline[
        expected_col
    ].replace(0, np.nan)

    eval_out = timeline.loc[evaluation.index]
    actual = float(eval_out["seo_sessions"].sum())
    exp = float(eval_out[expected_col].sum())
    gap = actual - exp
    summary = {
        "status": "ok",
        "formula": formula,
        "smear": smear,
        "n_train": len(train),
        "n_eval": len(eval_out),
        "train_start": str(train["week"].min().date()),
        "train_end": str(train["week"].max().date()),
        "eval_start": str(eval_out["week"].min().date()),
        "eval_end": str(eval_out["week"].max().date()),
        "actual": actual,
        "expected": exp,
        "gap": gap,
        "gap_pct": gap / exp if exp else np.nan,
        "r2": float(model.rsquared),
        "params": {k: float(v) for k, v in model.params.items()},
        "pvalues": {k: float(v) for k, v in model.pvalues.items()},
    }
    return model, timeline, summary


def plot_specs(timeline: pd.DataFrame, summaries: dict[str, dict], out_path: Path) -> None:
    keys = [
        ("trends", "expected_seo_trends", "A. Trends only"),
        ("direct_other", "expected_seo_direct_other", "B. Trends + lag Direct/Other"),
        ("direct_other_recent", "expected_seo_direct_other_recent", "B2. Same, train from 2023"),
        ("gsc_post", "expected_seo_gsc_post", "C. Post–Sep 2025 GSC + lag Direct/Other"),
    ]
    n = sum(1 for k, _, _ in keys if summaries.get(k, {}).get("status") == "ok")
    n = max(n, 1)
    fig, axes = plt.subplots(n, 1, figsize=(12, 3.6 * n), sharex=True)
    if n == 1:
        axes = [axes]
    ai = 0
    for key, ecol, title in keys:
        s = summaries.get(key, {})
        if s.get("status") != "ok":
            continue
        ax = axes[ai]
        ai += 1
        ax.plot(timeline["week"], timeline["seo_sessions"], color="tab:blue", lw=1.4, label="Actual SEO")
        ge = timeline.dropna(subset=[ecol])
        ax.plot(ge["week"], ge[ecol], color="tab:red", ls="--", lw=1.4, label="Expected")
        ax.axvspan(pd.Timestamp(s["train_start"]), pd.Timestamp(s["train_end"]), color="tab:blue", alpha=0.06)
        ax.axvspan(pd.Timestamp(s["eval_start"]), pd.Timestamp(s["eval_end"]), color="tab:green", alpha=0.06)
        ax.axvline(pd.Timestamp(EVAL_START), color="black", ls="--", lw=0.8)
        ax.axvline(pd.Timestamp(GSC_METHOD_CHANGE), color="purple", ls=":", lw=1.0, label="GSC impr. method change")
        masked = timeline[timeline["mask_bf_twelfth"]]
        for _, row in masked.iterrows():
            ax.axvspan(row["week"], row["week"] + pd.Timedelta(days=6), color="grey", alpha=0.1, lw=0)
        ax.set_title(f"{title} — gap {s['gap_pct']*100:+.1f}%")
        ax.set_ylabel("SEO sessions")
        ax.legend(loc="upper left", fontsize=7)
    axes[-1].set_xlabel("Week")
    fig.autofmt_xdate()
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def fmt(n: float) -> str:
    if n is None or not np.isfinite(n):
        return "n/a"
    if abs(n) >= 100:
        return f"{n:,.0f}"
    return f"{n:.3f}"


def spec_block(name: str, title: str, summaries: dict) -> list[str]:
    s = summaries[name]
    if s.get("status") != "ok":
        return [f"### {title}", "", f"Skipped: {s.get('reason')}", ""]
    lines = [
        f"### {title}",
        "",
        f"- Train `{s['train_start']}` → `{s['train_end']}` (n={s['n_train']}, BF→Twelfth Night masked)",
        f"- Eval `{s['eval_start']}` → `{s['eval_end']}` (n={s['n_eval']})",
        f"- `{s['formula']}`",
        f"- R²={s['r2']:.3f}, smear={s['smear']:.4f}",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Actual SEO | {fmt(s['actual'])} |",
        f"| Expected SEO | {fmt(s['expected'])} |",
        f"| Gap | {fmt(s['gap'])} |",
        f"| Gap % | {s['gap_pct']*100:+.1f}% |",
        "",
        "| Term | Coef | p |",
        "| --- | ---: | ---: |",
    ]
    for k, v in s["params"].items():
        lines.append(f"| {k} | {v:+.4f} | {s['pvalues'].get(k, float('nan')):.3f} |")
    lines.append("")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="ECP SEO CF (Direct/Other + post-Sep GSC)")
    parser.add_argument("--combined", type=Path, default=COMBINED)
    parser.add_argument("--gsc-dir", type=Path, default=GSC_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    # For spec C only: train end within post-Sep regime (before festive if possible)
    parser.add_argument("--gsc-train-end", default="2026-02-15")
    args = parser.parse_args()

    if not args.combined.is_file():
        raise SystemExit(f"Missing {args.combined}")

    out_dir = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(args.combined)
    gsc = load_gsc_weekly(args.gsc_dir)
    df = prepare(raw, gsc, args.gsc_train_end)

    summaries: dict[str, dict] = {}
    timeline = df

    _, timeline, sum_a = fit_cf(
        timeline,
        train_col="train_main",
        eval_col="eval_main",
        predictors=[
            "log_branded_trends",
            "log_nonbranded_trends",
            "sin_annual",
            "cos_annual",
            "trend",
        ],
        expected_col="expected_seo_trends",
    )
    summaries["trends"] = sum_a

    _, timeline, sum_b = fit_cf(
        timeline,
        train_col="train_main",
        eval_col="eval_main",
        predictors=[
            "log_branded_trends",
            "log_nonbranded_trends",
            "log_direct_l1",
            "log_other_l1",
            "sin_annual",
            "cos_annual",
            "trend",
        ],
        expected_col="expected_seo_direct_other",
    )
    summaries["direct_other"] = sum_b

    # B2: same covariates, recent train only (avoids 2022 level regime)
    timeline["train_recent"] = (
        (timeline["week"] >= "2023-01-01")
        & (timeline["week"] < EVAL_START)
        & (~timeline["mask_bf_twelfth"])
    )
    _, timeline, sum_b2 = fit_cf(
        timeline,
        train_col="train_recent",
        eval_col="eval_main",
        predictors=[
            "log_branded_trends",
            "log_nonbranded_trends",
            "log_direct_l1",
            "log_other_l1",
            "sin_annual",
            "cos_annual",
            "trend",
        ],
        expected_col="expected_seo_direct_other_recent",
    )
    summaries["direct_other_recent"] = sum_b2

    _, timeline, sum_c = fit_cf(
        timeline,
        train_col="train_gsc_post",
        eval_col="eval_gsc_post",
        predictors=[
            "log_direct_l1",
            "log_other_l1",
            "log_gsc_clicks",
            "gsc_position",
            "sin_annual",
            "cos_annual",
            "trend",
        ],
        expected_col="expected_seo_gsc_post",
    )
    summaries["gsc_post"] = sum_c

    timeline.to_csv(out_dir / "timeline.csv", index=False)

    rows = []
    for name, s in summaries.items():
        rows.append({"spec": name, **{k: v for k, v in s.items() if not isinstance(v, dict)}})
        if s.get("params"):
            pd.DataFrame(
                [
                    {"spec": name, "term": k, "coef": v, "p": s["pvalues"].get(k, np.nan)}
                    for k, v in s["params"].items()
                ]
            ).to_csv(out_dir / f"coefficients_{name}.csv", index=False)
    pd.DataFrame(rows).to_csv(out_dir / "summary_by_spec.csv", index=False)

    plot_specs(timeline, summaries, out_dir / "ecp_seo_counterfactual.png")

    # Train-period correlations for Direct/Other
    tr = timeline.loc[timeline["train_main"]]
    corr_d = float(tr["seo_sessions"].corr(tr["direct_sessions"]))
    corr_o = float(tr["seo_sessions"].corr(tr["other_sessions"]))

    lines = [
        "# Euro Car Parts — SEO counterfactual",
        "",
        "## GSC impressions caveat",
        "",
        f"Google changed GSC **impression** measurement in **September 2025** "
        f"(`{GSC_METHOD_CHANGE}`). Pre- and post-break impressions are **not comparable**, "
        "so impressions are **not** used as covariates across that break.",
        "",
        "Instead, Spec B uses **lag-1 Direct** and **lag-1 Other** GA4 sessions as site-activity "
        "covariates (full panel; no GSC impression break). Spec C optionally adds post-break "
        "GSC **clicks + position** only (never pre-Sep impressions).",
        "",
        f"Train-period correlations (pre-`{EVAL_START}`, festive masked): "
        f"SEO↔Direct r={corr_d:+.3f}, SEO↔Other r={corr_o:+.3f}.",
        "",
        "**Note:** Direct/Other are lag-1 to limit same-week reverse causality. "
        "If AI shifts SEO→Direct with a lag, Spec B can still partially absorb the effect — "
        "read beside Trends-only (A).",
        "",
        "## Specs",
        "",
    ]
    lines += spec_block("trends", "A — Trends only", summaries)
    lines += spec_block(
        "direct_other",
        "B — Trends + lag-1 Direct + lag-1 Other (full train)",
        summaries,
    )
    lines += spec_block(
        "direct_other_recent",
        "B2 — Trends + lag-1 Direct + lag-1 Other (train from 2023-01-01)",
        summaries,
    )
    lines += spec_block(
        "gsc_post",
        f"C — Post–Sep 2025 only: lag Direct/Other + GSC clicks + position "
        f"(train → {args.gsc_train_end}; no pre-break impressions)",
        summaries,
    )
    lines += [
        "## Mask",
        "",
        "Black Friday → Twelfth Night (5 Jan) excluded from **training** every year.",
        "",
        f"Chart: `{out_dir / 'ecp_seo_counterfactual.png'}`",
        "",
    ]
    text = "\n".join(lines)
    (out_dir / "FINDINGS.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"Wrote outputs to {out_dir}")


if __name__ == "__main__":
    main()

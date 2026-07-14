#!/usr/bin/env python3
"""
Site-level counterfactuals + equal-weight overall (normalised) panels.

Outputs (anonymised labels only)::

    research/analysis/outputs/site_counterfactuals/

- Per-site models on raw levels (no C(site))
- Overall publishers / advertisers on site-baseline-normalised series + C(site)
  so large-volume sites do not dominate

Spend proxy = lagged PPC sessions (same as segment runner).

Anonymised map (publishers → Site 1–3, advertisers → Site 4–6):
  Site 1, Site 2, Site 3 | Site 4, Site 5, Site 6
Real property names are never written to output files.
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
for _p in (_ANALYSIS_ROOT, _RESEARCH_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from paths import ANALYSIS_OUTPUTS  # noqa: E402

from run_segment_counterfactuals import (  # noqa: E402
    BASELINE_END,
    BASELINE_START,
    DEFAULT_TRAIN_START,
    EVAL_START,
    MODELS,
    TRANSITION_END,
    TRANSITION_START,
    _holiday_mask,
    build_full_panel,
    safe_log1p,
)

OUT_DIR = ANALYSIS_OUTPUTS / "site_counterfactuals"
CHART_DIR = OUT_DIR / "charts"

# Fixed anonymisation — order matches segment site lists; never dump reverse map to disk.
_INTERNAL_SITES_PUBLISHERS = ("good_food", "radio_times", "what_car")
_INTERNAL_SITES_ADVERTISERS = ("wickes", "starbucks", "euro_car_parts")
SITE_LABEL: dict[str, str] = {
    _INTERNAL_SITES_PUBLISHERS[0]: "Site 1",
    _INTERNAL_SITES_PUBLISHERS[1]: "Site 2",
    _INTERNAL_SITES_PUBLISHERS[2]: "Site 3",
    _INTERNAL_SITES_ADVERTISERS[0]: "Site 4",
    _INTERNAL_SITES_ADVERTISERS[1]: "Site 5",
    _INTERNAL_SITES_ADVERTISERS[2]: "Site 6",
}
LABEL_TO_INTERNAL = {v: k for k, v in SITE_LABEL.items()}
PUBLISHER_LABELS = ("Site 1", "Site 2", "Site 3")
ADVERTISER_LABELS = ("Site 4", "Site 5", "Site 6")

NORM_COLS = (
    "SEO_sessions",
    "Direct_sessions",
    "purchases",
    "brand_PPC_sessions",
    "nonbrand_PPC_sessions",
    "total_PPC_sessions",
    "sessions",
    "brand_trends",
    "nonbrand_info_trends",
    "nonbrand_commercial_trends",
    "nonbrand_trends",
    "trends_volume",
    "brand_PPC_spend",
    "nonbrand_PPC_spend",
    "spend",
)


def anonymise_panel(panel: pd.DataFrame) -> pd.DataFrame:
    out = panel.copy()
    out["site_internal"] = out["site"]
    out["site"] = out["site_internal"].map(SITE_LABEL)
    return out.dropna(subset=["site"])


def _site_scale_means(panel: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Per-site scale: baseline mean, else pre-eval mean (skips all-zero series)."""
    base = panel[
        (panel["week"] >= pd.to_datetime(BASELINE_START))
        & (panel["week"] <= pd.to_datetime(BASELINE_END))
    ]
    pre_eval = panel[panel["period"] != "evaluation"]
    m_base = base.groupby("site")[cols].mean()
    m_pre = pre_eval.groupby("site")[cols].mean()
    # Prefer baseline; fall back to pre-eval when baseline mean is ~0 / missing
    means = m_base.reindex(panel["site"].dropna().unique()).copy()
    m_pre = m_pre.reindex(means.index)
    for col in cols:
        use_pre = means[col].isna() | (means[col].abs() < 1e-12)
        means.loc[use_pre, col] = m_pre.loc[use_pre, col]
        means.loc[means[col].abs() < 1e-12, col] = np.nan
    return means


def add_site_normalisation(panel: pd.DataFrame) -> pd.DataFrame:
    """Index metrics to each site's scale mean (baseline, else pre-eval)."""
    out = panel.copy()
    cols = [c for c in NORM_COLS if c in out.columns]
    site_means = _site_scale_means(out, cols)
    for col in cols:
        mean_col = f"_{col}_bmean"
        out = out.merge(
            site_means[[col]].rename(columns={col: mean_col}),
            left_on="site",
            right_index=True,
            how="left",
        )
        denom = out[mean_col].replace(0, np.nan)
        out[f"{col}_norm"] = out[col] / denom
        out.drop(columns=[mean_col], inplace=True)
    return out


def usable_predictors(df: pd.DataFrame, predictors: list[str]) -> list[str]:
    """Drop predictors that are all-missing or constant zero in the sample."""
    kept = []
    for p in predictors:
        if p not in df.columns:
            continue
        s = pd.to_numeric(df[p], errors="coerce")
        if s.notna().sum() < 10:
            continue
        if float(s.fillna(0).abs().sum()) <= 0:
            continue
        # variance on training-ish window
        if float(s.dropna().std() or 0) <= 0:
            continue
        kept.append(p)
    return kept


def run_cf(
    data: pd.DataFrame,
    *,
    outcome: str,
    predictors: list[str],
    include_site_fe: bool,
    include_trend: bool = True,
    mask_holidays: bool = True,
    min_train: int = 20,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, dict, object | None]:
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

    model_cols = [log_outcome, *log_preds, *controls]
    if include_site_fe:
        model_cols.append("site")

    train = (df["week"] >= DEFAULT_TRAIN_START) & (df["week"] <= BASELINE_END)
    train = train | (df["period"] == "transition")
    if mask_holidays:
        train = train & ~_holiday_mask(df)

    baseline = df.loc[train].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)
    eval_mask = df["period"] == "evaluation"
    if mask_holidays:
        eval_mask = eval_mask & ~_holiday_mask(df)
    evaluation = df.loc[eval_mask].replace([np.inf, -np.inf], np.nan).dropna(subset=model_cols)

    if len(baseline) < min_train:
        return None, None, {
            "status": "skipped",
            "reason": f"training rows={len(baseline)} (<{min_train})",
        }, None
    if evaluation.empty:
        return None, None, {"status": "skipped", "reason": "no evaluation rows"}, None
    if include_site_fe and baseline["site"].nunique() < 2:
        return None, None, {
            "status": "skipped",
            "reason": "need ≥2 sites for overall panel",
        }, None

    rhs = " + ".join(log_preds + controls)
    if include_site_fe:
        formula = f"{log_outcome} ~ {rhs} + C(site)"
    else:
        formula = f"{log_outcome} ~ {rhs}"

    try:
        if include_site_fe:
            model = smf.ols(formula, data=baseline).fit(
                cov_type="cluster", cov_kwds={"groups": baseline["site"]}
            )
        else:
            model = smf.ols(formula, data=baseline).fit(cov_type="HC3")
    except Exception as exc:  # noqa: BLE001
        return None, None, {"status": "skipped", "reason": f"fit failed: {exc}"}, None

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
    r2 = float(model.rsquared)
    if not np.isfinite(r2):
        r2 = np.nan
    gap_pct = gap / expected if expected and abs(expected) > 1e-9 else np.nan
    status = "ok"
    note = ""
    if not np.isfinite(gap_pct):
        status = "unreliable"
        note = "expected≈0 or undefined gap % (thin / zero series in baseline)"
    summary = {
        "status": status,
        "outcome": outcome,
        "predictors": ",".join(predictors),
        "formula": formula,
        "training_rows": len(baseline),
        "evaluation_rows": len(eval_out),
        "n_sites": int(baseline["site"].nunique()),
        "actual": actual,
        "expected": expected,
        "gap": gap,
        "gap_pct": gap_pct,
        "model_r2": r2,
        "notes": note,
    }
    for p in log_preds + controls:
        summary[f"{p}_coef"] = float(model.params.get(p, np.nan))
        summary[f"{p}_p"] = float(model.pvalues.get(p, np.nan))
    return eval_out, timeline, summary, model


def plot_timeline(
    timeline: pd.DataFrame,
    *,
    outcome: str,
    title: str,
    out_path: Path,
    group_label_col: str = "site",
) -> None:
    expected_col = f"expected_{outcome}"
    fig, ax = plt.subplots(figsize=(14, 5))
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

    for label, g in timeline.groupby(group_label_col):
        ax.plot(g["week"], g[outcome], alpha=0.35, linewidth=1.2, label=f"{label} actual")
        ge = g.dropna(subset=[expected_col])
        if not ge.empty:
            ax.plot(
                ge["week"],
                ge[expected_col],
                alpha=0.8,
                linewidth=1.4,
                linestyle="--",
                label=f"{label} expected",
            )

    if timeline[group_label_col].nunique() > 1:
        weekly_actual = timeline.groupby("week")[outcome].median()
        weekly_expected = (
            timeline.dropna(subset=[expected_col]).groupby("week")[expected_col].median()
        )
        ax.plot(
            weekly_actual.index,
            weekly_actual.values,
            color="tab:blue",
            linewidth=2,
            label="Median actual",
        )
        if not weekly_expected.empty:
            ax.plot(
                weekly_expected.index,
                weekly_expected.values,
                color="tab:red",
                linestyle="--",
                linewidth=2,
                label="Median expected",
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


def model_applies_to_kind(model_id: str, kind: str) -> bool:
    if model_id in ("direct_sessions", "purchases"):
        return kind == "advertiser"
    return True


def kind_for_label(label: str) -> str:
    return "publisher" if label in PUBLISHER_LABELS else "advertiser"


def main() -> None:
    parser = argparse.ArgumentParser(description="Site + overall normalised counterfactuals")
    parser.add_argument("--no-trend", action="store_true")
    parser.add_argument("--no-mask-holidays", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    out_dir = args.output_dir.resolve()
    chart_dir = out_dir / "charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    chart_dir.mkdir(parents=True, exist_ok=True)

    include_trend = not args.no_trend
    mask_holidays = not args.no_mask_holidays

    print("Building anonymised panel…")
    raw = anonymise_panel(build_full_panel())
    # Drop internal id before any export
    export_panel = raw.drop(columns=["site_internal"], errors="ignore")
    export_panel.to_csv(out_dir / "panel_site_week_anon.csv", index=False)
    norm_panel = add_site_normalisation(export_panel)

    summaries: list[dict] = []
    spend_note = (
        "spend proxy = lagged PPC sessions "
        "(brand/nonbrand/total prior-week PPC sessions)"
    )

    # ---- Per-site (raw levels) ----
    print("\n===== Per-site models (raw levels) =====")
    for model in MODELS:
        predictors = list(model.predictors)
        for label in (*PUBLISHER_LABELS, *ADVERTISER_LABELS):
            kind = kind_for_label(label)
            if not model_applies_to_kind(model.model_id, kind):
                continue
            site_df = export_panel[export_panel["site"] == label].copy()
            if site_df[model.outcome].fillna(0).sum() <= 0:
                summaries.append({
                    "scope": "site",
                    "model_id": model.model_id,
                    "model_label": model.label,
                    "site": label,
                    "group": kind,
                    "scale": "raw",
                    "status": "skipped",
                    "reason": "outcome all zero / missing",
                    "spend_note": spend_note if model.requires_spend else "",
                })
                print(f"  {model.model_id} | {label}: skipped (no outcome)")
                continue

            preds = usable_predictors(site_df, predictors)
            if not preds:
                summaries.append({
                    "scope": "site",
                    "model_id": model.model_id,
                    "model_label": model.label,
                    "site": label,
                    "group": kind,
                    "scale": "raw",
                    "status": "skipped",
                    "reason": "no usable predictors",
                    "spend_note": spend_note if model.requires_spend else "",
                })
                print(f"  {model.model_id} | {label}: skipped (no usable predictors)")
                continue

            eval_df, timeline, summary, _ = run_cf(
                site_df,
                outcome=model.outcome,
                predictors=preds,
                include_site_fe=False,
                include_trend=include_trend,
                mask_holidays=mask_holidays,
                min_train=20,
            )
            row = {
                "scope": "site",
                "model_id": model.model_id,
                "model_label": model.label,
                "site": label,
                "group": kind,
                "scale": "raw",
                "spend_note": spend_note if model.requires_spend else "",
                **summary,
            }
            summaries.append(row)
            if summary.get("status") not in ("ok", "unreliable"):
                print(f"  {model.model_id} | {label}: SKIP — {summary.get('reason')}")
                continue

            stem = f"{model.model_id}_{label.replace(' ', '_').lower()}"
            for frame, suffix in ((eval_df, "predictions"), (timeline, "timeline")):
                frame.drop(columns=["site_internal"], errors="ignore").to_csv(
                    out_dir / f"{stem}_{suffix}.csv", index=False
                )
            gap_txt = (
                f"{summary['gap_pct']*100:+.1f}%"
                if np.isfinite(summary.get("gap_pct", np.nan))
                else "n/a"
            )
            r2_txt = (
                f"{summary['model_r2']:.2f}"
                if np.isfinite(summary.get("model_r2", np.nan))
                else "n/a"
            )
            plot_timeline(
                timeline,
                outcome=model.outcome,
                title=(
                    f"{model.label} — {label}\n"
                    f"Gap {gap_txt} | R²={r2_txt} | "
                    f"site-specific | holidays masked"
                ),
                out_path=chart_dir / f"{stem}.png",
            )
            flag = " ⚠ thin" if summary.get("status") == "unreliable" else ""
            print(
                f"  {model.model_id} | {label}: gap={gap_txt}  R²={r2_txt}{flag}"
            )

    # ---- Overall groups on normalised levels ----
    print("\n===== Overall (normalised, equal-weight) =====")
    overall_groups = (
        ("publishers", PUBLISHER_LABELS, "publisher"),
        ("advertisers", ADVERTISER_LABELS, "advertiser"),
    )
    for group_slug, labels, kind in overall_groups:
        group_df = norm_panel[norm_panel["site"].isin(labels)].copy()
        for model in MODELS:
            if not model_applies_to_kind(model.model_id, kind):
                continue
            outcome_n = f"{model.outcome}_norm"
            if outcome_n not in group_df.columns:
                summaries.append({
                    "scope": "overall",
                    "model_id": model.model_id,
                    "model_label": model.label,
                    "site": f"Overall ({group_slug})",
                    "group": kind,
                    "scale": "normalised",
                    "status": "skipped",
                    "reason": f"missing outcome {outcome_n}",
                })
                continue
            # Keep sites with a finite scale for the outcome
            usable_sites = group_df.loc[group_df[outcome_n].notna(), "site"].unique()
            gdf = group_df[group_df["site"].isin(usable_sites)].copy()
            predictors_n = usable_predictors(
                gdf, [f"{p}_norm" for p in model.predictors]
            )
            if gdf[outcome_n].fillna(0).sum() <= 0 or len(usable_sites) < 2:
                summaries.append({
                    "scope": "overall",
                    "model_id": model.model_id,
                    "model_label": model.label,
                    "site": f"Overall ({group_slug})",
                    "group": kind,
                    "scale": "normalised",
                    "status": "skipped",
                    "reason": (
                        f"need ≥2 sites with scaled outcome "
                        f"(have {len(usable_sites)}: {sorted(map(str, usable_sites))})"
                    ),
                })
                print(
                    f"  {model.model_id} | overall {group_slug}: skipped "
                    f"({len(usable_sites)} scalable sites)"
                )
                continue
            if not predictors_n:
                summaries.append({
                    "scope": "overall",
                    "model_id": model.model_id,
                    "model_label": model.label,
                    "site": f"Overall ({group_slug})",
                    "group": kind,
                    "scale": "normalised",
                    "status": "skipped",
                    "reason": "no usable predictors after dropping zero/NaN series",
                })
                print(f"  {model.model_id} | overall {group_slug}: skipped (no predictors)")
                continue

            eval_df, timeline, summary, _ = run_cf(
                gdf,
                outcome=outcome_n,
                predictors=predictors_n,
                include_site_fe=True,
                include_trend=include_trend,
                mask_holidays=mask_holidays,
                min_train=30,
            )
            row = {
                "scope": "overall",
                "model_id": model.model_id,
                "model_label": model.label,
                "site": f"Overall ({group_slug})",
                "group": kind,
                "scale": "normalised",
                "spend_note": spend_note if model.requires_spend else "",
                **summary,
            }
            summaries.append(row)
            if summary.get("status") not in ("ok", "unreliable"):
                print(
                    f"  {model.model_id} | overall {group_slug}: "
                    f"SKIP — {summary.get('reason')}"
                )
                continue

            stem = f"{model.model_id}_overall_{group_slug}_norm"
            for frame, suffix in ((eval_df, "predictions"), (timeline, "timeline")):
                frame.drop(columns=["site_internal"], errors="ignore").to_csv(
                    out_dir / f"{stem}_{suffix}.csv", index=False
                )
            gap_txt = (
                f"{summary['gap_pct']*100:+.1f}%"
                if np.isfinite(summary.get("gap_pct", np.nan))
                else "n/a"
            )
            r2_txt = (
                f"{summary['model_r2']:.2f}"
                if np.isfinite(summary.get("model_r2", np.nan))
                else "n/a"
            )
            plot_timeline(
                timeline,
                outcome=outcome_n,
                title=(
                    f"{model.label} — Overall {group_slug} (normalised)\n"
                    f"Gap {gap_txt} | R²={r2_txt} | "
                    f"baseline-indexed + C(site)"
                ),
                out_path=chart_dir / f"{stem}.png",
            )
            print(
                f"  {model.model_id} | overall {group_slug}: "
                f"gap={gap_txt}  R²={r2_txt}"
            )

    summary_df = pd.DataFrame(summaries)
    summary_path = out_dir / "summary_by_site.csv"
    summary_df.to_csv(summary_path, index=False)

    # Compact findings (no real site names)
    findings = out_dir / "FINDINGS.md"
    lines = [
        "# Site-level counterfactual findings",
        "",
        f"Training: `{DEFAULT_TRAIN_START}` → `{BASELINE_END}` (+ transition). "
        f"Evaluation from `{EVAL_START}`. Christmas/Easter weeks masked.",
        "",
        "**Anonymisation:** Publishers = Site 1–3; Advertisers = Site 4–6.",
        "",
        "**Spend proxy:** lagged PPC sessions (prior week by site).",
        "",
        "**Overall:** metrics / site baseline mean "
        f"(`{BASELINE_START}` → `{BASELINE_END}`), then panel OLS with `C(site)` "
        "so each site contributes on a comparable scale.",
        "",
        "## Overall (normalised)",
        "",
        "| Model | Group | Gap % | R² |",
        "|-------|-------|------:|---:|",
    ]
    overall = summary_df[
        (summary_df["scope"] == "overall") & (summary_df["status"].isin(["ok", "unreliable"]))
    ]
    for _, r in overall.iterrows():
        gap = f"{r['gap_pct']*100:+.1f}%" if pd.notna(r.get("gap_pct")) else "n/a"
        r2 = f"{r['model_r2']:.2f}" if pd.notna(r.get("model_r2")) else "n/a"
        lines.append(f"| {r['model_label']} | {r['site']} | {gap} | {r2} |")

    lines += [
        "",
        "## By site (raw levels)",
        "",
        "| Model | Site | Gap % | R² |",
        "|-------|------|------:|---:|",
    ]
    by_site = summary_df[
        (summary_df["scope"] == "site")
        & (summary_df["status"].isin(["ok", "unreliable"]))
    ].sort_values(["model_id", "site"])
    for _, r in by_site.iterrows():
        gap = f"{r['gap_pct']*100:+.1f}%" if pd.notna(r.get("gap_pct")) else "n/a"
        r2 = f"{r['model_r2']:.2f}" if pd.notna(r.get("model_r2")) else "n/a"
        flag = " *" if r.get("status") == "unreliable" else ""
        lines.append(f"| {r['model_label']} | {r['site']} | {gap}{flag} | {r2} |")

    lines += [
        "",
        "## Notes",
        "",
        "- Gap % = (actual − expected) / expected on non-holiday evaluation weeks.",
        "- Site models: no `C(site)`; HC3 SEs. Overall: `C(site)` + clustered SEs.",
        "- Normalisation: metric / site mean over baseline; if that mean is ~0, "
        "use pre-evaluation mean so late-starting series can still enter overall.",
        "- Constant-zero predictors (e.g. commercial trends for publishers) are dropped.",
        "- `*` / unreliable = thin series (expected≈0 in eval); treat with caution.",
        "- Charts under `charts/`.",
        "",
    ]
    findings.write_text("\n".join(lines), encoding="utf-8")

    print(f"\nWrote {summary_path}")
    print(f"Wrote {findings}")
    print(f"Charts in {chart_dir}")
    # Silence unused import warning for LABEL_TO_INTERNAL (kept for local debugging only)
    _ = LABEL_TO_INTERNAL


if __name__ == "__main__":
    main()

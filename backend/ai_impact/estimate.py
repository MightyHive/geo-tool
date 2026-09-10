"""AI proliferation impact ranges + session-quality (CVR) narrative."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .panel import filter_completed_weeks
from .model_artifact import HierarchicalArtifact
from .scoring import CategoryPosteriorResult, score_category_posterior

GSC_IMPRESSION_BREAK = pd.Timestamp("2025-09-01")


@dataclass
class RangeEstimate:
    low: float
    central: float
    high: float

    def as_dict(self) -> dict[str, float]:
        return {"low": self.low, "central": self.central, "high": self.high}


@dataclass
class EstimateResult:
    window_start: str
    window_end: str
    direct_ai_sessions: float
    direct_ai_purchases: float
    total_sessions: float
    total_purchases: float
    seo_sessions: float
    seo_purchases: float
    sessions_overall_net: RangeEstimate
    sessions_overall_gross: RangeEstimate
    sessions_seo_net: RangeEstimate
    purchases_overall: RangeEstimate
    purchases_seo: RangeEstimate
    site_cvr: float
    ai_cvr: float
    seo_cvr: float
    model_quality_score: float
    quality_narrative: str
    p_value: float | None = None
    method_notes: list[str] = field(default_factory=list)
    correlations: dict[str, float] = field(default_factory=dict)
    weekly_series: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _cvr(purchases: float, sessions: float) -> float:
    if not sessions or not np.isfinite(sessions) or sessions <= 0:
        return float("nan")
    return float(purchases / sessions)


def _quality_narrative(*, site_cvr: float, ai_cvr: float, seo_net_central: float) -> str:
    """Human-readable quality signal for the UI."""
    parts: list[str] = []
    if np.isfinite(seo_net_central) and seo_net_central < 0:
        parts.append("seo_sessions_associated_down")
    elif np.isfinite(seo_net_central) and seo_net_central > 0:
        parts.append("seo_sessions_associated_up")
    else:
        parts.append("seo_sessions_flat")

    if np.isfinite(ai_cvr) and np.isfinite(site_cvr):
        if ai_cvr > site_cvr * 1.05:
            parts.append("ai_cvr_above_site")
        elif ai_cvr < site_cvr * 0.95:
            parts.append("ai_cvr_below_site")
        else:
            parts.append("ai_cvr_near_site")

    # Combined story for the dashboard headline
    if "seo_sessions_associated_down" in parts and "ai_cvr_above_site" in parts:
        return "sessions_down_quality_up"
    # Former sessions_up_quality_down case folds into mixed (no longer surfaced).
    if "seo_sessions_associated_down" in parts and "ai_cvr_below_site" in parts:
        return "sessions_and_quality_down"
    if "seo_sessions_associated_up" in parts and "ai_cvr_above_site" in parts:
        return "sessions_and_quality_up"
    return "mixed_or_aligned"


def _range_from_values(values: list[float]) -> RangeEstimate:
    arr = np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if arr.size == 0:
        return RangeEstimate(0.0, 0.0, 0.0)
    return RangeEstimate(float(np.min(arr)), float(np.median(arr)), float(np.max(arr)))


def _estimate_model_quality_score(
    *,
    history_weeks: int,
    window_weeks: int,
    baseline_weeks: int,
    betas: list[float],
    session_scenarios: list[float],
    purchase_scenarios: list[float],
) -> float:
    """0–100 data/model quality heuristic, not a probability statement."""
    history_points = 25.0 * min(max(history_weeks, 0) / 52.0, 1.0)
    window_points = 15.0 * min(max(window_weeks, 0) / 13.0, 1.0)
    baseline_points = 10.0 * min(max(baseline_weeks, 0) / 8.0, 1.0)
    valid_betas = sum(1 for beta in betas if np.isfinite(beta))
    beta_points = 25.0 * valid_betas / max(len(betas), 1)

    def agreement(values: list[float]) -> float:
        estimate = _range_from_values(values)
        denominator = max(abs(estimate.central), 1.0)
        relative_width = abs(estimate.high - estimate.low) / denominator
        return max(0.0, 1.0 - min(relative_width, 1.0))

    agreement_points = 25.0 * float(
        np.mean([agreement(session_scenarios), agreement(purchase_scenarios)])
    )
    return float(
        np.clip(
            round(
                history_points
                + window_points
                + baseline_points
                + beta_points
                + agreement_points
            ),
            0,
            100,
        )
    )


def _two_sided_normal_p(t_stat: float) -> float:
    """Two-sided p-value from a |t| using the normal survival function."""
    return float(min(max(math.erfc(abs(t_stat) / math.sqrt(2.0)), 0.0), 1.0))


def _detrend_beta_stats(
    y: np.ndarray,
    x: np.ndarray,
    controls: np.ndarray | None = None,
) -> tuple[float, float]:
    """
    OLS slope of ``y ~ x`` after time (+ optional demand) controls.

    Returns ``(beta, two_sided_p_value)``. Uses Frisch–Waugh–Lovell via a full
    design matrix so the p-value matches the reported slope.
    """
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    n = len(y)
    if n < 8:
        return float("nan"), float("nan")
    t = np.arange(n, dtype=float)
    design_columns = [np.ones(n), t]
    if controls is not None:
        controls = np.asarray(controls, dtype=float)
        if controls.ndim == 1:
            controls = controls.reshape(-1, 1)
        for column in controls.T:
            if np.isfinite(column).all() and float(np.std(column)) > 1e-12:
                design_columns.append(
                    (column - float(np.mean(column))) / float(np.std(column))
                )
    design_columns.append(x)
    X = np.column_stack(design_columns)
    if float(np.std(x)) < 1e-12:
        return float("nan"), float("nan")
    beta_hat, _residuals, rank, _s = np.linalg.lstsq(X, y, rcond=None)
    if int(rank) < X.shape[1]:
        return float("nan"), float("nan")
    beta = float(beta_hat[-1])
    resid = y - X @ beta_hat
    df = n - X.shape[1]
    if df < 1:
        return beta, float("nan")
    mse = float(np.sum(resid**2) / df)
    try:
        xtx_inv = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        xtx_inv = np.linalg.pinv(X.T @ X)
    se = math.sqrt(max(mse * float(xtx_inv[-1, -1]), 0.0))
    if se < 1e-15:
        return beta, float("nan")
    return beta, _two_sided_normal_p(beta / se)


def _detrend_beta(
    y: np.ndarray,
    x: np.ndarray,
    controls: np.ndarray | None = None,
) -> float:
    """OLS slope after removing time and optional demand controls."""
    beta, _p = _detrend_beta_stats(y, x, controls)
    return beta


def estimate_ai_impact(
    panel: pd.DataFrame,
    *,
    window_weeks: int = 13,
    end_week: str | None = None,
    drop_terminal_partial: bool = True,
    category: str | None = None,
    artifact: HierarchicalArtifact | None = None,
) -> EstimateResult | CategoryPosteriorResult:
    """
    Score the approved hierarchical baseline for new runs.

    ``category`` is mandatory for production calls. The no-category path is
    retained solely to deserialize/reproduce legacy runs created before the
    hierarchical artifact existed.
    """
    if category is not None:
        hierarchical_panel = panel
        if end_week:
            hierarchical_panel = panel[
                pd.to_datetime(panel["week"]) <= pd.Timestamp(end_week)
            ]
        return score_category_posterior(
            hierarchical_panel,
            category=category,
            window_weeks=window_weeks,
            artifact=artifact,
        )

    df = panel.copy()
    df["week"] = pd.to_datetime(df["week"])
    df = df.sort_values("week").reset_index(drop=True)

    if drop_terminal_partial and len(df) >= 2:
        # Drop last week if total sessions collapse vs prior median (partial export)
        med = float(df["total_sessions"].iloc[:-1].median()) if len(df) > 2 else float("nan")
        if np.isfinite(med) and med > 0 and float(df["total_sessions"].iloc[-1]) < 0.25 * med:
            df = df.iloc[:-1].copy()

    if end_week:
        df = df[df["week"] <= pd.Timestamp(end_week)]

    end = df["week"].max()
    start = end - pd.Timedelta(weeks=window_weeks - 1)
    win = df[(df["week"] >= start) & (df["week"] <= end)].copy()
    if win.empty:
        raise ValueError("No weeks in analysis window")

    pre = df[(df["week"] >= start - pd.Timedelta(weeks=8)) & (df["week"] < start)]
    base_ai = float(pre["ai_sessions"].mean()) if len(pre) else 0.0

    ai_s = float(win["ai_sessions"].sum())
    ai_p = float(win.get("ai_purchases", pd.Series(0, index=win.index)).fillna(0).sum())
    tot_s = float(win["total_sessions"].sum())
    tot_p = float(win["total_purchases"].sum())
    seo_s = float(win["seo_sessions"].sum())
    seo_p = float(win.get("seo_purchases", pd.Series(0, index=win.index)).fillna(0).sum())
    non_ai_s = float(win["non_ai_sessions"].sum())

    site_cvr = _cvr(tot_p, tot_s)
    ai_cvr = _cvr(ai_p, ai_s) if ai_s > 0 else float("nan")
    seo_cvr = _cvr(seo_p, seo_s)

    # Detrended betas on 2025+ through window end (excl festive)
    hist = df[(df["week"] >= "2025-01-01") & (df["week"] <= end)].copy()
    if "mask_bf_twelfth" in hist.columns:
        hist = hist[~hist["mask_bf_twelfth"].fillna(False)]
    hist = hist.reset_index(drop=True)
    ai_arr = hist["ai_sessions"].to_numpy(dtype=float)
    trend_columns = [column for column in hist.columns if column.startswith("trends_")]
    trend_controls = hist[trend_columns].to_numpy(dtype=float) if trend_columns else None
    b_seo = _detrend_beta(
        hist["seo_sessions"].to_numpy(dtype=float), ai_arr, trend_controls
    )
    b_non, p_non = _detrend_beta_stats(
        hist["non_ai_sessions"].to_numpy(dtype=float), ai_arr, trend_controls
    )
    b_purch = _detrend_beta(
        hist["total_purchases"].to_numpy(dtype=float), ai_arr, trend_controls
    )
    b_seo_p = _detrend_beta(
        hist.get("seo_purchases", pd.Series(0, index=hist.index)).fillna(0).to_numpy(dtype=float),
        ai_arr,
        trend_controls,
    )
    p_value = float(p_non) if np.isfinite(p_non) else None

    ai_uplift = float((win["ai_sessions"] - base_ai).sum())
    uplift_pos = max(ai_uplift, 0.0)

    cap_s = 0.15 * tot_s
    cap_seo = 0.15 * seo_s
    cap_p = 0.15 * tot_p
    cap_seo_p = 0.15 * max(seo_p, 1.0)

    def clip(x: float, cap: float) -> float:
        if not np.isfinite(x):
            return 0.0
        return float(np.clip(x, -cap, cap))

    # Overall net = direct AI + indirect on non-AI
    ind_non_up = clip(b_non * uplift_pos, cap_s) if np.isfinite(b_non) else 0.0
    ind_non_all = clip(b_non * ai_s, cap_s) if np.isfinite(b_non) else 0.0
    overall_nets = [
        ai_s,
        ai_s + 0.5 * ind_non_up,
        ai_s + ind_non_up,
        ai_s + 0.5 * ind_non_all,
        ai_s + ind_non_all,
    ]
    overall_gross = [ai_s + abs(v - ai_s) for v in overall_nets]

    # SEO-only (indirect only — AI is not in SEO channel)
    ind_seo_up = clip(b_seo * uplift_pos, cap_seo) if np.isfinite(b_seo) else 0.0
    ind_seo_all = clip(b_seo * ai_s, cap_seo) if np.isfinite(b_seo) else 0.0
    seo_nets = [0.0, 0.5 * ind_seo_up, ind_seo_up, 0.5 * ind_seo_all, ind_seo_all]

    purch_ind_up = clip(b_purch * uplift_pos, cap_p) if np.isfinite(b_purch) else 0.0
    purch_nets = [
        ai_p,
        ai_p + 0.5 * purch_ind_up,
        ai_p + purch_ind_up,
        ai_s * site_cvr if np.isfinite(site_cvr) else ai_p,
    ]
    seo_purch_ind = clip(b_seo_p * uplift_pos, cap_seo_p) if np.isfinite(b_seo_p) else 0.0
    seo_purch_nets = [0.0, 0.5 * seo_purch_ind, seo_purch_ind]
    model_quality_score = _estimate_model_quality_score(
        history_weeks=len(hist),
        window_weeks=len(win),
        baseline_weeks=len(pre),
        betas=[b_seo, b_non, b_purch, b_seo_p],
        session_scenarios=overall_nets,
        purchase_scenarios=purch_nets,
    )

    sessions_seo_net = _range_from_values(seo_nets)
    quality = _quality_narrative(
        site_cvr=site_cvr, ai_cvr=ai_cvr, seo_net_central=sessions_seo_net.central
    )

    corr_ai_seo = (
        float(win["ai_sessions"].corr(win["seo_sessions"])) if len(win) > 2 else float("nan")
    )
    corr_ai_tot = (
        float(win["ai_sessions"].corr(win["total_sessions"])) if len(win) > 2 else float("nan")
    )

    notes = [
        "Black Friday → Twelfth Night weeks flagged on panel (to handle retail spikes).",
        "Direct AI = measured AI-channel sessions/purchases in the window.",
        "Indirect central estimate from detrended AI↔channel association × AI volume/uplift.",
        "Displayed indirect sessions range is ±10% around the central estimate.",
        "Single-spec impacts capped at ±15% of window totals.",
        "GSC impressions not used across the Sep 2025 measurement break.",
        "Model quality is a 0–100 heuristic based on data history, baseline coverage, estimable relationships, and scenario agreement; it is not statistical confidence.",
        "Probability of result is (1 − p) × 100 from the AI→non-AI sessions association p-value.",
    ]
    if trend_columns:
        notes.append(
            f"Indirect associations control for {len(trend_columns)} manually uploaded Google Trends series."
        )

    sessions_overall_net = _range_from_values(overall_nets)
    indirect_central = float(sessions_overall_net.central - ai_s)
    weekly_series: list[dict[str, Any]] = []
    for _, row in win.iterrows():
        week = pd.Timestamp(row["week"]).date().isoformat()
        ai_w = float(row.get("ai_sessions") or 0.0)
        tot_w = float(row.get("total_sessions") or 0.0)
        seo_w = float(row.get("seo_sessions") or 0.0)
        non_ai_w = float(row.get("non_ai_sessions") or 0.0)
        share = (ai_w / ai_s) if ai_s > 0 else (1.0 / max(len(win), 1))
        ind_w = float(indirect_central * share)
        point: dict[str, Any] = {
            "week": week,
            "total_sessions": tot_w,
            "ai_sessions": ai_w,
            "seo_sessions": seo_w,
            "non_ai_sessions": non_ai_w,
            "indirect_ai_sessions": ind_w,
            "estimated_ai_sessions": ai_w + ind_w,
            "counterfactual_sessions": tot_w - ai_w - ind_w,
        }
        if "gsc_clicks" in win.columns:
            point["gsc_clicks"] = float(row.get("gsc_clicks") or 0.0)
        for column in trend_columns:
            value = row.get(column)
            try:
                point[column] = float(value) if value is not None and np.isfinite(float(value)) else None
            except (TypeError, ValueError):
                point[column] = None
        weekly_series.append(point)

    # Chart / export series: omit the in-progress Sunday week (partial days).
    weekly_series = filter_completed_weeks(weekly_series)

    return EstimateResult(
        window_start=str(win["week"].min().date()),
        window_end=str(win["week"].max().date()),
        direct_ai_sessions=ai_s,
        direct_ai_purchases=ai_p,
        total_sessions=tot_s,
        total_purchases=tot_p,
        seo_sessions=seo_s,
        seo_purchases=seo_p,
        sessions_overall_net=sessions_overall_net,
        sessions_overall_gross=_range_from_values(overall_gross),
        sessions_seo_net=sessions_seo_net,
        purchases_overall=_range_from_values(purch_nets),
        purchases_seo=_range_from_values(seo_purch_nets),
        site_cvr=site_cvr,
        ai_cvr=ai_cvr,
        seo_cvr=seo_cvr,
        model_quality_score=model_quality_score,
        quality_narrative=quality,
        p_value=p_value,
        method_notes=notes,
        correlations={"ai_vs_seo_sessions": corr_ai_seo, "ai_vs_total_sessions": corr_ai_tot},
        weekly_series=weekly_series,
    )

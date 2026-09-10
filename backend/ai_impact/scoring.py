"""Immediate category-posterior scoring for unseen sites."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from .model_artifact import (
    HierarchicalArtifact,
    load_artifact,
    transform_ai_share,
    validate_category,
)
from .panel import is_completed_sunday_week, is_model_christmas_week


@dataclass(frozen=True)
class PosteriorEstimate:
    posterior_mean: float
    lower_94: float
    upper_94: float


@dataclass(frozen=True)
class OutcomePosteriorEstimate:
    actual_sessions: float
    uncapped: PosteriorEstimate
    capped: PosteriorEstimate
    sensitivity_delta: float
    robust: bool


@dataclass
class CategoryPosteriorResult:
    window_start: str
    window_end: str
    category: str
    estimate_mode: str
    model_artifact_version: str
    signal_artifact_version: str
    refit_status: str
    history_eligible_weeks: int
    awaiting_signal_weeks: int
    direct_ai_sessions: float
    direct_ai_purchases: float
    total_sessions: float
    total_purchases: float
    site_cvr: float
    ai_cvr: float
    seo: OutcomePosteriorEstimate
    direct: OutcomePosteriorEstimate
    weekly_series: list[dict[str, Any]]
    method_notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["posterior_outcomes"] = {
            "seo": payload.pop("seo"),
            "direct": payload.pop("direct"),
        }
        return payload


def _summary(draws: np.ndarray) -> PosteriorEstimate:
    values = np.asarray(draws, dtype=float)
    return PosteriorEstimate(
        posterior_mean=float(values.mean()),
        lower_94=float(np.percentile(values, 3)),
        upper_94=float(np.percentile(values, 97)),
    )


def _robust(
    uncapped: PosteriorEstimate, capped: PosteriorEstimate
) -> bool:
    means_agree = np.sign(uncapped.posterior_mean) == np.sign(
        capped.posterior_mean
    )
    uncapped_direction = uncapped.lower_94 > 0 or uncapped.upper_94 < 0
    capped_direction = capped.lower_94 > 0 or capped.upper_94 < 0
    return bool(means_agree and uncapped_direction and capped_direction)


def _completed_panel(
    panel: pd.DataFrame,
    signal: pd.DataFrame,
    *,
    as_of: date | datetime | None,
) -> pd.DataFrame:
    frame = panel.copy()
    frame["week"] = pd.to_datetime(frame["week"]).dt.normalize()
    frame = frame[
        frame["week"].map(lambda value: is_completed_sunday_week(value, as_of=as_of))
    ]
    latest_signal = pd.Timestamp(signal["week_start"].max()).normalize()
    frame = frame[frame["week"] <= latest_signal]
    if frame.empty:
        raise ValueError("No completed site weeks overlap the portfolio signal")
    return frame.sort_values("week").drop_duplicates("week", keep="last")


def _site_adjusted_signal(
    panel: pd.DataFrame,
    artifact: HierarchicalArtifact,
) -> pd.DataFrame:
    portfolio = artifact.portfolio_signal().copy()
    portfolio["week_start"] = pd.to_datetime(portfolio["week_start"]).dt.normalize()
    selected = panel[
        ["week", "ai_sessions", "total_sessions"]
    ].rename(columns={"week": "week_start"})
    merged = portfolio[
        ["week_start", "ai_sessions_total", "all_sessions_total"]
    ].merge(selected, on="week_start", how="inner")
    if merged.empty:
        raise ValueError("Selected site has no weeks overlapping the portfolio signal")
    merged["ai_sessions_total_with_site"] = (
        merged["ai_sessions_total"] + merged["ai_sessions"].clip(lower=0)
    )
    merged["all_sessions_total_with_site"] = (
        merged["all_sessions_total"] + merged["total_sessions"].clip(lower=0)
    )
    denominator = merged["all_sessions_total_with_site"].replace(0, np.nan)
    merged["ai_share_with_site"] = (
        merged["ai_sessions_total_with_site"] / denominator
    )
    if merged["ai_share_with_site"].isna().any():
        raise ValueError("Portfolio plus site total sessions must be positive")
    transform = artifact.manifest["transform"]
    merged["ai_signal"] = transform_ai_share(
        merged["ai_share_with_site"], transform, capped=False
    )
    merged["ai_signal_capped"] = transform_ai_share(
        merged["ai_share_with_site"], transform, capped=True
    )
    return merged


def _score_outcome(
    *,
    actual: np.ndarray,
    signals: dict[str, np.ndarray],
    artifact: HierarchicalArtifact,
    outcome: str,
    category_index: int,
) -> tuple[OutcomePosteriorEstimate, dict[str, np.ndarray]]:
    by_spec: dict[str, np.ndarray] = {}
    weekly: dict[str, np.ndarray] = {}
    for specification in ("uncapped", "capped"):
        beta = artifact.posterior(outcome, specification)["beta_ai_cat"][
            :, category_index
        ]
        delta = signals[specification] - artifact.baseline(specification)
        counterfactual = actual[None, :] / np.exp(beta[:, None] * delta[None, :])
        influenced = actual[None, :] - counterfactual
        by_spec[specification] = influenced.sum(axis=1)
        weekly[f"{specification}_counterfactual"] = counterfactual
        weekly[f"{specification}_influenced"] = influenced
    uncapped = _summary(by_spec["uncapped"])
    capped = _summary(by_spec["capped"])
    return (
        OutcomePosteriorEstimate(
            actual_sessions=float(actual.sum()),
            uncapped=uncapped,
            capped=capped,
            sensitivity_delta=float(
                uncapped.posterior_mean - capped.posterior_mean
            ),
            robust=_robust(uncapped, capped),
        ),
        weekly,
    )


def score_category_posterior(
    panel: pd.DataFrame,
    *,
    category: str,
    window_weeks: int = 13,
    artifact: HierarchicalArtifact | None = None,
    as_of: date | datetime | None = None,
) -> CategoryPosteriorResult:
    """Apply category slope draws to the site's observed SEO and Direct traffic."""
    normalized_category = validate_category(category)
    model = artifact or load_artifact()
    category_index = model.category_index(normalized_category)
    required = {
        "week",
        "ai_sessions",
        "seo_sessions",
        "direct_sessions",
        "total_sessions",
        "total_purchases",
    }
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"AI-impact panel is missing columns: {sorted(missing)}")
    portfolio_signal = model.portfolio_signal()
    latest_signal = pd.Timestamp(portfolio_signal["week_start"].max()).normalize()
    complete_site_weeks = pd.to_datetime(panel["week"]).dt.normalize().map(
        lambda value: is_completed_sunday_week(value, as_of=as_of)
    )
    awaiting_signal_weeks = int(
        pd.to_datetime(panel.loc[complete_site_weeks, "week"])
        .dt.normalize()
        .gt(latest_signal)
        .sum()
    )
    frame = _completed_panel(panel, portfolio_signal, as_of=as_of)
    signal = _site_adjusted_signal(frame, model)
    frame = frame.merge(
        signal[["week_start", "ai_signal", "ai_signal_capped"]],
        left_on="week",
        right_on="week_start",
        how="inner",
    ).drop(columns="week_start")
    if len(frame) < window_weeks:
        raise ValueError(
            f"Need at least {window_weeks} completed overlapping weeks; got {len(frame)}"
        )
    window = frame.iloc[-window_weeks:].copy()
    signals = {
        "uncapped": window["ai_signal"].to_numpy(dtype=float),
        "capped": window["ai_signal_capped"].to_numpy(dtype=float),
    }
    seo, seo_weekly = _score_outcome(
        actual=window["seo_sessions"].to_numpy(dtype=float),
        signals=signals,
        artifact=model,
        outcome="seo",
        category_index=category_index,
    )
    direct, direct_weekly = _score_outcome(
        actual=window["direct_sessions"].to_numpy(dtype=float),
        signals=signals,
        artifact=model,
        outcome="direct",
        category_index=category_index,
    )

    weekly_series: list[dict[str, Any]] = []
    for row_index, (_, row) in enumerate(window.iterrows()):
        point: dict[str, Any] = {
            "week": pd.Timestamp(row["week"]).date().isoformat(),
            "seo_sessions": float(row["seo_sessions"]),
            "direct_sessions": float(row["direct_sessions"]),
            "total_sessions": float(row["total_sessions"]),
            "ai_sessions": float(row["ai_sessions"]),
            "ai_signal": float(row["ai_signal"]),
            "ai_signal_capped": float(row["ai_signal_capped"]),
        }
        for outcome, values in (("seo", seo_weekly), ("direct", direct_weekly)):
            for specification in ("uncapped", "capped"):
                for measure in ("counterfactual", "influenced"):
                    draws = values[f"{specification}_{measure}"][:, row_index]
                    summary = _summary(draws)
                    point[f"{outcome}_{specification}_{measure}"] = asdict(summary)
        weekly_series.append(point)

    valid_history = frame.loc[~is_model_christmas_week(frame["week"])]
    direct_ai_sessions = float(window["ai_sessions"].sum())
    direct_ai_purchases = float(
        window.get("ai_purchases", pd.Series(0.0, index=window.index))
        .fillna(0)
        .sum()
    )
    total_sessions = float(window["total_sessions"].sum())
    total_purchases = float(window["total_purchases"].sum())
    return CategoryPosteriorResult(
        window_start=window["week"].min().date().isoformat(),
        window_end=window["week"].max().date().isoformat(),
        category=normalized_category,
        estimate_mode="category_posterior",
        model_artifact_version=model.model_version,
        signal_artifact_version=model.signal_version,
        refit_status="eligible" if len(valid_history) >= 8 else "insufficient_history",
        history_eligible_weeks=int(len(valid_history)),
        awaiting_signal_weeks=awaiting_signal_weeks,
        direct_ai_sessions=direct_ai_sessions,
        direct_ai_purchases=direct_ai_purchases,
        total_sessions=total_sessions,
        total_purchases=total_purchases,
        site_cvr=(
            total_purchases / total_sessions if total_sessions > 0 else float("nan")
        ),
        ai_cvr=(
            direct_ai_purchases / direct_ai_sessions
            if direct_ai_sessions > 0
            else float("nan")
        ),
        seo=seo,
        direct=direct,
        weekly_series=weekly_series,
        method_notes=[
            "Category-level AI effect applied to this site's observed traffic.",
            "Uncertainty is the posterior 94% credible interval.",
            "Uncapped scaled-log is primary; capped P3/P97 is sensitivity.",
            "Measured AI sessions, purchases, and CVR are observed separately.",
            "No modeled purchase effect is produced.",
        ],
    )

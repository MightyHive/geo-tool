#!/usr/bin/env python3
"""
SEO sessions counterfactual from GA4 + manual Google Trends exports.

Model (panel)::

    log_SEO_sessions ~
        log_brand_trends
      + log_nonbrand_info_trends
      + log_nonbrand_commercial_trends
      + C(site)
      + sin_annual + cos_annual
      + christmas + easter
      + trend   # optional

Holiday seasonality (optional dummies):
  - **christmas** — week overlaps 24 Dec–1 Jan
  - **easter** — week overlaps Good Friday–Easter Monday
  - **sin_annual / cos_annual** — smooth annual cycle

By default, Christmas and Easter weeks are **masked** (excluded) from both
training and evaluation so the counterfactual is estimated on non-holiday weeks.

Trends mapping (from ``trends_manual/term_registry.json`` + folder hierarchy):
  - **brand_trends** — sum of that site's branded terms
  - **nonbrand_info_trends** — publishers: own non-branded terms;
    advertisers: market index (sum of all publisher non-branded terms)
  - **nonbrand_commercial_trends** — advertisers: own non-branded terms;
    publishers: 0

SEO sessions from ``ga4_channel_weekly_{site}_{id}.csv`` (``seo_sessions`` column).

Counterfactual predictions (``expected_SEO_sessions``) are computed **only for the
evaluation period** — the model is trained on pre-intervention weeks but not applied
in-sample to training data.

Default training uses a **recent window** (2024-09-01 → 2025-03-31) plus transition
weeks, without a linear time trend, so the counterfactual level aligns with actual
sessions immediately before evaluation. Use ``--long-baseline --include-trend`` to
reproduce the earlier specification.

Usage::

    # Euro Car Parts site-specific counterfactual
    python research/analysis/seo-sessions-counterfactual.py --site euro_car_parts

    # Panel across all registry sites
    python research/analysis/seo-sessions-counterfactual.py --all-sites
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

from paths import GA4_WEEKLY, SEO_COUNTERFACTUAL, TRENDS_ROOT  # noqa: E402

DEFAULT_OUT = SEO_COUNTERFACTUAL

# Period labels (for chart shading and counterfactual timing)
BASELINE_START = "2022-06-01"
BASELINE_END = "2025-03-31"
TRANSITION_START = "2025-04-01"
TRANSITION_END = "2025-05-25"
EVAL_START = "2025-05-26"

# Default training window: recent pre-intervention regime (avoids 2022 level drift)
DEFAULT_TRAIN_START = "2024-09-01"
DEFAULT_TRAIN_END = BASELINE_END


@dataclass(frozen=True)
class ModelConfig:
    train_start: str = DEFAULT_TRAIN_START
    train_end: str = DEFAULT_TRAIN_END
    include_transition_in_training: bool = True
    include_trend: bool = False
    include_holiday_seasonality: bool = False
    mask_holidays: bool = True
    eval_start: str = EVAL_START
    predict_periods: tuple[str, ...] = ("evaluation",)


CANONICAL_SITES = frozenset(
    {"good_food", "radio_times", "what_car", "wickes", "starbucks", "euro_car_parts"}
)


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous / Meeus algorithm)."""
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


def _week_date_set(week_start: date) -> set[date]:
    return {week_start + timedelta(days=i) for i in range(7)}


def christmas_dates_for_year(year: int) -> set[date]:
    """Christmas / New Year window: 24 Dec → 1 Jan (spans year boundary)."""
    dates = {
        date(year, 12, d) for d in range(24, 32) if d <= 31
    }
    # Dec 24–31 always valid; add Jan 1 of next year
    dates.add(date(year + 1, 1, 1))
    return dates


def easter_dates_for_year(year: int) -> set[date]:
    """Good Friday through Easter Monday."""
    easter = easter_sunday(year)
    return {easter + timedelta(days=offset) for offset in (-2, -1, 0, 1)}


def build_holiday_date_sets(
    year_min: int,
    year_max: int,
) -> tuple[set[date], set[date]]:
    christmas: set[date] = set()
    easter: set[date] = set()
    for year in range(year_min - 1, year_max + 2):
        christmas |= christmas_dates_for_year(year)
        easter |= easter_dates_for_year(year)
    return christmas, easter


def week_holiday_flags(weeks: pd.Series) -> pd.DataFrame:
    """Return christmas / easter indicators for Sunday-start week labels."""
    weeks = pd.to_datetime(weeks)
    year_min = int(weeks.min().year)
    year_max = int(weeks.max().year)
    christmas_dates, easter_dates = build_holiday_date_sets(year_min, year_max)

    christmas_flags: list[int] = []
    easter_flags: list[int] = []
    for w in weeks:
        days = _week_date_set(w.date())
        christmas_flags.append(int(bool(days & christmas_dates)))
        easter_flags.append(int(bool(days & easter_dates)))

    return pd.DataFrame(
        {"christmas": christmas_flags, "easter": easter_flags},
        index=weeks.index,
    )


def _term_slug(term: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", term.strip()).strip("_").lower()
    return slug or "term"


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


def _sum_terms_weekly(
    entity_id: str,
    kind_plural: str,
    bucket: str,
    terms: list[str],
) -> dict[str, float]:
    weekly_sum: dict[str, float] = {}
    for term in terms:
        slug = _term_slug(str(term))
        path = TRENDS_ROOT / kind_plural / entity_id / bucket / f"{slug}_20220601" / "trends_weekly.csv"
        for week, value in _read_trends_weekly(path).items():
            weekly_sum[week] = weekly_sum.get(week, 0.0) + value
    return weekly_sum


def _weekly_dict_to_rows(
    site: str,
    weekly: dict[str, float],
    column: str,
) -> list[dict]:
    return [{"site": site, "week": w, column: v} for w, v in sorted(weekly.items())]


def build_market_publisher_nonbrand_info() -> dict[str, float]:
    """Sum all publisher non-branded terms (informational market index)."""
    registry = load_registry()
    market: dict[str, float] = {}
    for entity_id, spec in (registry.get("entities") or {}).items():
        if str(spec.get("kind") or "publisher").strip().lower() != "publisher":
            continue
        terms = spec.get("non_branded") or []
        weekly = _sum_terms_weekly(entity_id, "publishers", "non_branded", terms)
        for week, value in weekly.items():
            market[week] = market.get(week, 0.0) + value
    return market


def build_trends_panel(sites: list[str] | None = None) -> pd.DataFrame:
    registry = load_registry()
    entities = registry.get("entities") or {}
    if sites:
        unknown = set(sites) - set(entities)
        if unknown:
            raise SystemExit(f"Unknown site(s): {sorted(unknown)}")
        entity_ids = sites
    else:
        entity_ids = [e for e in entities if e in CANONICAL_SITES]

    market_info = build_market_publisher_nonbrand_info()
    parts: list[pd.DataFrame] = []

    for entity_id in entity_ids:
        spec = entities[entity_id]
        kind = str(spec.get("kind") or "publisher").strip().lower()
        kind_plural = "advertisers" if kind == "advertiser" else "publishers"

        brand = _sum_terms_weekly(entity_id, kind_plural, "branded", spec.get("branded") or [])
        non_branded = _sum_terms_weekly(
            entity_id, kind_plural, "non_branded", spec.get("non_branded") or []
        )

        if kind == "publisher":
            info = non_branded
            commercial: dict[str, float] = {}
        else:
            info = market_info
            commercial = non_branded

        weeks = sorted(set(brand) | set(info) | set(commercial))
        rows = []
        for week in weeks:
            rows.append(
                {
                    "site": entity_id,
                    "week": week,
                    "site_type": kind,
                    "brand_trends": brand.get(week, 0.0),
                    "nonbrand_info_trends": info.get(week, 0.0),
                    "nonbrand_commercial_trends": commercial.get(week, 0.0),
                }
            )
        parts.append(pd.DataFrame(rows))

    out = pd.concat(parts, ignore_index=True)
    out["week"] = pd.to_datetime(out["week"])
    return out.sort_values(["site", "week"]).reset_index(drop=True)


def load_seo_sessions(sites: list[str] | None = None) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for path in sorted(GA4_WEEKLY.glob("ga4_channel_weekly_*_*.csv")):
        if path.name == "ga4_channel_weekly_combined.csv":
            continue
        df = pd.read_csv(path)
        if "property_name" not in df.columns or "seo_sessions" not in df.columns:
            continue
        site = str(df["property_name"].iloc[0])
        if site not in CANONICAL_SITES:
            continue
        if sites and site not in sites:
            continue
        rows.append(
            df[["property_name", "week", "seo_sessions"]].rename(
                columns={"property_name": "site", "seo_sessions": "SEO_sessions"}
            )
        )
    if not rows:
        raise SystemExit(f"No ga4_channel_weekly_*.csv in {GA4_WEEKLY} (run ga4/ga4_channel_weekly_export.py)")
    out = pd.concat(rows, ignore_index=True)
    out = out.drop_duplicates(subset=["site", "week"], keep="first")
    out["week"] = pd.to_datetime(out["week"])
    return out.sort_values(["site", "week"]).reset_index(drop=True)


def load_model_panel(sites: list[str] | None = None) -> pd.DataFrame:
    trends = build_trends_panel(sites)
    seo = load_seo_sessions(sites)
    panel = seo.merge(trends, on=["site", "week"], how="inner")
    panel = panel.sort_values(["site", "week"]).reset_index(drop=True)

    dup = panel.groupby(["site", "week"]).size()
    if (dup > 1).any():
        raise ValueError("Duplicate site-week rows after merge")

    return panel


def safe_log1p(x: pd.Series) -> pd.Series:
    x = pd.to_numeric(x, errors="coerce").replace([np.inf, -np.inf], np.nan).clip(lower=0)
    return np.log1p(x)


def add_model_columns(
    df: pd.DataFrame,
    *,
    train_start: str = DEFAULT_TRAIN_START,
) -> pd.DataFrame:
    out = df.copy()
    out["log_SEO_sessions"] = safe_log1p(out["SEO_sessions"])
    out["log_brand_trends"] = safe_log1p(out["brand_trends"])
    out["log_nonbrand_info_trends"] = safe_log1p(out["nonbrand_info_trends"])
    out["log_nonbrand_commercial_trends"] = safe_log1p(out["nonbrand_commercial_trends"])

    # Week index from training start (not dataset min) so trend does not extrapolate
    # a 2022→2024 structural decline into the counterfactual.
    origin = pd.to_datetime(train_start)
    out["trend"] = (out["week"] - origin).dt.days / 7
    out["sin_annual"] = np.sin(2 * np.pi * out["week"].dt.dayofyear / 365.25)
    out["cos_annual"] = np.cos(2 * np.pi * out["week"].dt.dayofyear / 365.25)

    holiday = week_holiday_flags(out["week"])
    out["christmas"] = holiday["christmas"].to_numpy()
    out["easter"] = holiday["easter"].to_numpy()

    out["period"] = "other"
    out.loc[
        (out["week"] >= pd.to_datetime(BASELINE_START))
        & (out["week"] <= pd.to_datetime(BASELINE_END)),
        "period",
    ] = "baseline"
    out.loc[
        (out["week"] >= pd.to_datetime(TRANSITION_START))
        & (out["week"] <= pd.to_datetime(TRANSITION_END)),
        "period",
    ] = "transition"
    out.loc[out["week"] >= pd.to_datetime(EVAL_START), "period"] = "evaluation"
    return out


TREND_PREDICTORS = [
    "log_brand_trends",
    "log_nonbrand_info_trends",
    "log_nonbrand_commercial_trends",
]

MODEL_CONTROLS = ["sin_annual", "cos_annual"]
HOLIDAY_CONTROLS = ["christmas", "easter"]


def _model_controls(config: ModelConfig) -> list[str]:
    controls = list(MODEL_CONTROLS)
    if config.include_holiday_seasonality:
        controls.extend(HOLIDAY_CONTROLS)
    if config.include_trend:
        controls.append("trend")
    return controls


def _model_cols(config: ModelConfig, *, include_site: bool = False) -> list[str]:
    cols = ["log_SEO_sessions", *TREND_PREDICTORS, *_model_controls(config)]
    if include_site:
        cols.append("site")
    return cols


def _build_formula(config: ModelConfig, *, include_site: bool = False) -> str:
    rhs = " + ".join([*TREND_PREDICTORS, *_model_controls(config)])
    if include_site:
        rhs = f"C(site) + {rhs}"
    return f"log_SEO_sessions ~ {rhs}"


def _training_mask(df: pd.DataFrame, config: ModelConfig) -> pd.Series:
    train = (df["week"] >= pd.to_datetime(config.train_start)) & (
        df["week"] <= pd.to_datetime(config.train_end)
    )
    if config.include_transition_in_training:
        train = train | (df["period"] == "transition")
    if config.mask_holidays:
        train = train & ~_holiday_mask(df)
    return train


def _holiday_mask(df: pd.DataFrame) -> pd.Series:
    """True for Christmas or Easter weeks."""
    christmas = df["christmas"].fillna(0).astype(int) == 1
    easter = df["easter"].fillna(0).astype(int) == 1
    return christmas | easter


def _evaluation_mask(df: pd.DataFrame, config: ModelConfig) -> pd.Series:
    mask = df["period"].isin(config.predict_periods)
    if config.mask_holidays:
        mask = mask & ~_holiday_mask(df)
    return mask


def _coef_params(config: ModelConfig) -> list[str]:
    params = list(TREND_PREDICTORS)
    if config.include_holiday_seasonality:
        params.extend(HOLIDAY_CONTROLS)
    if config.include_trend:
        params.append("trend")
    return params


def _coef_summary(model, config: ModelConfig) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in _coef_params(config):
        out[f"{p}_coef"] = float(model.params.get(p, np.nan))
        out[f"{p}_p"] = float(model.pvalues.get(p, np.nan))
    return out


def apply_counterfactual_predictions(
    model,
    df: pd.DataFrame,
    config: ModelConfig,
    *,
    include_site: bool = False,
) -> pd.DataFrame:
    """Add ``expected_SEO_sessions`` only for post-intervention periods (counterfactual).

    The model is fit on baseline data; predictions are applied only to rows whose
    ``period`` is in ``predict_periods`` (default: evaluation). Training-period
    rows keep ``expected_SEO_sessions`` as NaN — those are not counterfactuals.
    """
    cols = _model_cols(config, include_site=include_site)
    out = df.copy()
    out["expected_SEO_sessions"] = np.nan

    mask = _evaluation_mask(out, config)
    valid = out.loc[mask].replace([np.inf, -np.inf], np.nan).dropna(subset=cols)
    if not valid.empty:
        out.loc[valid.index, "expected_SEO_sessions"] = np.expm1(model.predict(valid)).clip(
            lower=0
        )

    out["SEO_sessions_gap"] = out["SEO_sessions"] - out["expected_SEO_sessions"]
    out["SEO_sessions_gap_pct"] = out["SEO_sessions_gap"] / out["expected_SEO_sessions"].replace(
        0, np.nan
    )
    return out


def run_site_counterfactual(
    data: pd.DataFrame,
    site: str,
    config: ModelConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, dict, object]:
    site_df = data[data["site"] == site].copy()
    cols = _model_cols(config, include_site=False)

    baseline = site_df[_training_mask(site_df, config)].copy()
    baseline = baseline.replace([np.inf, -np.inf], np.nan).dropna(subset=cols)

    evaluation = site_df[site_df["period"] == "evaluation"].copy()
    evaluation = evaluation.replace([np.inf, -np.inf], np.nan).dropna(subset=cols)

    if len(baseline) < 20:
        raise ValueError(f"{site}: need ≥20 training weeks, got {len(baseline)}")
    if evaluation.empty:
        raise ValueError(f"{site}: no evaluation weeks")

    formula = _build_formula(config, include_site=False)
    model = smf.ols(formula, data=baseline).fit(cov_type="HC3")

    timeline = apply_counterfactual_predictions(model, site_df, config, include_site=False)
    timeline["model_type"] = "site_specific"

    evaluation = timeline[
        (timeline["period"] == "evaluation") & timeline["expected_SEO_sessions"].notna()
    ].copy()
    if evaluation.empty:
        raise ValueError(f"{site}: no evaluation weeks after holiday mask")

    transition = site_df[site_df["period"] == "transition"].copy()
    if config.mask_holidays and not transition.empty:
        transition = transition[~_holiday_mask(transition)]
    trans_last = transition.iloc[-1] if not transition.empty else None
    trans_pred = np.nan
    if trans_last is not None:
        pred_row = transition.tail(1).replace([np.inf, -np.inf], np.nan).dropna(
            subset=[c for c in cols if c != "log_SEO_sessions"]
        )
        if not pred_row.empty:
            trans_pred = float(np.expm1(model.predict(pred_row).iloc[0]))

    holiday_weeks_excluded = int(_holiday_mask(site_df).sum()) if config.mask_holidays else 0

    summary = {
        "model_type": "site_specific",
        "site": site,
        "site_type": site_df["site_type"].iloc[0],
        "train_start": config.train_start,
        "train_end": config.train_end,
        "include_transition_in_training": config.include_transition_in_training,
        "include_trend": config.include_trend,
        "include_holiday_seasonality": config.include_holiday_seasonality,
        "mask_holidays": config.mask_holidays,
        "holiday_weeks_excluded_site": holiday_weeks_excluded,
        "training_weeks": len(baseline),
        "evaluation_weeks": len(evaluation),
        "transition_last_actual": float(trans_last["SEO_sessions"]) if trans_last is not None else np.nan,
        "transition_last_in_sample_pred": trans_pred,
        "actual_sessions": float(evaluation["SEO_sessions"].sum()),
        "expected_sessions": float(evaluation["expected_SEO_sessions"].sum()),
        "gap": float(evaluation["SEO_sessions_gap"].sum()),
        "gap_pct": float(
            evaluation["SEO_sessions_gap"].sum() / evaluation["expected_SEO_sessions"].sum()
        )
        if evaluation["expected_SEO_sessions"].sum()
        else np.nan,
        "model_r2": float(model.rsquared),
        **_coef_summary(model, config),
    }
    return evaluation, timeline, summary, model


def run_panel_counterfactual(
    data: pd.DataFrame,
    config: ModelConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, dict, object]:
    cols = _model_cols(config, include_site=True)

    baseline = data[_training_mask(data, config)].copy()
    baseline = baseline.replace([np.inf, -np.inf], np.nan).dropna(subset=cols)

    evaluation = data[data["period"] == "evaluation"].copy()
    evaluation = evaluation.replace([np.inf, -np.inf], np.nan).dropna(subset=cols)

    if len(baseline) < 30:
        raise ValueError(f"Panel training set too small: {len(baseline)} rows")
    if evaluation.empty:
        raise ValueError("No panel evaluation rows")

    formula = _build_formula(config, include_site=True)
    model = smf.ols(formula, data=baseline).fit(
        cov_type="cluster",
        cov_kwds={"groups": baseline["site"]},
    )

    timeline = apply_counterfactual_predictions(model, data, config, include_site=True)
    timeline["model_type"] = "panel"

    evaluation = timeline[
        (timeline["period"] == "evaluation") & timeline["expected_SEO_sessions"].notna()
    ].copy()
    if evaluation.empty:
        raise ValueError("No panel evaluation rows with complete model inputs")

    summary = {
        "model_type": "panel",
        "site": "all",
        "site_type": "mixed",
        "train_start": config.train_start,
        "train_end": config.train_end,
        "include_transition_in_training": config.include_transition_in_training,
        "include_trend": config.include_trend,
        "include_holiday_seasonality": config.include_holiday_seasonality,
        "mask_holidays": config.mask_holidays,
        "training_weeks": len(baseline),
        "evaluation_weeks": len(evaluation),
        "actual_sessions": float(evaluation["SEO_sessions"].sum()),
        "expected_sessions": float(evaluation["expected_SEO_sessions"].sum()),
        "gap": float(evaluation["SEO_sessions_gap"].sum()),
        "gap_pct": float(
            evaluation["SEO_sessions_gap"].sum() / evaluation["expected_SEO_sessions"].sum()
        )
        if evaluation["expected_SEO_sessions"].sum()
        else np.nan,
        "model_r2": float(model.rsquared),
        **_coef_summary(model, config),
    }
    return evaluation, timeline, summary, model


def plot_site_actual_vs_expected(
    timeline: pd.DataFrame,
    site: str,
    out_path: Path,
    config: ModelConfig,
) -> None:
    plot_df = timeline.sort_values("week").copy()
    if plot_df.empty:
        return

    fig, ax = plt.subplots(figsize=(14, 5))

    baseline_end = pd.to_datetime(BASELINE_END)
    eval_start = pd.to_datetime(config.eval_start)
    train_label = config.train_start
    if config.include_transition_in_training:
        train_label += f" → {TRANSITION_END} (+ transition)"

    ax.axvspan(
        plot_df["week"].min(),
        baseline_end,
        color="tab:blue",
        alpha=0.06,
        label="Training (baseline)",
    )
    ax.axvspan(
        pd.to_datetime(TRANSITION_START),
        pd.to_datetime(TRANSITION_END),
        color="tab:orange",
        alpha=0.08,
        label="Transition",
    )
    ax.axvspan(
        eval_start,
        plot_df["week"].max(),
        color="tab:green",
        alpha=0.06,
        label="Evaluation",
    )

    ax.plot(
        plot_df["week"],
        plot_df["SEO_sessions"],
        label="Actual SEO sessions",
        color="tab:blue",
        linewidth=1.5,
    )

    counterfactual = plot_df[plot_df["expected_SEO_sessions"].notna()].sort_values("week")
    if not counterfactual.empty:
        ax.plot(
            counterfactual["week"],
            counterfactual["expected_SEO_sessions"],
            label="Expected (counterfactual, post-intervention)",
            color="tab:red",
            linestyle="--",
            linewidth=1.5,
        )

    ax.axvline(baseline_end, color="grey", linestyle=":", linewidth=0.9)
    ax.axvline(eval_start, color="black", linestyle="--", linewidth=0.9)

    ax.set_title(
        f"{site}: actual vs counterfactual SEO sessions\n"
        f"Trained on {train_label}; counterfactual from {config.eval_start}"
    )
    ax.set_xlabel("Week")
    ax.set_ylabel("Sessions")
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="SEO sessions counterfactual from GA4 + Trends")
    parser.add_argument(
        "--site",
        default="euro_car_parts",
        help="Site id from term_registry (default: euro_car_parts)",
    )
    parser.add_argument(
        "--all-sites",
        action="store_true",
        help="Also run panel model across all canonical sites",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output directory (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip writing chart PNGs",
    )
    parser.add_argument(
        "--train-start",
        default=DEFAULT_TRAIN_START,
        help=f"First training week YYYY-MM-DD (default: {DEFAULT_TRAIN_START})",
    )
    parser.add_argument(
        "--train-end",
        default=DEFAULT_TRAIN_END,
        help=f"Last baseline training week YYYY-MM-DD (default: {DEFAULT_TRAIN_END})",
    )
    parser.add_argument(
        "--long-baseline",
        action="store_true",
        help=f"Train from {BASELINE_START} instead of recent window (not recommended for ECP)",
    )
    parser.add_argument(
        "--include-trend",
        action="store_true",
        help="Include linear week trend (can extrapolate old regime decline)",
    )
    parser.add_argument(
        "--include-holiday-seasonality",
        action="store_true",
        help="Include christmas / easter week dummies (off by default when holidays are masked)",
    )
    parser.add_argument(
        "--no-mask-holidays",
        action="store_true",
        help="Keep Christmas/Easter weeks in training and evaluation (default: mask them)",
    )
    parser.add_argument(
        "--no-transition-in-training",
        action="store_true",
        help="Exclude transition weeks from model training",
    )
    args = parser.parse_args()

    train_start = BASELINE_START if args.long_baseline else args.train_start
    mask_holidays = not args.no_mask_holidays
    config = ModelConfig(
        train_start=train_start,
        train_end=args.train_end,
        include_transition_in_training=not args.no_transition_in_training,
        include_trend=args.include_trend,
        include_holiday_seasonality=args.include_holiday_seasonality,
        mask_holidays=mask_holidays,
    )

    out_dir = args.output_dir.resolve()
    chart_dir = out_dir / "charts"
    out_dir.mkdir(parents=True, exist_ok=True)

    sites = list(CANONICAL_SITES) if args.all_sites else [args.site]
    panel = add_model_columns(
        load_model_panel(sites if args.all_sites else [args.site]),
        train_start=config.train_start,
    )
    panel.to_csv(out_dir / "model_panel_weekly.csv", index=False)

    print(f"Loaded {len(panel)} site-week rows for {panel['site'].nunique()} site(s)")
    print(panel.groupby("site").agg(weeks=("week", "count"), seo_total=("SEO_sessions", "sum")))
    print(
        f"\nModel config: train {config.train_start} → {config.train_end}, "
        f"transition_in_training={config.include_transition_in_training}, "
        f"include_trend={config.include_trend}, "
        f"mask_holidays={config.mask_holidays}, "
        f"holiday_dummies={config.include_holiday_seasonality}"
    )

    site_eval, site_timeline, site_summary, site_model = run_site_counterfactual(
        panel, args.site, config
    )
    site_eval.to_csv(out_dir / f"{args.site}_predictions.csv", index=False)
    site_timeline.to_csv(out_dir / f"{args.site}_timeline.csv", index=False)
    pd.DataFrame([site_summary]).to_csv(out_dir / f"{args.site}_summary.csv", index=False)

    print("\n==============================")
    print(f"SITE MODEL: {args.site}")
    print("==============================")
    print(site_model.summary())
    print("\nEvaluation counterfactual summary:")
    for k, v in site_summary.items():
        if k.endswith("_coef") or k.endswith("_p") or k in (
            "gap",
            "gap_pct",
            "model_r2",
            "transition_last_actual",
            "transition_last_in_sample_pred",
            "mask_holidays",
            "holiday_weeks_excluded_site",
            "training_weeks",
            "evaluation_weeks",
        ):
            print(f"  {k}: {v}")

    if not args.no_plots:
        chart_dir.mkdir(parents=True, exist_ok=True)
        plot_site_actual_vs_expected(
            site_timeline,
            args.site,
            chart_dir / f"{args.site}_actual_vs_expected.png",
            config,
        )

    if args.all_sites:
        full_panel = add_model_columns(load_model_panel(), train_start=config.train_start)
        full_panel.to_csv(out_dir / "model_panel_weekly_all_sites.csv", index=False)
        panel_eval, panel_timeline, panel_summary, panel_model = run_panel_counterfactual(
            full_panel, config
        )
        panel_eval.to_csv(out_dir / "panel_predictions.csv", index=False)
        panel_timeline.to_csv(out_dir / "panel_timeline.csv", index=False)
        pd.DataFrame([panel_summary]).to_csv(out_dir / "panel_summary.csv", index=False)

        print("\n==============================")
        print("PANEL MODEL (all sites)")
        print("==============================")
        print(panel_model.summary())
        print("\nPanel evaluation summary:")
        for k, v in panel_summary.items():
            if k.endswith("_coef") or k.endswith("_p") or k in ("gap", "gap_pct", "model_r2"):
                print(f"  {k}: {v}")

        if not args.no_plots:
            for site in panel_timeline["site"].unique():
                sub = panel_timeline[panel_timeline["site"] == site]
                plot_site_actual_vs_expected(
                    sub,
                    site,
                    chart_dir / f"{site}_actual_vs_expected.png",
                    config,
                )

    print(f"\nOutputs written to {out_dir}")


if __name__ == "__main__":
    main()

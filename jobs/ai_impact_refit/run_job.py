#!/usr/bin/env python3
"""Site-inclusive hierarchical AI-impact refit.

The API writes a run directory containing ``panel.csv`` and
``cold_start_estimate.json``. This job appends that site's eligible rows to the
promoted baseline panel, adds its traffic to the overlapping portfolio signal,
and refits the same SEO/Direct NumPyro model for uncapped and capped signals.

Required env:
  AI_IMPACT_REFIT_RUN_URI   Local directory or gs:// prefix for the API run.
  AI_IMPACT_REFIT_CATEGORY One of the promoted model categories.

Optional env:
  AI_IMPACT_REFIT_SITE_ID, AI_IMPACT_REFIT_ARTIFACT_DIR,
  AI_IMPACT_REFIT_WINDOW_WEEKS, AI_IMPACT_REFIT_WARMUP,
  AI_IMPACT_REFIT_SAMPLES, AI_IMPACT_REFIT_CHAINS.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
for _path in (_REPO, _REPO / "backend", _REPO / "research" / "modelling"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

OUTCOMES = {"seo": "log_seo", "direct": "log_direct"}
SPECIFICATIONS = {"uncapped": "ai_signal", "capped": "ai_signal_capped"}
MIN_ELIGIBLE_WEEKS = 8


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_value(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class RunStorage:
    """Small local/GCS adapter so the job contract is deployment-neutral."""

    def __init__(self, uri: str, work_dir: Path):
        self.uri = uri.rstrip("/")
        self.work_dir = work_dir
        self.is_gcs = self.uri.startswith("gs://")
        if self.is_gcs:
            _, _, remainder = self.uri.partition("gs://")
            self.bucket_name, _, self.prefix = remainder.partition("/")
        else:
            self.local_dir = Path(self.uri).expanduser().resolve()

    def download(self, name: str, *, required: bool = True) -> Path | None:
        destination = self.work_dir / name
        if self.is_gcs:
            from google.cloud import storage  # type: ignore

            blob_name = f"{self.prefix}/{name}" if self.prefix else name
            blob = storage.Client().bucket(self.bucket_name).blob(blob_name)
            if not blob.exists():
                if required:
                    raise FileNotFoundError(f"{self.uri}/{name}")
                return None
            blob.download_to_filename(str(destination))
        else:
            source = self.local_dir / name
            if not source.is_file():
                if required:
                    raise FileNotFoundError(str(source))
                return None
            shutil.copy2(source, destination)
        return destination

    def upload(self, path: Path, name: str | None = None) -> None:
        target_name = name or path.name
        if self.is_gcs:
            from google.cloud import storage  # type: ignore

            blob_name = f"{self.prefix}/{target_name}" if self.prefix else target_name
            storage.Client().bucket(self.bucket_name).blob(blob_name).upload_from_filename(
                str(path)
            )
            return
        self.local_dir.mkdir(parents=True, exist_ok=True)
        destination = self.local_dir / target_name
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copy2(path, temporary)
        temporary.replace(destination)


def _brand_trends_column(panel: pd.DataFrame) -> str:
    from ai_impact.panel import select_brand_trends_column

    configured = (os.getenv("AI_IMPACT_BRAND_TRENDS_COLUMN") or "").strip()
    return select_brand_trends_column(panel, configured or None)


def prepare_site_inputs(
    panel: pd.DataFrame,
    *,
    site_id: str,
    category: str,
    signal_weeks: pd.Series,
    as_of: datetime | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return model rows, Trends rows, and site portfolio contributions."""
    from ai_impact.model_artifact import validate_category
    from ai_impact.panel import is_completed_sunday_week, is_model_christmas_week

    validate_category(category)
    frame = panel.copy()
    frame["week"] = pd.to_datetime(frame["week"])
    frame = frame[
        frame["week"].map(lambda value: is_completed_sunday_week(value, as_of=as_of))
    ].copy()
    frame = frame.loc[~is_model_christmas_week(frame["week"])].copy()
    trends_column = _brand_trends_column(frame)
    numeric = ["seo_sessions", "direct_sessions", "ai_sessions", "total_sessions"]
    for column in numeric:
        if column not in frame.columns:
            raise ValueError(f"Site panel is missing {column}")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame[trends_column] = pd.to_numeric(frame[trends_column], errors="coerce")
    valid = (
        frame[numeric].notna().all(axis=1)
        & frame[trends_column].notna()
        & (frame["seo_sessions"] > 0)
        & (frame["direct_sessions"] > 0)
        & (frame["total_sessions"] > 0)
    )
    frame = frame.loc[valid].sort_values("week").drop_duplicates("week", keep="last")
    available_signal_weeks = set(pd.to_datetime(signal_weeks))
    frame = frame[frame["week"].isin(available_signal_weeks)].copy()
    if len(frame) < MIN_ELIGIBLE_WEEKS:
        raise ValueError(
            f"Site has {len(frame)} eligible completed non-Christmas weeks; "
            f"{MIN_ELIGIBLE_WEEKS} required"
        )

    week_lookup = {
        pd.Timestamp(value): index
        for index, value in enumerate(pd.to_datetime(signal_weeks).sort_values().unique())
    }
    model_panel = pd.DataFrame(
        {
            "site": site_id,
            "category": category,
            "week": frame["week"].map(week_lookup).astype(int),
            "date": frame["week"],
            "seo_sessions": frame["seo_sessions"].astype(float),
            "direct_sessions": frame["direct_sessions"].astype(float),
        }
    )
    trends = pd.DataFrame(
        {
            "site": site_id,
            "week_start": frame["week"],
            "brand_interest": frame[trends_column].astype(float),
        }
    )
    contribution = pd.DataFrame(
        {
            "week_start": frame["week"],
            "site_ai_sessions": frame["ai_sessions"].astype(float),
            "site_all_sessions": frame["total_sessions"].astype(float),
        }
    )
    return model_panel, trends, contribution


def recompute_portfolio_signal(
    baseline_signal: pd.DataFrame,
    contribution: pd.DataFrame,
    transform: dict[str, Any],
) -> pd.DataFrame:
    """Add the new site's weekly traffic and reapply the frozen transform."""
    from ai_impact.model_artifact import transform_ai_share

    signal = baseline_signal.copy()
    signal["week_start"] = pd.to_datetime(signal["week_start"])
    additions = contribution.copy()
    additions["week_start"] = pd.to_datetime(additions["week_start"])
    signal = signal.merge(additions, on="week_start", how="outer")
    for column in (
        "ai_sessions_total",
        "all_sessions_total",
        "site_ai_sessions",
        "site_all_sessions",
    ):
        signal[column] = pd.to_numeric(signal[column], errors="coerce").fillna(0.0)
    signal["ai_sessions_total"] += signal.pop("site_ai_sessions")
    signal["all_sessions_total"] += signal.pop("site_all_sessions")
    signal = signal.sort_values("week_start").reset_index(drop=True)
    signal["ai_share"] = signal["ai_sessions_total"] / signal[
        "all_sessions_total"
    ].replace(0, np.nan)
    if signal["ai_share"].isna().any():
        raise ValueError("Portfolio signal contains weeks with zero total sessions")
    signal["ai_signal"] = transform_ai_share(
        signal["ai_share"], transform, capped=False
    )
    signal["ai_signal_capped"] = transform_ai_share(
        signal["ai_share"], transform, capped=True
    )
    return signal


def _diagnostics(mcmc: Any, categories: list[str], outcome: str, spec: str) -> dict:
    import custom_ci

    summary = custom_ci.summarize_ai_parameters(mcmc, categories, outcome, spec)
    return {
        "divergences": int(np.asarray(mcmc.get_extra_fields()["diverging"]).sum()),
        "max_r_hat": float(summary["r_hat"].max()),
        "min_ess_bulk": float(summary["ess_bulk"].min()),
        "ai_parameters": summary.to_dict(orient="records"),
    }


def _site_influence(
    *,
    mcmc: Any,
    model_data: dict[str, Any],
    panel: pd.DataFrame,
    site_id: str,
    session_column: str,
    baseline: float,
    window_weeks: int,
) -> dict[str, Any]:
    import custom_ci

    site_rows = panel["site"].eq(site_id)
    site_dates = panel.loc[site_rows, "date"].sort_values().unique()
    evaluation_dates = set(site_dates[-window_weeks:])
    evaluation = (site_rows & panel["date"].isin(evaluation_dates)).to_numpy()
    result = custom_ci.compute_ai_influence(
        mcmc,
        model_data,
        panel,
        baseline,
        evaluation,
        session_column,
    )
    portfolio = result["portfolio"]
    influenced = np.asarray(result["influenced_draws"])[:, evaluation]
    actual = panel.loc[evaluation, session_column].to_numpy(dtype=float)
    dates = pd.to_datetime(panel.loc[evaluation, "date"]).dt.date.astype(str).to_numpy()
    weekly: dict[str, Any] = {}
    for index, week in enumerate(dates):
        influenced_summary = custom_ci._summarize_draws(influenced[:, index])
        counterfactual_summary = custom_ci._summarize_draws(
            actual[index] - influenced[:, index]
        )
        weekly[str(week)] = {
            "influenced": {
                "posterior_mean": influenced_summary["mean"],
                "lower_94": influenced_summary["low"],
                "upper_94": influenced_summary["high"],
            },
            "counterfactual": {
                "posterior_mean": counterfactual_summary["mean"],
                "lower_94": counterfactual_summary["low"],
                "upper_94": counterfactual_summary["high"],
            },
        }
    return {
        "posterior_mean": portfolio["mean"],
        "lower_94": portfolio["low"],
        "upper_94": portfolio["high"],
        "actual_sessions": portfolio["actual_sessions"],
        "_weekly": weekly,
    }


def run_refit(
    *,
    run_dir: Path,
    artifact_dir: Path,
    category: str,
    site_id: str,
    window_weeks: int,
    warmup: int,
    samples: int,
    chains: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    import custom_ci
    from ai_impact.model_artifact import load_artifact

    artifact = load_artifact(artifact_dir)
    if site_id in set(artifact.manifest.get("site_order") or ()):
        raise ValueError(f"Site id {site_id!r} already exists in baseline artifact")

    baseline_panel = pd.read_csv(
        artifact.path / "training_panel.csv.gz", parse_dates=["date"]
    )
    baseline_sites = pd.read_csv(artifact.path / "training_sites.csv")
    baseline_signal = artifact.portfolio_signal()
    site_panel = pd.read_csv(run_dir / "panel.csv", parse_dates=["week"])
    new_panel, new_trends, contribution = prepare_site_inputs(
        site_panel,
        site_id=site_id,
        category=category,
        signal_weeks=baseline_signal["week_start"],
    )
    signal = recompute_portfolio_signal(
        baseline_signal, contribution, artifact.manifest["transform"]
    )

    baseline_basic = baseline_panel[
        ["site", "category", "week", "date", "seo_sessions", "direct_sessions"]
    ].copy()
    baseline_trends = baseline_panel[["site", "date", "brand_interest"]].rename(
        columns={"date": "week_start"}
    )
    combined_basic = pd.concat([baseline_basic, new_panel], ignore_index=True)
    combined_trends = pd.concat([baseline_trends, new_trends], ignore_index=True)
    sites = pd.concat(
        [
            baseline_sites,
            pd.DataFrame(
                [
                    {
                        "site": site_id,
                        "website_type": "publisher"
                        if category == "publisher"
                        else "advertiser",
                        "category": category,
                        "n_weeks": len(new_panel),
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    categories = list(artifact.categories)
    prepared, sites, site_order, model_data, outcomes = custom_ci.prepare_model_data(
        combined_basic, sites, signal, combined_trends, categories
    )

    fits: dict[tuple[str, str], Any] = {}
    diagnostics: dict[str, Any] = {}
    estimates: dict[str, Any] = {}
    for outcome_index, (outcome, outcome_column) in enumerate(OUTCOMES.items()):
        estimates[outcome] = {}
        for spec_index, (spec, signal_column) in enumerate(SPECIFICATIONS.items()):
            spec_data = custom_ci.model_data_with_ai_signal(
                model_data, prepared, signal_column
            )
            mcmc = custom_ci.fit_outcome_model(
                spec_data,
                outcomes[outcome_column],
                num_warmup=warmup,
                num_samples=samples,
                num_chains=chains,
                seed=5000 + outcome_index * 10 + spec_index,
            )
            fits[(outcome, spec)] = mcmc
            diagnostics[f"{outcome}_{spec}"] = _diagnostics(
                mcmc, categories, outcome, spec
            )
            estimates[outcome][spec] = _site_influence(
                mcmc=mcmc,
                model_data=spec_data,
                panel=prepared,
                site_id=site_id,
                session_column=f"{outcome}_sessions",
                baseline=artifact.baseline(spec),
                window_weeks=window_weeks,
            )

    cold_path = run_dir / "cold_start_estimate.json"
    cold = json.loads(cold_path.read_text(encoding="utf-8")) if cold_path.is_file() else {}
    posterior_outcomes: dict[str, Any] = {}
    weekly_series = [
        dict(point) for point in (cold.get("weekly_series") or [])
        if isinstance(point, dict)
    ]
    for outcome in OUTCOMES:
        uncapped = dict(estimates[outcome]["uncapped"])
        capped = dict(estimates[outcome]["capped"])
        uncapped_weekly = uncapped.pop("_weekly")
        capped_weekly = capped.pop("_weekly")
        posterior_outcomes[outcome] = {
            "actual_sessions": uncapped.pop("actual_sessions"),
            "uncapped": uncapped,
            "capped": capped,
            "sensitivity_delta": (
                uncapped["posterior_mean"] - capped["posterior_mean"]
            ),
        }
        for point in weekly_series:
            week = str(point.get("week") or "")
            if week in uncapped_weekly:
                point[f"{outcome}_uncapped_influenced"] = uncapped_weekly[week][
                    "influenced"
                ]
                point[f"{outcome}_uncapped_counterfactual"] = uncapped_weekly[week][
                    "counterfactual"
                ]
            if week in capped_weekly:
                point[f"{outcome}_capped_influenced"] = capped_weekly[week][
                    "influenced"
                ]
                point[f"{outcome}_capped_counterfactual"] = capped_weekly[week][
                    "counterfactual"
                ]

    estimate = {
        **cold,
        "estimate_mode": "site_refit",
        "category": category,
        "site_id": site_id,
        "model_artifact_version": artifact.model_version,
        "signal_artifact_version": str(signal["week_start"].max().date()),
        "posterior_outcomes": posterior_outcomes,
        "weekly_series": weekly_series,
        "artifact_versions": {
            "baseline_model": artifact.model_version,
            "baseline_signal": artifact.signal_version,
            "refit_signal": str(signal["week_start"].max().date()),
        },
        "hierarchical_refit": {
            "status": "completed",
            "completed_at": _utc_now(),
            "eligible_weeks": int(
                prepared.loc[prepared["site"].eq(site_id), "date"].nunique()
            ),
            "window_weeks": window_weeks,
            "outcomes": estimates,
        },
        "awaiting_signal_weeks": 0,
    }
    diagnostic_payload = {
        "status": "completed",
        "completed_at": _utc_now(),
        "site_id": site_id,
        "category": category,
        "baseline_model_version": artifact.model_version,
        "site_order": site_order,
        "portfolio_signal_week_count": len(signal),
        "fits": diagnostics,
    }
    return estimate, diagnostic_payload


def main() -> None:
    uri = (os.getenv("AI_IMPACT_REFIT_RUN_URI") or os.getenv("GCS_OUTPUT_URI") or "").strip()
    if not uri:
        raise SystemExit("AI_IMPACT_REFIT_RUN_URI is required")
    category = (os.getenv("AI_IMPACT_REFIT_CATEGORY") or "").strip()
    site_id = (os.getenv("AI_IMPACT_REFIT_SITE_ID") or os.getenv("RUN_ID") or "").strip()
    if not category or not site_id:
        raise SystemExit("AI_IMPACT_REFIT_CATEGORY and AI_IMPACT_REFIT_SITE_ID are required")

    from ai_impact.model_artifact import current_artifact_dir

    artifact_dir = Path(
        os.getenv("AI_IMPACT_REFIT_ARTIFACT_DIR") or current_artifact_dir()
    ).expanduser()
    with tempfile.TemporaryDirectory(prefix="ai-impact-refit-") as temporary:
        work_dir = Path(temporary)
        storage = RunStorage(uri, work_dir)
        for name in ("panel.csv", "cold_start_estimate.json"):
            storage.download(name, required=name == "panel.csv")
        status_path = work_dir / "refit_status.json"
        try:
            estimate, diagnostics = run_refit(
                run_dir=work_dir,
                artifact_dir=artifact_dir,
                category=category,
                site_id=site_id,
                window_weeks=int(os.getenv("AI_IMPACT_REFIT_WINDOW_WEEKS") or "13"),
                warmup=int(os.getenv("AI_IMPACT_REFIT_WARMUP") or "1000"),
                samples=int(os.getenv("AI_IMPACT_REFIT_SAMPLES") or "1000"),
                chains=int(os.getenv("AI_IMPACT_REFIT_CHAINS") or "4"),
            )
            estimate_path = work_dir / "estimate.json"
            diagnostics_path = work_dir / "refit_diagnostics.json"
            estimate_path.write_text(
                json.dumps(_json_value(estimate), indent=2) + "\n", encoding="utf-8"
            )
            diagnostics_path.write_text(
                json.dumps(_json_value(diagnostics), indent=2) + "\n",
                encoding="utf-8",
            )
            status_path.write_text(
                json.dumps({"status": "completed", "updated_at": _utc_now()}, indent=2)
                + "\n",
                encoding="utf-8",
            )
            storage.upload(diagnostics_path)
            storage.upload(estimate_path)
            storage.upload(status_path)
        except Exception as exc:
            status_path.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "updated_at": _utc_now(),
                        "error": str(exc),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            storage.upload(status_path)
            raise


if __name__ == "__main__":
    main()

"""Train and publish the production hierarchical AI-impact baseline.

Run from ``research/modelling``. The output is a self-contained, versioned
bundle consumed by the NumPy-only production scorer; JAX/NumPyro are only
required in this offline training job.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import custom_ci


SCHEMA_VERSION = "ai-impact-hierarchical-v1"
DEFAULT_VERSION = "hierarchical-ai-v1"
OUTCOMES = {"seo": "log_seo", "direct": "log_direct"}
SPECIFICATIONS = {
    "uncapped": "ai_signal",
    "capped": "ai_signal_capped",
}
INPUT_DATA_FILES = (
    "portfolio_signal.csv.gz",
    "training_panel.csv.gz",
    "training_sites.csv",
)
FIT_SEEDS = {
    f"{outcome}_{spec}": 1000 + index
    for index, (outcome, spec) in enumerate(
        (outcome, spec) for outcome in OUTCOMES for spec in SPECIFICATIONS
    )
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bundle_hashes(output_dir: Path) -> dict[str, str]:
    """Hash every bundle payload; manifest is excluded to avoid recursion."""
    return {
        path.name: _sha256(path)
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }


def _data_digest(file_hashes: dict[str, str]) -> str:
    inputs = {
        name: file_hashes[name]
        for name in INPUT_DATA_FILES
        if name in file_hashes
    }
    payload = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _dependency_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for package in ("numpy", "pandas", "jax", "numpyro"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _promotion_thresholds(args: argparse.Namespace) -> dict[str, float | int]:
    return {
        "max_divergences": args.max_divergences,
        "max_r_hat": args.max_rhat,
        "min_ess_bulk": args.min_ess,
    }


def _passes_promotion(
    diagnostics: dict[str, dict],
    validation: list[dict],
    thresholds: dict[str, float | int],
) -> bool:
    diagnostics_ok = all(
        result["divergences"] <= thresholds["max_divergences"]
        and result["max_r_hat"] <= thresholds["max_r_hat"]
        and result["min_ess_bulk"] >= thresholds["min_ess_bulk"]
        for result in diagnostics.values()
    )
    validation_ok = all(
        row["direction_agrees"]
        and row["cold_divergences"] <= thresholds["max_divergences"]
        for row in validation
    )
    return bool(diagnostics_ok and validation_ok)


def _atomic_write_current(output_root: Path, version: str) -> None:
    temporary = output_root / f".CURRENT.{uuid.uuid4().hex}.tmp"
    temporary.write_text(version + "\n", encoding="utf-8")
    os.replace(temporary, output_root / "CURRENT")


def _new_staging_dir(output_root: Path, version: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    final = output_root / version
    if final.exists():
        raise FileExistsError(
            f"Artifact version {version!r} already exists at {final}; "
            "choose a new --version"
        )
    staging = output_root / f".{version}.staging-{uuid.uuid4().hex}"
    staging.mkdir()
    return staging


def _promote_candidate(staging: Path, final: Path) -> None:
    """Atomically expose a complete candidate without replacing a version."""
    if final.exists():
        raise FileExistsError(f"Artifact version already exists: {final}")
    staging.rename(final)


def _json_value(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def _save_posterior(path: Path, mcmc: Any) -> None:
    samples = {
        name: np.asarray(values)
        for name, values in mcmc.get_samples(group_by_chain=False).items()
    }
    np.savez_compressed(path, **samples)


def _diagnostics(mcmc: Any, categories: list[str], outcome: str, spec: str) -> dict:
    frame = custom_ci.summarize_ai_parameters(mcmc, categories, outcome, spec)
    divergences = int(np.asarray(mcmc.get_extra_fields()["diverging"]).sum())
    return {
        "divergences": divergences,
        "ai_parameters": frame.to_dict(orient="records"),
        "max_r_hat": float(frame["r_hat"].max()),
        "min_ess_bulk": float(frame["ess_bulk"].min()),
    }


def _influence_draws(
    beta_draws: np.ndarray,
    actual: np.ndarray,
    signal: np.ndarray,
    baseline: float,
) -> np.ndarray:
    delta = signal - baseline
    counterfactual = actual[None, :] / np.exp(beta_draws[:, None] * delta[None, :])
    return (actual[None, :] - counterfactual).sum(axis=1)


def _holdout_validation(
    *,
    panel: pd.DataFrame,
    sites: pd.DataFrame,
    categories: list[str],
    model_data: dict,
    outcomes: dict,
    production_posteriors: dict[tuple[str, str], dict[str, np.ndarray]],
    baseline_signal: float,
    warmup: int,
    samples: int,
    chains: int,
) -> list[dict]:
    """Refit once per category and outcome with a representative site held out.

    Validation compares immediate category-posterior influence with the promoted
    site-inclusive posterior. The held-out site is the median-history site in
    each category, avoiding cherry-picked shortest or longest histories.
    """
    rows: list[dict] = []
    cat_of_site = np.asarray(model_data["cat_of_site"])
    for category_index, category in enumerate(categories):
        candidates = sites[sites["category"] == category].sort_values("n_weeks")
        if candidates.empty:
            raise ValueError(f"No training site for required category {category!r}")
        held_site = candidates.iloc[len(candidates) // 2]["site"]
        held_rows = (panel["site"] == held_site).to_numpy()
        training_rows = ~held_rows
        held_category = int(cat_of_site[sites["site"].tolist().index(held_site)])
        assert held_category == category_index

        for outcome, outcome_column in OUTCOMES.items():
            y = np.asarray(outcomes[outcome_column])
            heldout_model = custom_ci.fit_outcome_model(
                custom_ci.subset_model_rows(model_data, training_rows),
                y[training_rows],
                num_warmup=warmup,
                num_samples=samples,
                num_chains=chains,
                seed=700 + category_index * 10 + list(OUTCOMES).index(outcome),
            )
            cold_beta = np.asarray(heldout_model.get_samples()["beta_ai_cat"])[
                :, category_index
            ]
            refit_beta = production_posteriors[(outcome, "uncapped")][
                "beta_ai_cat"
            ][:, category_index]
            actual = panel.loc[held_rows, f"{outcome}_sessions"].to_numpy(dtype=float)
            signal = panel.loc[held_rows, "ai_signal"].to_numpy(dtype=float)
            cold = _influence_draws(cold_beta, actual, signal, baseline_signal)
            refit = _influence_draws(refit_beta, actual, signal, baseline_signal)
            cold_interval = np.percentile(cold, [3, 97])
            refit_interval = np.percentile(refit, [3, 97])
            overlap = max(
                0.0,
                min(cold_interval[1], refit_interval[1])
                - max(cold_interval[0], refit_interval[0]),
            )
            union = max(cold_interval[1], refit_interval[1]) - min(
                cold_interval[0], refit_interval[0]
            )
            rows.append(
                {
                    "category": category,
                    "held_out_site": held_site,
                    "outcome": outcome,
                    "cold_mean": float(cold.mean()),
                    "cold_low": float(cold_interval[0]),
                    "cold_high": float(cold_interval[1]),
                    "site_refit_mean": float(refit.mean()),
                    "site_refit_low": float(refit_interval[0]),
                    "site_refit_high": float(refit_interval[1]),
                    "interval_overlap_ratio": float(overlap / union) if union > 0 else 1.0,
                    "direction_agrees": bool(np.sign(cold.mean()) == np.sign(refit.mean())),
                    "cold_divergences": int(
                        np.asarray(
                            heldout_model.get_extra_fields()["diverging"]
                        ).sum()
                    ),
                }
            )
    return rows


def repair_fit(args: argparse.Namespace) -> Path:
    """Explicitly refit one fit in an unpromoted candidate."""
    if not args.repair_fit:
        raise ValueError("--repair-fit is required")
    outcome, spec = args.repair_fit.split("_", 1)
    output_root = Path(args.output_root)
    output_dir = output_root / args.version
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Repair candidate has no manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_path = output_root / "CURRENT"
    current = (
        current_path.read_text(encoding="utf-8").strip()
        if current_path.is_file()
        else None
    )
    if manifest.get("promoted") or current == args.version:
        raise ValueError(
            "Repair is only allowed for an unpromoted candidate that is not CURRENT"
        )
    thresholds = manifest.get("promotion_thresholds")
    if not thresholds:
        raise ValueError(
            "Repair candidate lacks recorded promotion_thresholds; "
            "retrain under a new version"
        )

    panel_raw, sites_raw, categories, raw_signal = custom_ci.aggregate_site_data()
    signal, _transform = custom_ci.build_ai_adoption_index(raw_signal)
    trends = custom_ci.load_brand_trends(sites_raw)
    panel, _sites, _site_order, model_data, outcomes = custom_ci.prepare_model_data(
        panel_raw, sites_raw, signal, trends, categories
    )
    model_data = custom_ci.model_data_with_ai_signal(
        model_data, panel, SPECIFICATIONS[spec]
    )
    mcmc = custom_ci.fit_outcome_model(
        model_data,
        outcomes[OUTCOMES[outcome]],
        num_warmup=args.warmup,
        num_samples=args.samples,
        num_chains=args.chains,
        seed=9000,
    )
    posterior_path = output_dir / f"posterior_{outcome}_{spec}.npz"
    temporary_posterior = output_dir / f".{posterior_path.name}.{uuid.uuid4().hex}.tmp.npz"
    _save_posterior(temporary_posterior, mcmc)
    os.replace(temporary_posterior, posterior_path)
    manifest["diagnostics"][args.repair_fit] = _diagnostics(
        mcmc, categories, outcome, spec
    )

    posterior = {
        name: np.asarray(values) for name, values in mcmc.get_samples().items()
    }
    beta = posterior["beta_ai_cat"]
    baseline = float(manifest["baseline_signal"][spec])
    for row in manifest.get("validation") or []:
        if row["outcome"] != outcome:
            continue
        category_index = categories.index(row["category"])
        held = panel["site"].eq(row["held_out_site"])
        actual = panel.loc[held, f"{outcome}_sessions"].to_numpy(dtype=float)
        signal_values = panel.loc[held, SPECIFICATIONS[spec]].to_numpy(dtype=float)
        refit = _influence_draws(
            beta[:, category_index], actual, signal_values, baseline
        )
        interval = np.percentile(refit, [3, 97])
        row["site_refit_mean"] = float(refit.mean())
        row["site_refit_low"] = float(interval[0])
        row["site_refit_high"] = float(interval[1])
        row["direction_agrees"] = bool(
            np.sign(row["cold_mean"]) == np.sign(row["site_refit_mean"])
        )
        cold_interval = (row["cold_low"], row["cold_high"])
        overlap = max(
            0.0,
            min(cold_interval[1], interval[1])
            - max(cold_interval[0], interval[0]),
        )
        union = max(cold_interval[1], interval[1]) - min(
            cold_interval[0], interval[0]
        )
        row["interval_overlap_ratio"] = float(overlap / union) if union > 0 else 1.0

    validation_path = output_dir / "holdout_validation.csv"
    temporary_validation = output_dir / (
        f".{validation_path.name}.{uuid.uuid4().hex}.tmp"
    )
    pd.DataFrame(manifest["validation"]).to_csv(temporary_validation, index=False)
    os.replace(temporary_validation, validation_path)
    manifest["promoted"] = _passes_promotion(
        manifest["diagnostics"], manifest["validation"], thresholds
    )
    manifest["repair_fit"] = {
        "fit": args.repair_fit,
        "repaired_at": datetime.now(timezone.utc),
        "warmup": args.warmup,
        "samples": args.samples,
        "chains": args.chains,
        "seed": 9000,
    }
    manifest["bundle_sha256"] = _bundle_hashes(output_dir)
    manifest["input_data_sha256"] = _data_digest(manifest["bundle_sha256"])
    temporary_manifest = output_dir / f".manifest.{uuid.uuid4().hex}.tmp"
    temporary_manifest.write_text(
        json.dumps(_json_value(manifest), indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary_manifest, manifest_path)
    if not manifest["promoted"]:
        raise RuntimeError(
            f"Artifact {args.version} still fails promotion checks; inspect {output_dir}"
        )
    _atomic_write_current(output_root, args.version)
    print(f"Promoted repaired artifact: {output_dir}")
    return output_dir


def train(args: argparse.Namespace) -> Path:
    output_root = Path(args.output_root)
    final_dir = output_root / args.version
    output_dir = _new_staging_dir(output_root, args.version)

    panel, sites, categories, raw_signal = custom_ci.aggregate_site_data()
    expected_categories = [
        "advertiser-retail",
        "advertiser-services",
        "publisher",
    ]
    if sorted(categories) != sorted(expected_categories):
        raise ValueError(
            f"Training taxonomy must be exactly {expected_categories}; got {categories}"
        )
    signal, transform = custom_ci.build_ai_adoption_index(raw_signal)
    ramp_start = custom_ci.detect_ai_ramp_start(signal)
    baselines = {
        name: custom_ci.ai_baseline_value(signal, ramp_start, column)
        for name, column in SPECIFICATIONS.items()
    }
    trends = custom_ci.load_brand_trends(sites)
    panel, sites, site_order, model_data, outcomes = custom_ci.prepare_model_data(
        panel, sites, signal, trends, categories
    )

    signal.to_csv(output_dir / "portfolio_signal.csv.gz", index=False)
    panel.to_csv(output_dir / "training_panel.csv.gz", index=False)
    sites.to_csv(output_dir / "training_sites.csv", index=False)

    diagnostics: dict[str, dict] = {}
    posterior_cache: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    for outcome, outcome_column in OUTCOMES.items():
        for spec, signal_column in SPECIFICATIONS.items():
            print(f"\nTraining {outcome}/{spec} baseline...")
            spec_data = custom_ci.model_data_with_ai_signal(
                model_data, panel, signal_column
            )
            mcmc = custom_ci.fit_outcome_model(
                spec_data,
                outcomes[outcome_column],
                num_warmup=args.warmup,
                num_samples=args.samples,
                num_chains=args.chains,
                seed=FIT_SEEDS[f"{outcome}_{spec}"],
            )
            posterior_path = output_dir / f"posterior_{outcome}_{spec}.npz"
            _save_posterior(posterior_path, mcmc)
            posterior_cache[(outcome, spec)] = {
                name: np.asarray(values)
                for name, values in mcmc.get_samples().items()
            }
            diagnostics[f"{outcome}_{spec}"] = _diagnostics(
                mcmc, categories, outcome, spec
            )

    validation = _holdout_validation(
        panel=panel,
        sites=sites,
        categories=categories,
        model_data=model_data,
        outcomes=outcomes,
        production_posteriors=posterior_cache,
        baseline_signal=baselines["uncapped"],
        warmup=args.validation_warmup,
        samples=args.validation_samples,
        chains=args.validation_chains,
    )
    pd.DataFrame(validation).to_csv(output_dir / "holdout_validation.csv", index=False)

    thresholds = _promotion_thresholds(args)
    promoted = _passes_promotion(diagnostics, validation, thresholds)
    file_hashes = _bundle_hashes(output_dir)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "model_version": args.version,
        "created_at": datetime.now(timezone.utc),
        "promoted": promoted,
        "categories": categories,
        "site_order": site_order,
        "site_categories": dict(zip(sites["site"], sites["category"])),
        "outcomes": list(OUTCOMES),
        "specifications": SPECIFICATIONS,
        "posterior_files": {
            f"{outcome}_{spec}": f"posterior_{outcome}_{spec}.npz"
            for outcome in OUTCOMES
            for spec in SPECIFICATIONS
        },
        "transform": transform,
        "ramp_start": ramp_start,
        "baseline_signal": baselines,
        "fit_start": panel["date"].min(),
        "fit_end": panel["date"].max(),
        "latest_completed_portfolio_week": signal["week_start"].max(),
        "source": {
            "latest_completed_week": signal["week_start"].max(),
            "week_count": int(panel["date"].nunique()),
            "site_count": len(site_order),
        },
        "fourier_epoch": "absolute portfolio week; annual periods 52 and 26",
        "christmas_mask": {"start_md": 1215, "end_md": 107},
        "training_panel_version": f"{len(site_order)}-site-ga4-brand-trends-v2",
        "diagnostics": diagnostics,
        "validation": validation,
        "promotion_thresholds": thresholds,
        "seeds": {
            "production_fits": FIT_SEEDS,
            "holdout_fits": {
                f"{category}_{outcome}": 700
                + category_index * 10
                + outcome_index
                for category_index, category in enumerate(categories)
                for outcome_index, outcome in enumerate(OUTCOMES)
            },
        },
        "git_commit": _git_commit(),
        "dependency_versions": _dependency_versions(),
        "bundle_sha256": file_hashes,
        "input_data_sha256": _data_digest(file_hashes),
        "training": {
            "warmup": args.warmup,
            "samples": args.samples,
            "chains": args.chains,
        },
    }
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(_json_value(manifest), handle, indent=2, sort_keys=True)
    _promote_candidate(output_dir, final_dir)
    if not manifest["promoted"]:
        raise RuntimeError(
            f"Artifact {args.version} failed promotion checks; inspect {final_dir}"
        )
    _atomic_write_current(output_root, args.version)
    print(f"\nPromoted artifact: {final_dir}")
    return final_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--version",
        default=os.environ.get(
            "AI_IMPACT_MODEL_VERSION",
            os.environ.get("AI_MODEL_VERSION", DEFAULT_VERSION),
        ),
        help=(
            "Immutable artifact version (defaults to AI_IMPACT_MODEL_VERSION; "
            "legacy AI_MODEL_VERSION is also accepted)"
        ),
    )
    parser.add_argument(
        "--output-root",
        default="../../backend/ai_impact/model_artifacts",
    )
    parser.add_argument("--warmup", type=int, default=1000)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--chains", type=int, default=4)
    parser.add_argument("--validation-warmup", type=int, default=400)
    parser.add_argument("--validation-samples", type=int, default=400)
    parser.add_argument("--validation-chains", type=int, default=2)
    parser.add_argument("--max-divergences", type=int, default=2)
    parser.add_argument("--max-rhat", type=float, default=1.02)
    parser.add_argument("--min-ess", type=float, default=200)
    parser.add_argument(
        "--repair-fit",
        choices=[
            f"{outcome}_{spec}"
            for outcome in OUTCOMES
            for spec in SPECIFICATIONS
        ],
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    repair_fit(arguments) if arguments.repair_fit else train(arguments)

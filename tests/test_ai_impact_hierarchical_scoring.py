import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.ai_impact.model_artifact import (
    ArtifactCompatibilityError,
    load_artifact,
    transform_ai_share,
    validate_category,
)
from backend.ai_impact.panel import (
    is_model_christmas_week,
    select_brand_trends_column,
)
from backend.ai_impact.scoring import score_category_posterior


CATEGORIES = ["advertiser-retail", "advertiser-services", "publisher"]


def _artifact(tmp_path: Path) -> Path:
    artifact = tmp_path / "model-v1"
    artifact.mkdir()
    weeks = pd.date_range("2026-01-11", periods=16, freq="W-SUN")
    signal = pd.DataFrame(
        {
            "week_start": weeks,
            "ai_sessions_total": np.linspace(100, 400, len(weeks)),
            "all_sessions_total": np.full(len(weeks), 100_000.0),
        }
    )
    signal["ai_share"] = signal["ai_sessions_total"] / signal["all_sessions_total"]
    transform = {
        "share_scale": 0.001,
        "transform_mean": 0.0,
        "transform_std": 1.0,
        "cap_low": 0.0,
        "cap_high": 0.25,
        "reference_end": "2026-06-01T00:00:00",
    }
    signal["ai_signal"] = transform_ai_share(
        signal["ai_share"], transform, capped=False
    )
    signal["ai_signal_capped"] = transform_ai_share(
        signal["ai_share"], transform, capped=True
    )
    signal.to_csv(artifact / "portfolio_signal.csv.gz", index=False)

    posterior_files = {}
    for outcome in ("seo", "direct"):
        for specification in ("uncapped", "capped"):
            filename = f"posterior_{outcome}_{specification}.npz"
            beta = np.tile(np.array([[0.10, -0.05, 0.02]]), (200, 1))
            if specification == "capped":
                beta *= 0.8
            np.savez_compressed(artifact / filename, beta_ai_cat=beta)
            posterior_files[f"{outcome}_{specification}"] = filename

    manifest = {
        "schema_version": "ai-impact-hierarchical-v1",
        "model_version": "model-v1",
        "promoted": True,
        "categories": CATEGORIES,
        "posterior_files": posterior_files,
        "transform": transform,
        "baseline_signal": {"uncapped": 0.0, "capped": 0.0},
        "latest_completed_portfolio_week": weeks[-1].isoformat(),
    }
    (artifact / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return artifact


def _panel() -> pd.DataFrame:
    weeks = pd.date_range("2026-01-11", periods=16, freq="W-SUN")
    return pd.DataFrame(
        {
            "week": weeks,
            "ai_sessions": np.linspace(4, 20, len(weeks)),
            "ai_purchases": np.linspace(0, 3, len(weeks)),
            "seo_sessions": np.full(len(weeks), 1_000.0),
            "direct_sessions": np.full(len(weeks), 700.0),
            "total_sessions": np.full(len(weeks), 5_000.0),
            "total_purchases": np.full(len(weeks), 100.0),
            "trends_brand": np.linspace(10, 80, len(weeks)),
        }
    )


def test_artifact_rejects_unpromoted_bundle(tmp_path: Path) -> None:
    path = _artifact(tmp_path)
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["promoted"] = False
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ArtifactCompatibilityError, match="not promoted"):
        load_artifact(path)


def test_category_and_brand_term_validation() -> None:
    assert validate_category("publisher") == "publisher"
    with pytest.raises(ValueError, match="Invalid model category"):
        validate_category("retail")
    assert select_brand_trends_column(_panel()) == "trends_brand"
    with pytest.raises(ValueError, match="Multiple"):
        select_brand_trends_column(_panel().assign(trends_generic=20))


def test_training_parity_christmas_mask() -> None:
    values = pd.Series(pd.to_datetime(["2025-12-14", "2025-12-21", "2026-01-04", "2026-01-11"]))
    assert is_model_christmas_week(values).tolist() == [False, True, True, False]


def test_category_scoring_returns_posterior_and_capped_sensitivity(
    tmp_path: Path,
) -> None:
    artifact = load_artifact(_artifact(tmp_path))
    result = score_category_posterior(
        _panel(),
        category="advertiser-retail",
        window_weeks=13,
        artifact=artifact,
        as_of=date(2026, 5, 10),
    )

    assert result.estimate_mode == "category_posterior"
    assert result.model_artifact_version == "model-v1"
    assert result.category == "advertiser-retail"
    assert result.seo.actual_sessions == 13_000
    assert result.direct.actual_sessions == 9_100
    assert result.seo.uncapped.posterior_mean > 0
    assert result.seo.capped.posterior_mean > 0
    assert result.seo.sensitivity_delta != 0
    assert len(result.weekly_series) == 13
    assert "seo_uncapped_counterfactual" in result.weekly_series[-1]
    assert "No modeled purchase effect" in " ".join(result.method_notes)

import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "research"
    / "modelling"
    / "train_baseline_artifact.py"
)


@pytest.fixture
def trainer(monkeypatch):
    custom_ci = types.ModuleType("custom_ci")
    monkeypatch.setitem(sys.modules, "custom_ci", custom_ci)
    spec = importlib.util.spec_from_file_location("baseline_trainer_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _args(output_root: Path, version: str = "candidate-v2") -> argparse.Namespace:
    return argparse.Namespace(
        version=version,
        output_root=str(output_root),
        warmup=2,
        samples=3,
        chains=1,
        validation_warmup=2,
        validation_samples=3,
        validation_chains=1,
        max_divergences=2,
        max_rhat=1.02,
        min_ess=200,
        repair_fit=None,
    )


def test_existing_version_is_never_overwritten(trainer, tmp_path):
    existing = tmp_path / "stable-v1"
    existing.mkdir()
    marker = existing / "marker"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="choose a new --version"):
        trainer.train(_args(tmp_path, "stable-v1"))

    assert marker.read_text(encoding="utf-8") == "keep"


def test_failed_candidate_is_complete_and_does_not_change_current(
    trainer, monkeypatch, tmp_path
):
    categories = ["advertiser-retail", "advertiser-services", "publisher"]
    weeks = pd.to_datetime(["2026-07-05", "2026-07-12", "2026-07-19"])
    raw_sites = pd.DataFrame(
        {
            "site": ["a", "b", "c"],
            "category": categories,
            "n_weeks": [3, 3, 3],
        }
    )
    signal = pd.DataFrame(
        {
            "week_start": weeks,
            "ai_signal": [0.0, 0.5, 1.0],
            "ai_signal_capped": [0.0, 0.5, 0.8],
        }
    )
    panel = pd.DataFrame(
        {
            "site": ["a", "b", "c"],
            "category": categories,
            "date": weeks,
            "seo_sessions": [100.0, 110.0, 120.0],
            "direct_sessions": [80.0, 90.0, 100.0],
            "ai_signal": [0.0, 0.5, 1.0],
            "ai_signal_capped": [0.0, 0.5, 0.8],
        }
    )

    class FakeMCMC:
        def get_samples(self, group_by_chain=False):
            return {"beta_ai_cat": np.ones((3, 3))}

    ci = trainer.custom_ci
    ci.aggregate_site_data = lambda: (panel.copy(), raw_sites.copy(), categories, signal)
    ci.build_ai_adoption_index = lambda raw: (signal.copy(), {"kind": "test"})
    ci.detect_ai_ramp_start = lambda value: weeks[0]
    ci.ai_baseline_value = lambda value, start, column: 0.0
    ci.load_brand_trends = lambda sites: pd.DataFrame()
    ci.prepare_model_data = lambda *unused: (
        panel.copy(),
        raw_sites.copy(),
        ["a", "b", "c"],
        {"cat_of_site": np.arange(3)},
        {"log_seo": np.ones(3), "log_direct": np.ones(3)},
    )
    ci.model_data_with_ai_signal = lambda model_data, panel, column: model_data
    ci.fit_outcome_model = lambda *args, **kwargs: FakeMCMC()
    monkeypatch.setattr(
        trainer,
        "_diagnostics",
        lambda *args: {"divergences": 0, "max_r_hat": 1.0, "min_ess_bulk": 500},
    )
    monkeypatch.setattr(
        trainer,
        "_holdout_validation",
        lambda **kwargs: [
            {
                "category": categories[0],
                "held_out_site": "a",
                "outcome": "seo",
                "direction_agrees": False,
                "cold_divergences": 0,
            }
        ],
    )
    monkeypatch.setattr(trainer, "_git_commit", lambda: "abc123")
    monkeypatch.setattr(
        trainer, "_dependency_versions", lambda: {"python": "test", "numpy": "test"}
    )
    (tmp_path / "CURRENT").write_text("stable-v1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="failed promotion checks"):
        trainer.train(_args(tmp_path))

    candidate = tmp_path / "candidate-v2"
    manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
    assert (tmp_path / "CURRENT").read_text(encoding="utf-8") == "stable-v1\n"
    assert candidate.is_dir()
    assert not list(tmp_path.glob(".candidate-v2.staging-*"))
    assert manifest["promoted"] is False
    assert manifest["training_panel_version"] == "3-site-ga4-brand-trends-v2"
    assert manifest["source"]["site_count"] == 3
    assert manifest["source"]["week_count"] == 3
    assert manifest["git_commit"] == "abc123"
    assert manifest["promotion_thresholds"]["min_ess_bulk"] == 200
    assert manifest["seeds"]["production_fits"]["seo_uncapped"] == 1000
    assert manifest["input_data_sha256"]
    for filename, expected_hash in manifest["bundle_sha256"].items():
        assert trainer._sha256(candidate / filename) == expected_hash

    monkeypatch.setattr(
        trainer,
        "_holdout_validation",
        lambda **kwargs: [
            {
                "category": categories[0],
                "held_out_site": "a",
                "outcome": "seo",
                "direction_agrees": True,
                "cold_divergences": 0,
            }
        ],
    )
    promoted = trainer.train(_args(tmp_path, "candidate-v3"))

    assert promoted == tmp_path / "candidate-v3"
    assert (tmp_path / "CURRENT").read_text(encoding="utf-8") == "candidate-v3\n"
    assert json.loads((promoted / "manifest.json").read_text())["promoted"] is True


def test_repair_rejects_promoted_or_current_artifact(trainer, tmp_path):
    artifact = tmp_path / "stable-v1"
    artifact.mkdir()
    (artifact / "manifest.json").write_text(
        json.dumps({"promoted": True, "promotion_thresholds": {}}),
        encoding="utf-8",
    )
    (tmp_path / "CURRENT").write_text("stable-v1\n", encoding="utf-8")
    args = _args(tmp_path, "stable-v1")
    args.repair_fit = "seo_uncapped"

    with pytest.raises(ValueError, match="only allowed for an unpromoted"):
        trainer.repair_fit(args)


def test_version_environment_prefers_ai_impact_name(trainer, monkeypatch):
    monkeypatch.setenv("AI_MODEL_VERSION", "legacy")
    monkeypatch.setenv("AI_IMPACT_MODEL_VERSION", "preferred")
    monkeypatch.setattr(sys, "argv", ["train_baseline_artifact.py"])

    assert trainer.parse_args().version == "preferred"

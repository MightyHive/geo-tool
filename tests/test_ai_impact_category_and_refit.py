import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import numpy as np
import pytest

from api import ai_impact as ai_impact_api
from api.ai_impact import (
    CreateAiImpactRunRequest,
    _refit_eligibility,
    _refresh_refit_state,
    create_run,
    get_run,
)
from api.audit_runner import _persist_model_category_metadata
from backend import geo_setup_llm
from jobs.ai_impact_refit.run_job import (
    prepare_site_inputs,
    recompute_portfolio_signal,
)


class _GeminiModels:
    def __init__(self, payload: str):
        self.payload = payload

    def generate_content(self, **_kwargs):
        return SimpleNamespace(text=self.payload)


def test_gemini_category_is_strictly_validated(monkeypatch) -> None:
    monkeypatch.setattr(
        geo_setup_llm,
        "build_genai_client",
        lambda: SimpleNamespace(
            models=_GeminiModels('{"category":"advertiser-services"}')
        ),
    )
    result = geo_setup_llm.classify_site_model_category("https://example.com")
    assert result["category"] == "advertiser-services"
    assert result["classifier"] == "gemini-site-category-v1"

    monkeypatch.setattr(
        geo_setup_llm,
        "build_genai_client",
        lambda: SimpleNamespace(models=_GeminiModels('{"category":"retail"}')),
    )
    with pytest.raises(ValueError, match="Invalid model category"):
        geo_setup_llm.classify_site_model_category("https://example.com")


def test_category_and_provenance_are_persisted(tmp_path: Path) -> None:
    result = _persist_model_category_metadata(
        tmp_path,
        {
            "category": "publisher",
            "classifier": "gemini-site-category-v1",
            "provider": "google-gemini",
            "model": "gemini-test",
        },
    )
    stored = json.loads((tmp_path / "onboarding_context.json").read_text())
    assert result["model_category"] == "publisher"
    assert stored["model_category"] == "publisher"
    assert stored["model_category_provenance"]["model"] == "gemini-test"
    assert stored["model_category_provenance"]["classified_at"]


def test_refit_eligibility_uses_completed_model_christmas_mask() -> None:
    weeks = pd.date_range("2025-11-02", periods=14, freq="W-SUN")
    panel = pd.DataFrame(
        {
            "week": weeks,
            "seo_sessions": 100.0,
            "direct_sessions": 80.0,
            "ai_sessions": 2.0,
            "total_sessions": 500.0,
            "trends_1": 20.0,
        }
    )
    result = _refit_eligibility(
        panel,
        category="publisher",
        brand_trends_column="trends_1",
        has_ga4=True,
        has_brand_trends=True,
        as_of=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    expected = panel.loc[
        ~(
            (panel["week"].dt.month * 100 + panel["week"].dt.day >= 1215)
            | (panel["week"].dt.month * 100 + panel["week"].dt.day <= 107)
        ),
        "week",
    ].nunique()
    assert result["eligible_weeks"] == expected
    assert result["eligible"] is True


def test_polling_promotes_site_refit(tmp_path: Path) -> None:
    (tmp_path / "estimate.json").write_text(
        json.dumps(
            {
                "estimate_mode": "site_refit",
                "artifact_versions": {
                    "baseline_model": "model-v1",
                    "refit_signal": "2026-08-02",
                },
                "hierarchical_refit": {"status": "completed"},
            }
        )
    )
    meta = {
        "run_id": "run-1",
        "jobs": {"refit": "queued"},
        "hierarchical_refit": {"status": "queued"},
    }
    assert _refresh_refit_state(tmp_path, meta) is True
    assert meta["hierarchical_refit"]["status"] == "completed"
    assert meta["jobs"]["refit"] == "completed"


def test_refit_preparation_requires_selected_brand_term(monkeypatch) -> None:
    weeks = pd.date_range("2026-02-01", periods=10, freq="W-SUN")
    panel = pd.DataFrame(
        {
            "week": weeks,
            "seo_sessions": 100.0,
            "direct_sessions": 80.0,
            "ai_sessions": 2.0,
            "total_sessions": 500.0,
            "trends_1": 20.0,
            "trends_2": 30.0,
        }
    )
    with pytest.raises(ValueError, match="Multiple Google Trends"):
        prepare_site_inputs(
            panel,
            site_id="new-site",
            category="publisher",
            signal_weeks=pd.Series(weeks),
            as_of=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )

    monkeypatch.setenv("AI_IMPACT_BRAND_TRENDS_COLUMN", "trends_2")
    model_panel, trends, contribution = prepare_site_inputs(
        panel,
        site_id="new-site",
        category="publisher",
        signal_weeks=pd.Series(weeks),
        as_of=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert len(model_panel) == 10
    assert trends["brand_interest"].eq(30).all()
    assert contribution["site_all_sessions"].eq(500).all()


def test_refit_recomputes_overlapping_portfolio_signal() -> None:
    weeks = pd.date_range("2026-02-01", periods=2, freq="W-SUN")
    baseline = pd.DataFrame(
        {
            "week_start": weeks,
            "ai_sessions_total": [10.0, 20.0],
            "all_sessions_total": [1_000.0, 1_000.0],
        }
    )
    additions = pd.DataFrame(
        {
            "week_start": weeks,
            "site_ai_sessions": [1.0, 2.0],
            "site_all_sessions": [100.0, 100.0],
        }
    )
    signal = recompute_portfolio_signal(
        baseline,
        additions,
        {
            "share_scale": 0.01,
            "transform_mean": 0.0,
            "transform_std": 1.0,
            "cap_low": 0.0,
            "cap_high": 1.0,
        },
    )
    assert signal["ai_sessions_total"].tolist() == [11.0, 22.0]
    assert signal["all_sessions_total"].tolist() == [1_100.0, 1_100.0]
    assert {"ai_signal", "ai_signal_capped"} <= set(signal.columns)


def _write_api_artifact(root: Path, weeks: pd.DatetimeIndex) -> None:
    artifact = root / "model-v1"
    artifact.mkdir(parents=True)
    signal = pd.DataFrame(
        {
            "week_start": weeks,
            "ai_sessions_total": np.linspace(100, 300, len(weeks)),
            "all_sessions_total": 100_000.0,
        }
    )
    signal["ai_share"] = signal["ai_sessions_total"] / signal["all_sessions_total"]
    transform = {
        "share_scale": 0.001,
        "transform_mean": 0.0,
        "transform_std": 1.0,
        "cap_low": 0.0,
        "cap_high": 0.5,
    }
    from backend.ai_impact.model_artifact import transform_ai_share

    signal["ai_signal"] = transform_ai_share(
        signal["ai_share"], transform, capped=False
    )
    signal["ai_signal_capped"] = transform_ai_share(
        signal["ai_share"], transform, capped=True
    )
    signal.to_csv(artifact / "portfolio_signal.csv.gz", index=False)
    posterior_files = {}
    for outcome in ("seo", "direct"):
        for spec in ("uncapped", "capped"):
            filename = f"posterior_{outcome}_{spec}.npz"
            np.savez_compressed(
                artifact / filename,
                beta_ai_cat=np.tile([[0.03, -0.01, 0.02]], (100, 1)),
            )
            posterior_files[f"{outcome}_{spec}"] = filename
    (artifact / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "ai-impact-hierarchical-v1",
                "model_version": "model-v1",
                "promoted": True,
                "categories": [
                    "advertiser-retail",
                    "advertiser-services",
                    "publisher",
                ],
                "posterior_files": posterior_files,
                "transform": transform,
                "baseline_signal": {"uncapped": 0.0, "capped": 0.0},
                "latest_completed_portfolio_week": weeks[-1].isoformat(),
            }
        )
    )
    (root / "CURRENT").write_text("model-v1\n")


def test_api_persists_immediate_category_estimate(
    tmp_path: Path, monkeypatch
) -> None:
    from api import geo_services as geo

    weeks = pd.date_range("2026-02-01", periods=13, freq="W-SUN")
    artifact_root = tmp_path / "artifacts"
    _write_api_artifact(artifact_root, weeks)
    monkeypatch.setenv("AI_IMPACT_MODEL_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("AI_IMPACT_REFIT_MODE", "noop")

    panel = pd.DataFrame(
        {
            "week": weeks,
            "ai_sessions": 10.0,
            "ai_purchases": 1.0,
            "seo_sessions": 1_000.0,
            "direct_sessions": 700.0,
            "total_sessions": 5_000.0,
            "total_purchases": 100.0,
        }
    )
    panel_path = tmp_path / "site_panel.csv"
    panel.to_csv(panel_path, index=False)
    trends_path = tmp_path / "trends.csv"
    pd.DataFrame({"week": weeks, "trends_1": 50.0}).to_csv(
        trends_path, index=False
    )
    runs = tmp_path / "runs"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "audit_summary.json").write_text("{}")
    (audit_dir / "onboarding_context.json").write_text(
        json.dumps({"model_category": "publisher"})
    )
    monkeypatch.setattr(ai_impact_api, "_runs_root", lambda: runs)
    monkeypatch.setattr(geo, "resolve_audit_dir", lambda _audit_id: audit_dir)
    monkeypatch.setattr(
        ai_impact_api,
        "_resolve_trends_upload",
        lambda _upload_id: (
            trends_path,
            {"terms": ["Example Brand"], "upload_id": "a" * 32},
        ),
    )

    result = create_run(
        CreateAiImpactRunRequest(
            category="publisher",
            trends_upload_id="a" * 32,
            local_panel_path=str(panel_path),
            window_weeks=13,
            audit_id="audit-1",
        ),
        SimpleNamespace(session={}),
    )
    assert result.estimate["estimate_mode"] == "category_posterior"
    assert result.cold_start_estimate == result.estimate
    run_dir = runs / result.run_id
    assert (run_dir / "cold_start_estimate.json").is_file()
    assert json.loads((run_dir / "meta.json").read_text())["category"] == "publisher"
    assert json.loads((audit_dir / "ai_impact" / "latest.json").read_text())[
        "run_id"
    ] == result.run_id

    # Fixture end-to-end replacement: emulate the async job's atomic output,
    # then verify the existing poll endpoint promotes it over the cold estimate.
    refined = dict(result.estimate)
    refined["estimate_mode"] = "site_refit"
    refined["hierarchical_refit"] = {"status": "completed"}
    (run_dir / "estimate.json").write_text(json.dumps(refined))
    polled = get_run(result.run_id)
    assert polled.estimate["estimate_mode"] == "site_refit"
    assert polled.cold_start_estimate["estimate_mode"] == "category_posterior"
    assert polled.hierarchical_refit["status"] == "completed"


def test_polling_preserves_old_runs_and_exposes_refit_failure(
    tmp_path: Path, monkeypatch
) -> None:
    runs = tmp_path / "runs"
    run_dir = runs / "legacy"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "run_id": "legacy",
                "status": "completed",
                "created_at": "2026-01-01T00:00:00Z",
                "jobs": {"estimate": "completed"},
            }
        )
    )
    (run_dir / "estimate.json").write_text(
        json.dumps({"estimate_mode": "legacy", "weekly_series": []})
    )
    monkeypatch.setattr(ai_impact_api, "_runs_root", lambda: runs)
    legacy = get_run("legacy")
    assert legacy.estimate["estimate_mode"] == "legacy"
    assert legacy.cold_start_estimate is None

    (run_dir / "refit_status.json").write_text(
        json.dumps({"status": "failed", "error": "sampling failed"})
    )
    failed = get_run("legacy")
    assert failed.hierarchical_refit["status"] == "failed"
    assert failed.hierarchical_refit["error"] == "sampling failed"


def test_export_uses_only_audit_local_hierarchical_estimate(tmp_path: Path) -> None:
    from api.export_page_builders import build_ga4_traffic_export

    interval = {"posterior_mean": 20.0, "lower_94": 5.0, "upper_94": 35.0}
    estimate = {
        "window_start": "2026-02-01",
        "window_end": "2026-02-08",
        "category": "publisher",
        "estimate_mode": "category_posterior",
        "model_artifact_version": "model-v1",
        "posterior_outcomes": {
            "seo": {"uncapped": interval, "capped": interval},
            "direct": {"uncapped": interval, "capped": interval},
        },
        "weekly_series": [
            {
                "week": "2026-02-01",
                "seo_sessions": 100,
                "direct_sessions": 80,
                "seo_uncapped_counterfactual": interval,
                "direct_uncapped_counterfactual": interval,
            },
            {
                "week": "2026-02-08",
                "seo_sessions": 110,
                "direct_sessions": 85,
                "seo_uncapped_counterfactual": interval,
                "direct_uncapped_counterfactual": interval,
            },
        ],
    }
    (tmp_path / "ai_impact_estimate.json").write_text(json.dumps(estimate))
    output = build_ga4_traffic_export(tmp_path)
    assert "category_posterior" in output
    assert "SEO actual" in output
    assert "Primary mean (94% CI)" in output
    assert "±10%" not in output


def test_load_ai_impact_estimate_returns_none_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api.ai_impact import load_ai_impact_estimate

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(ai_impact_api, "_runs_root", lambda: runs)
    assert load_ai_impact_estimate(tmp_path) is None
    (tmp_path / "ai_impact_estimate.json").write_text("not-json", encoding="utf-8")
    assert load_ai_impact_estimate(tmp_path) is None


def test_completed_models_are_persisted_audit_locally_and_latest_wins(
    tmp_path: Path,
) -> None:
    audit_dir = tmp_path / "audit"
    (audit_dir / "audit_summary.json").parent.mkdir(parents=True)
    (audit_dir / "audit_summary.json").write_text("{}")
    older = {
        "run_id": "run-old",
        "audit_id": "audit",
        "created_at": "2026-01-01T00:00:00+00:00",
        "jobs": {"estimate": "completed"},
    }
    newer = {
        "run_id": "run-new",
        "audit_id": "audit",
        "created_at": "2026-02-01T00:00:00+00:00",
        "jobs": {"estimate": "completed"},
    }

    ai_impact_api.persist_completed_model(
        audit_dir,
        meta=older,
        estimate={"estimate_mode": "category_posterior", "value": 1},
    )
    ai_impact_api.persist_completed_model(
        audit_dir,
        meta=newer,
        estimate={"estimate_mode": "category_posterior", "value": 2},
    )

    latest = json.loads((audit_dir / "ai_impact" / "latest.json").read_text())
    assert latest["run_id"] == "run-new"
    assert (
        audit_dir / "ai_impact" / "models" / "run-old" / "model.json"
    ).is_file()
    assert (
        audit_dir / "ai_impact" / "models" / "run-new" / "model.json"
    ).is_file()
    assert json.loads((audit_dir / "ai_impact_estimate.json").read_text())[
        "value"
    ] == 2


def test_latest_audit_model_refreshes_completed_refit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api import geo_services as geo

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "audit_summary.json").write_text("{}")
    runs = tmp_path / "runs"
    run_dir = runs / "run-1"
    run_dir.mkdir(parents=True)
    meta = {
        "run_id": "run-1",
        "audit_id": "audit-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "completed",
        "jobs": {"estimate": "completed", "refit": "queued"},
        "hierarchical_refit": {"status": "queued"},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta))
    (run_dir / "estimate.json").write_text(
        json.dumps({"estimate_mode": "category_posterior"})
    )
    monkeypatch.setattr(ai_impact_api, "_runs_root", lambda: runs)
    monkeypatch.setattr(
        ai_impact_api, "_sync_gcs_refit_outputs", lambda _run_dir, _run_id: None
    )
    monkeypatch.setattr(geo, "resolve_audit_dir", lambda _audit_id: audit_dir)
    ai_impact_api.persist_completed_model(
        audit_dir,
        meta=meta,
        estimate={"estimate_mode": "category_posterior"},
    )

    (run_dir / "estimate.json").write_text(
        json.dumps(
            {
                "estimate_mode": "site_refit",
                "hierarchical_refit": {"status": "completed"},
            }
        )
    )
    restored = ai_impact_api.load_ai_impact_estimate(audit_dir)

    assert restored["estimate_mode"] == "site_refit"
    assert restored["_run_id"] == "run-1"
    model = json.loads(
        (
            audit_dir
            / "ai_impact"
            / "models"
            / "run-1"
            / "model.json"
        ).read_text()
    )
    assert model["estimate"]["estimate_mode"] == "site_refit"


def test_legacy_global_runs_migrate_latest_for_matching_audit_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api import geo_services as geo

    audit_dir = tmp_path / "audit"
    other_audit_dir = tmp_path / "other"
    audit_dir.mkdir()
    other_audit_dir.mkdir()
    runs = tmp_path / "runs"
    for run_id, audit_id, created_at, value in [
        ("old", "audit-1", "2026-01-01T00:00:00+00:00", 1),
        ("new", "audit-1", "2026-02-01T00:00:00+00:00", 2),
        ("other", "audit-2", "2026-03-01T00:00:00+00:00", 3),
    ]:
        run_dir = runs / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "meta.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "audit_id": audit_id,
                    "created_at": created_at,
                    "status": "completed",
                    "jobs": {"estimate": "completed"},
                }
            )
        )
        (run_dir / "estimate.json").write_text(
            json.dumps({"estimate_mode": "category_posterior", "value": value})
        )
    monkeypatch.setattr(ai_impact_api, "_runs_root", lambda: runs)
    monkeypatch.setattr(
        ai_impact_api, "_sync_gcs_refit_outputs", lambda _run_dir, _run_id: None
    )
    monkeypatch.setattr(
        geo,
        "resolve_audit_dir",
        lambda audit_id: audit_dir if audit_id == "audit-1" else other_audit_dir,
    )

    restored = ai_impact_api.load_ai_impact_estimate(audit_dir)

    assert restored["value"] == 2
    assert restored["_run_id"] == "new"
    assert json.loads((audit_dir / "ai_impact" / "latest.json").read_text())[
        "run_id"
    ] == "new"


def test_get_ai_impact_estimate_restores_saved_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    from api import geo_services as geo
    from api.main import AiImpactEstimateBody, get_ai_impact_estimate, save_ai_impact_estimate

    monkeypatch.setattr(geo, "resolve_audit_dir", lambda _audit_id: tmp_path)

    with pytest.raises(HTTPException) as missing_audit:
        get_ai_impact_estimate("audit-1")
    assert missing_audit.value.status_code == 404

    (tmp_path / "audit_summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(HTTPException) as missing_estimate:
        get_ai_impact_estimate("audit-1")
    assert missing_estimate.value.status_code == 404

    save_ai_impact_estimate(
        "audit-1",
        AiImpactEstimateBody(
            estimate={"window_start": "2026-01-01", "window_end": "2026-04-01"},
            run_id="run-9",
        ),
    )
    payload = get_ai_impact_estimate("audit-1")
    assert payload["_run_id"] == "run-9"
    assert payload["window_start"] == "2026-01-01"
    assert payload["window_end"] == "2026-04-01"

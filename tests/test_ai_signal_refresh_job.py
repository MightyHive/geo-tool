from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from backend.ai_impact.model_artifact import HierarchicalArtifact
from jobs.ai_signal_refresh import run_job


def _artifact(tmp_path: Path) -> HierarchicalArtifact:
    sites = [f"property_{index:04d}" for index in range(1, 82)]
    return HierarchicalArtifact(
        path=tmp_path / "model",
        manifest={
            "schema_version": "ai-impact-hierarchical-v1",
            "model_version": "frozen-model-v1",
            "site_order": sites,
            "transform": {
                "share_scale": 0.1,
                "transform_mean": 0.25,
                "transform_std": 0.5,
                "cap_low": 0.1,
                "cap_high": 2.0,
                "reference_end": "2026-06-01",
            },
            "training_panel_version": "81-site-test-v1",
        },
    )


def _write_sessions(input_dir: Path, artifact: HierarchicalArtifact) -> None:
    complete_days = pd.date_range("2026-07-05", periods=7, freq="D")
    incomplete_days = pd.date_range("2026-07-12", periods=3, freq="D")
    for site in artifact.manifest["site_order"]:
        rows: list[dict[str, object]] = []
        for day in [*complete_days, *incomplete_days]:
            rows.extend(
                [
                    {
                        "date": day.date().isoformat(),
                        "name": site,
                        "custom_channel_grouping": "AI Chatbots",
                        "sessions": 1,
                    },
                    {
                        "date": day.date().isoformat(),
                        "name": site,
                        "custom_channel_grouping": "SEO",
                        "sessions": 2,
                    },
                ]
            )
        pd.DataFrame(rows).to_csv(input_dir / f"sessions_{site}.csv", index=False)


def test_compute_weekly_signal_excludes_incomplete_week_and_preserves_totals(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    input_dir = tmp_path / "sessions"
    input_dir.mkdir()
    _write_sessions(input_dir, artifact)

    signal, details = run_job.compute_weekly_signal(
        input_dir, artifact, as_of="2026-07-15T12:00:00Z"
    )

    assert signal["week_start"].dt.strftime("%Y-%m-%d").tolist() == ["2026-07-05"]
    assert signal.loc[0, "ai_sessions_total"] == 81 * 7
    assert signal.loc[0, "all_sessions_total"] == 81 * 7 * 3
    expected = (np.log1p((1 / 3) / 0.1) - 0.25) / 0.5
    assert signal.loc[0, "ai_signal"] == expected
    assert signal.loc[0, "ai_signal_capped"] == expected
    assert details["site_count"] == 81
    assert details["latest_completed_week"] == "2026-07-05"


def test_compute_weekly_signal_uses_exact_totals_for_dynamic_site_count(
    tmp_path: Path,
) -> None:
    artifact = _artifact(tmp_path)
    artifact.manifest["site_order"] = artifact.manifest["site_order"][:2]
    input_dir = tmp_path / "sessions"
    input_dir.mkdir()
    complete_days = pd.date_range("2026-07-05", periods=7, freq="D")
    for site in artifact.manifest["site_order"]:
        rows = [
            {
                "date": day.date().isoformat(),
                "name": site,
                "custom_channel_grouping": channel,
                "sessions": sessions,
            }
            for day in complete_days
            for channel, sessions in (("AI Chatbots", 1), ("SEO", 20))
        ]
        rows.append(
            {
                "date": "2026-07-05",
                "name": site,
                "custom_channel_grouping": "All Sessions",
                "sessions": 1000,
            }
        )
        pd.DataFrame(rows).to_csv(input_dir / f"sessions_{site}.csv", index=False)

    signal, details = run_job.compute_weekly_signal(
        input_dir, artifact, as_of="2026-07-15T12:00:00Z"
    )

    assert signal.loc[0, "ai_sessions_total"] == 14
    assert signal.loc[0, "all_sessions_total"] == 2000
    assert details["site_count"] == 2


def test_refresh_publishes_immutable_version_and_latest_pointer(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    input_dir = tmp_path / "sessions"
    input_dir.mkdir()
    _write_sessions(input_dir, artifact)
    output_dir = tmp_path / "published"

    result = run_job.refresh_and_publish(
        input_path=input_dir,
        output_uri=output_dir,
        artifact=artifact,
        as_of="2026-07-15",
        version="signal-test-v1",
    )

    version_dir = output_dir / "signal-test-v1"
    manifest = json.loads((version_dir / "manifest.json").read_text())
    signal = pd.read_csv(version_dir / "portfolio_signal.csv.gz")
    assert result["published_uri"] == str(version_dir)
    assert (output_dir / "CURRENT").read_text() == "signal-test-v1\n"
    assert json.loads((output_dir / "latest.json").read_text())["signal_version"] == (
        "signal-test-v1"
    )
    assert manifest["posterior_retrained"] is False
    assert manifest["model_version"] == "frozen-model-v1"
    assert manifest["latest_completed_week"] == "2026-07-05"
    assert manifest["transform"] == artifact.manifest["transform"]
    assert {"ai_sessions_total", "all_sessions_total"}.issubset(signal.columns)

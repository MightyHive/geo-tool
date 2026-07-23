import numpy as np
import pandas as pd

from backend.ai_impact.estimate import estimate_ai_impact


def _panel(weeks: int) -> pd.DataFrame:
    index = np.arange(weeks, dtype=float)
    ai_sessions = 120 + index * 2.5 + np.sin(index) * 4
    seo_sessions = 8_000 + index * 18 + ai_sessions * 1.8
    total_sessions = 24_000 + index * 35 + ai_sessions * 2.4
    total_purchases = 1_100 + index * 2 + ai_sessions * 0.08
    seo_purchases = 360 + index + ai_sessions * 0.03
    return pd.DataFrame(
        {
            "week": pd.date_range("2025-01-06", periods=weeks, freq="W-MON"),
            "ai_sessions": ai_sessions,
            "ai_purchases": ai_sessions * 0.06,
            "seo_sessions": seo_sessions,
            "seo_purchases": seo_purchases,
            "total_sessions": total_sessions,
            "total_purchases": total_purchases,
            "non_ai_sessions": total_sessions - ai_sessions,
        }
    )


def test_estimate_includes_bounded_model_quality_score() -> None:
    result = estimate_ai_impact(_panel(70), window_weeks=13)

    assert 0 <= result.model_quality_score <= 100
    assert result.model_quality_score >= 70
    assert result.p_value is not None
    assert 0 <= result.p_value <= 1
    assert "not statistical confidence" in " ".join(result.method_notes)
    assert result.quality_narrative != "sessions_up_quality_down"
    assert any("Black Friday → Twelfth Night" in note for note in result.method_notes)
    assert not any("mask_bf_twelfth" in note for note in result.method_notes)
    assert not any(note.startswith("UK Black Friday") for note in result.method_notes)


def test_model_quality_rewards_longer_history_and_baseline_coverage() -> None:
    short = estimate_ai_impact(_panel(10), window_weeks=8)
    long = estimate_ai_impact(_panel(70), window_weeks=13)

    assert long.model_quality_score > short.model_quality_score


def test_quality_narrative_never_emits_sessions_up_quality_down() -> None:
    from backend.ai_impact.estimate import _quality_narrative

    assert (
        _quality_narrative(site_cvr=0.1, ai_cvr=0.05, seo_net_central=100.0)
        == "mixed_or_aligned"
    )

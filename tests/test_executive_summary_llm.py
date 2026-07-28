"""Unit tests for executive-summary digest, score bands, and SOV framing."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import executive_summary_llm as es  # noqa: E402


def test_score_label_matches_ui_bands() -> None:
    assert es._score_label(90) == "Excellent"
    assert es._score_label(75) == "Good"
    # Banding uses nearest-integer display (74.6 → 75 Good; 74.4 → 74 OK).
    assert es._score_label(74.6) == "Good"
    assert es._score_label(74.4) == "OK"
    assert es._score_label(60) == "OK"
    assert es._score_label(59.4) == "Weak"
    assert es._score_label(59.5) == "OK"
    assert es._score_label(40) == "Weak"
    assert es._score_label(39.4) == "Poor"
    assert es._score_label(39.5) == "Weak"


def test_build_executive_digest_score_60_is_ok() -> None:
    categories = [
        SimpleNamespace(
            key="ai_visibility",
            title="AI Visibility",
            score=55.0,
            weight=40.0,
            improvements=["Improve answer clarity"],
        ),
        SimpleNamespace(
            key="technical_setup",
            title="Technical Setup",
            score=70.0,
            weight=30.0,
            improvements=[],
        ),
        SimpleNamespace(
            key="content_structure",
            title="Content Quality & Structure",
            score=58.0,
            weight=30.0,
            improvements=["Add citations"],
        ),
    ]
    digest = es.build_executive_digest(
        {"base_url": "https://example.com", "audit_inputs": {"brand": "Example"}, "pages": [{}]},
        overall=60.0,
        categories=categories,
        priorities=[],
        working=["HTTPS enabled"],
        prompt_performance={"available": False},
    )
    assert digest["overall_score"] == 60.0
    assert digest["overall_rating"] == "OK"
    assert digest["narrative_rules"]["overall_rating_must_be"] == "OK"
    assert digest["pillar_names"] == list(es.PILLAR_TITLES)
    labels = {row["label"] for row in digest["score_label_bands"]}
    assert labels == {"Excellent", "Good", "OK", "Weak", "Poor"}


def test_sov_framing_rank_one_is_strength() -> None:
    framing = es._sov_framing(sov_rank=1, sov_pct=29.9, competitor_count=5)
    assert framing["tone"] == "strength"
    assert framing["leads_tracked_competitors"] is True
    assert "majority" in framing["instruction"].lower()
    assert "#1" in framing["instruction"]
    assert "29.9%" in framing["instruction"]


def test_sov_framing_rank_two_is_gap() -> None:
    framing = es._sov_framing(sov_rank=2, sov_pct=29.9, competitor_count=5)
    assert framing["tone"] == "gap"
    assert framing["leads_tracked_competitors"] is False
    assert "#2" in framing["instruction"]


def test_prompt_performance_digest_uses_canonical_rank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe = {
        "live_probe": {
            "brand_match_tokens": ["carglass"],
            "per_prompt": [
                {
                    "prompt": "Who repairs windscreens?",
                    "gemini_response": "Carglass is often recommended.",
                    "mention_scores_gemini": {
                        "brand_signal": 3,
                        "competitor_detail": {"rival.com": 2, "other.com": 1},
                    },
                }
            ],
        }
    }
    (tmp_path / "prompt_performance_live_probe.json").write_text(
        json.dumps(probe), encoding="utf-8"
    )

    monkeypatch.setattr(
        "api.geo_services.load_prompt_visibility_metrics",
        lambda _audit_dir, **_kwargs: {
            "visibility_pct": 80.0,
            "sov_pct": 29.9,
            "sov_performance_score": 100.0,
            "sov_rank": 1,
            "competitor_count": 4,
            "top_competitor_sov_pct": 22.0,
            "response_count": 10,
            "visible_response_count": 8,
            "tested_prompt_count": 1,
            "per_platform": {},
        },
    )

    digest = es.build_prompt_performance_digest(tmp_path, "Carglass")
    assert digest["available"] is True
    assert digest["share_of_voice_pct"] == 29.9
    assert digest["sov_rank"] == 1
    assert digest["sov_framing"]["tone"] == "strength"
    assert "majority" in digest["sov_framing"]["instruction"].lower()


def test_prompt_payload_locks_ok_label_and_sov_strength() -> None:
    digest = {
        "brand": "Carglass",
        "site_url": "https://www.carglass.co.uk",
        "overall_score": 60.0,
        "overall_rating": "OK",
        "score_label_bands": list(es.SCORE_LABEL_BANDS),
        "pillar_names": list(es.PILLAR_TITLES),
        "narrative_rules": {
            "overall_rating_must_be": "OK",
            "do_not_invent_score_bands": True,
        },
        "prompt_performance": {
            "available": True,
            "share_of_voice_pct": 29.9,
            "sov_rank": 1,
            "sov_framing": es._sov_framing(sov_rank=1, sov_pct=29.9, competitor_count=4),
        },
    }
    prompt = es.build_executive_summary_prompt(digest)
    assert "it is **OK** for score **60.0**" in prompt
    assert "60.0 is OK, not Weak" in prompt
    assert "SOV framing tone is **strength**" in prompt
    assert "do NOT claim competitors capture the majority" in prompt
    assert '"overall_rating": "OK"' in prompt
    assert '"sov_rank": 1' in prompt
    assert "AI Visibility" in prompt
    assert "Content Quality & Structure" in prompt


def test_schema_version_bumped_for_cache_invalidation() -> None:
    assert es.EXECUTIVE_SUMMARY_SCHEMA_VERSION >= 4


def test_load_cached_rejects_stale_schema(tmp_path: Path) -> None:
    stale = {
        "schema_version": 3,
        "paragraph_html": "Old Weak summary with majority SOV claim.",
        "source": "gemini",
    }
    (tmp_path / es.EXECUTIVE_SUMMARY_FILE).write_text(json.dumps(stale), encoding="utf-8")
    assert es.load_cached_executive_summary(tmp_path) is None

    fresh = {**stale, "schema_version": es.EXECUTIVE_SUMMARY_SCHEMA_VERSION}
    (tmp_path / es.EXECUTIVE_SUMMARY_FILE).write_text(json.dumps(fresh), encoding="utf-8")
    cached = es.load_cached_executive_summary(tmp_path)
    assert cached is not None
    assert cached["paragraph_html"].startswith("Old Weak")

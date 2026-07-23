from __future__ import annotations

import json

from api.geo_services import load_crawl_site, load_prompt_visibility_metrics


def _write_probe(tmp_path, *, brand_hits: float, competitors: dict[str, float]) -> None:
    payload = {
        "live_probe": {
            "brand_match_tokens": ["example"],
            "per_prompt": [
                {
                    "prompt": "Which brand is best?",
                    "gemini_response": "Example is one option.",
                    "mention_scores_gemini": {
                        "brand_signal": brand_hits,
                        "competitor_detail": competitors,
                    },
                }
            ],
        }
    }
    (tmp_path / "prompt_performance_live_probe.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_sov_performance_scores_relative_leader_as_top_score(tmp_path) -> None:
    _write_probe(tmp_path, brand_hits=7, competitors={"a.com": 5, "b.com": 4, "c.com": 3})

    metrics = load_prompt_visibility_metrics(tmp_path)

    assert metrics is not None
    assert metrics["sov_pct"] == 36.8
    assert metrics["sov_performance_score"] == 100.0
    assert metrics["sov_rank"] == 1
    assert metrics["score"] == 100.0


def test_sov_performance_uses_competitor_rank_not_raw_percentage(tmp_path) -> None:
    _write_probe(tmp_path, brand_hits=4, competitors={"leader.com": 8, "smaller.com": 2})

    metrics = load_prompt_visibility_metrics(tmp_path)

    assert metrics is not None
    assert metrics["sov_pct"] == 28.6
    assert metrics["sov_performance_score"] == 50.0
    assert metrics["sov_rank"] == 2
    assert metrics["score"] == 80.0


def test_sov_uses_only_ten_most_mentioned_competitors(tmp_path) -> None:
    competitors = {
        f"competitor-{index}.com": float(21 - index)
        for index in range(1, 13)
    }
    _write_probe(tmp_path, brand_hits=10, competitors=competitors)

    metrics = load_prompt_visibility_metrics(tmp_path)

    assert metrics is not None
    assert metrics["competitor_count"] == 10
    assert metrics["detected_competitor_count"] == 12
    assert metrics["sov_competitor_limit"] == 10
    assert metrics["competitor_hits"] == 155.0
    # Raw SOV uses all 12 website-backed competitors (not only the top 10).
    assert metrics["sov_pct"] == round(100.0 * 10 / (10 + 174), 1)
    assert metrics["sov_performance_score"] == 0.0


def test_sov_ignores_non_website_backed_competitor_names(tmp_path) -> None:
    _write_probe(
        tmp_path,
        brand_hits=5,
        competitors={"noise-word": 20, "rival.com": 5},
    )

    metrics = load_prompt_visibility_metrics(tmp_path)

    assert metrics is not None
    assert metrics["detected_competitor_count"] == 1
    assert metrics["sov_pct"] == 50.0
    assert metrics["sov_performance_score"] == 50.0


def test_visibility_counts_each_completed_platform_run(tmp_path) -> None:
    payload = {
        "live_probe": {
            "brand_match_tokens": ["example"],
            "per_prompt": [{
                "prompt": "Which brand is best?",
                "runs": {
                    "gemini": [
                        {
                            "response": "Example is recommended.",
                            "mention_scores": {"brand_signal": 1, "competitor_detail": {}},
                        },
                        {
                            "response": "A competitor is recommended.",
                            "mention_scores": {
                                "brand_signal": 0,
                                "competitor_detail": {"competitor.com": 1},
                            },
                        },
                        {
                            "response": "Example is also suitable.",
                            "mention_scores": {"brand_signal": 1, "competitor_detail": {}},
                        },
                    ]
                },
            }],
        }
    }
    (tmp_path / "prompt_performance_live_probe.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    metrics = load_prompt_visibility_metrics(tmp_path)

    assert metrics is not None
    assert metrics["visible_response_count"] == 2
    assert metrics["response_count"] == 3
    assert metrics["tested_prompt_count"] == 1
    assert metrics["visibility_pct"] == 66.7


def test_text_visibility_contributes_to_sov_when_stored_signal_is_missing(tmp_path) -> None:
    _write_probe(tmp_path, brand_hits=0, competitors={"competitor.com": 1})

    metrics = load_prompt_visibility_metrics(tmp_path)

    assert metrics is not None
    assert metrics["visibility_pct"] == 100.0
    assert metrics["brand_hits"] == 1.0
    assert metrics["sov_pct"] == 50.0


def test_crawl_retains_bounded_content_example_for_score_evidence() -> None:
    crawl = load_crawl_site()
    sentence = (
        "Our laboratory team tested the formulation across four skin types because "
        "customers need evidence that explains how the ingredients perform in daily use."
    )

    signals = crawl.compute_page_content_signals(f"<main><p>{sentence}</p></main>")

    assert signals["representative_excerpt"] == sentence
    assert len(signals["representative_excerpt"]) <= 420

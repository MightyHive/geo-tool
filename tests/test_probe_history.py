from __future__ import annotations

import json
from pathlib import Path

from api.probe_history import (
    _build_daily_summary,
    _load_history_entries,
    _summary_needs_repair,
)
from api.prompt_performance import _all_prompts_for_api, _competitor_lists


def test_daily_summary_uses_top_site_count_as_frequency() -> None:
    summary = _build_daily_summary(
        {
            "per_prompt": [],
            "top_cited_sites": [
                {"domain": "example.com", "count": 7},
                {"domain": "other.example", "frequency": 3},
            ],
        }
    )

    assert summary["top_cited_domains"] == [
        {"domain": "example.com", "frequency": 7},
        {"domain": "other.example", "frequency": 3},
    ]


def test_report_mode_keeps_competitors_for_prompt_scoring() -> None:
    urls, brands = _competitor_lists(
        [{"competitor_website": "https://cetaphil.co.uk", "competitor_brand": "Cetaphil"}],
        report_mode=True,
    )

    assert urls == ["https://cetaphil.co.uk"]
    assert brands == ["Cetaphil"]


def test_all_prompts_for_rerun_uses_every_stored_prompt() -> None:
    prompts = _all_prompts_for_api({
        "pss_rows": [
            {"prompts": [f"Prompt {index}" for index in range(30)]},
            {"prompts": ["Prompt 1", "Custom prompt"]},
        ],
    })

    assert len(prompts) == 31
    assert prompts[-1] == "Custom prompt"


def test_daily_summary_counts_each_run_for_visibility_and_sentiment() -> None:
    summary = _build_daily_summary({
        "brand_name": "Example",
        "brand_site_url": "https://example.com",
        "brand_match_tokens": ["example"],
        "competitor_urls": ["https://laroche-posay.example"],
        "competitor_brands": ["La Roche-Posay"],
        "reply_detected_brand_names": ["La Roche-Posay"],
        "per_prompt": [{
            "runs": {
                "gemini": [
                    {
                        "response": "Example is the best option.",
                        "mention_scores": {"brand_signal": 1, "competitor_detail": {}},
                    },
                    {
                        "response": "Example has a problem.",
                        "mention_scores": {"brand_signal": 1, "competitor_detail": {}},
                    },
                    {
                        "response": "La Roche-Posay is recommended.",
                        "mention_scores": {
                            "brand_signal": 0,
                            "competitor_detail": {"La Roche-Posay": 1},
                        },
                    },
                ]
            }
        }],
    })

    assert summary["gemini"]["response_count"] == 3
    assert summary["gemini"]["brand_visibility"] == 0.6667
    assert summary["gemini"]["brand_mentioned_count"] == 2
    assert summary["gemini"]["positive_brand_mention_count"] == 1
    assert summary["gemini"]["sentiment_score"] == 0.5
    assert summary["gemini"]["competitor_visibility"]["la roche-posay"] == 0.3333


def test_summary_needs_repair_ignores_missing_platforms() -> None:
    """Partial platform coverage must not force a full daily-blob rebuild."""
    assert not _summary_needs_repair({
        "gemini": {
            "brand_visibility": 0.5,
            "response_count": 2,
            "sentiment_score": 1.0,
            "competitor_visibility": {},
        },
    })
    assert _summary_needs_repair({
        "gemini": {"brand_visibility": 0.5},
    })


def test_load_history_entries_persists_repaired_summaries(tmp_path: Path) -> None:
    hist_dir = tmp_path / "probe_history"
    hist_dir.mkdir()
    (hist_dir / "2026-07-01.json").write_text(
        json.dumps({
            "brand_name": "Example",
            "brand_match_tokens": ["example"],
            "per_prompt": [{
                "runs": {
                    "gemini": [{
                        "response": "Example is recommended.",
                        "mention_scores": {"brand_signal": 1, "competitor_detail": {}},
                    }],
                },
            }],
            "top_cited_sites": [{"domain": "example.com", "count": 4}],
        }),
        encoding="utf-8",
    )
    index_path = tmp_path / "probe_history_index.json"
    index_path.write_text(
        json.dumps({
            "entries": [{
                "date": "2026-07-01",
                "summary": {"gemini": {"brand_visibility": 1.0}},
            }],
        }),
        encoding="utf-8",
    )

    entries = _load_history_entries(tmp_path)
    assert entries[0]["summary"]["gemini"]["response_count"] == 1
    assert entries[0]["summary"]["gemini"]["sentiment_score"] == 1.0

    saved = json.loads(index_path.read_text(encoding="utf-8"))
    assert saved["entries"][0]["summary"]["gemini"]["response_count"] == 1

    # Second load should not need the dated probe blob again (summary already complete).
    (hist_dir / "2026-07-01.json").unlink()
    again = _load_history_entries(tmp_path)
    assert again[0]["summary"]["gemini"]["response_count"] == 1

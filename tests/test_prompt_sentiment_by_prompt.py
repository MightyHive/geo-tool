"""Tests for Gemini qualitative per-prompt sentiment persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from insights_llm import (
    SENTIMENT_SCHEMA_VERSION,
    ByPromptBatchResponse,
    CategorySentimentRow,
    PerPromptSentimentRow,
    PromptSentimentOverviewResponse,
    PromptSentimentResponse,
    _BY_PROMPT_CHUNK_SIZE,
    _BUNDLE_MAX_CHARS,
    _MAX_SERVABLE_BY_PROMPT_MAX_ITEMS,
    _OVERVIEW_BUNDLE_MAX_CHARS,
    _OVERVIEW_SAMPLE_SIZE,
    _chunk_rows,
    _extract_json_text,
    _generate_structured,
    _loads_json_lenient,
    _sample_rows_for_overview,
    _strip_markdown_json_fence,
    assert_sentiment_schema_servable,
    ensure_by_prompt_coverage,
    estimate_sentiment_request_chars,
    generate_prompt_sentiment,
    ground_sentiment_on_mentions,
    load_cached_sentiment,
    save_sentiment_cache,
    sentiment_schema_by_prompt_max_items,
)


def _sentiment(**kwargs: object) -> PromptSentimentResponse:
    base = {
        "overall_sentiment": "Positive",
        "overall_summary": "Favourable overall.",
        "by_category": [
            CategorySentimentRow(category="Skincare", sentiment="Positive", summary="Praised."),
        ],
        "by_prompt": [],
    }
    base.update(kwargs)
    return PromptSentimentResponse.model_validate(base)


def test_ensure_by_prompt_coverage_fills_missing_ids() -> None:
    per_prompt = [
        {"prompt_id": "0:best-moisturizer", "prompt": "best moisturizer", "index": 0},
        {"prompt_id": "1:serum-rec", "prompt": "serum recommendations", "index": 1},
    ]
    sent = _sentiment(
        by_prompt=[
            PerPromptSentimentRow(prompt_id="0:best-moisturizer", sentiment="Positive", summary="Recommended."),
        ]
    )
    filled = ensure_by_prompt_coverage(sent, per_prompt)
    assert len(filled.by_prompt) == 2
    assert filled.by_prompt[0].sentiment == "Positive"
    assert filled.by_prompt[1].prompt_id == "1:serum-rec"
    assert filled.by_prompt[1].sentiment == "Neutral"


def test_ground_sentiment_forces_neutral_when_no_brand_hits() -> None:
    per_prompt = [
        {
            "prompt_id": "0:p",
            "prompt": "best cream",
            "index": 0,
            "mention_scores_gemini": {"brand_signal": 0},
            "mention_scores_openai": {"brand_signal": 0},
        }
    ]
    sent = _sentiment(
        by_prompt=[
            PerPromptSentimentRow(prompt_id="0:p", sentiment="Positive", summary="Loved."),
        ]
    )
    grounded = ground_sentiment_on_mentions(
        sent,
        probed_rows=[{"product_or_service": "Skincare", "prompts": ["best cream"]}],
        per_prompt=per_prompt,
    )
    assert grounded.overall_sentiment == "Neutral"
    assert grounded.by_prompt[0].sentiment == "Neutral"


def test_ground_sentiment_keeps_label_when_brand_mentioned() -> None:
    per_prompt = [
        {
            "prompt_id": "0:p",
            "prompt": "best cream",
            "index": 0,
            "mention_scores_gemini": {"brand_signal": 2},
        }
    ]
    sent = _sentiment(
        by_prompt=[
            PerPromptSentimentRow(prompt_id="0:p", sentiment="Mixed", summary="Some caveats."),
        ]
    )
    grounded = ground_sentiment_on_mentions(
        sent,
        probed_rows=[{"product_or_service": "Skincare", "prompts": ["best cream"]}],
        per_prompt=per_prompt,
    )
    assert grounded.by_prompt[0].sentiment == "Mixed"


def test_sentiment_cache_requires_schema_v2_by_prompt(tmp_path: Path) -> None:
    probe = tmp_path / "prompt_performance_live_probe.json"
    probe.write_text(json.dumps({"live_probe": {"per_prompt": [{"prompt": "x"}]}}), encoding="utf-8")
    sent = _sentiment(
        by_prompt=[
            PerPromptSentimentRow(prompt_id="0:x", sentiment="Neutral", summary="n/a"),
        ]
    )
    save_sentiment_cache(tmp_path, probe, sent)
    cached = load_cached_sentiment(tmp_path, probe)
    assert cached is not None
    assert int(cached.get("schema_version") or 0) == SENTIMENT_SCHEMA_VERSION
    assert isinstance(cached["sentiment"].get("by_prompt"), list)

    # Legacy cache without schema_version / by_prompt is treated as stale.
    legacy = tmp_path / "prompt_performance_sentiment.json"
    legacy.write_text(
        json.dumps(
            {
                "source_mtime": probe.stat().st_mtime,
                "mention_rules_version": 2,
                "sentiment": {
                    "overall_sentiment": "Positive",
                    "overall_summary": "ok",
                    "by_category": [],
                },
            }
        ),
        encoding="utf-8",
    )
    assert load_cached_sentiment(tmp_path, probe) is None


def test_sentiment_schema_has_no_oversized_by_prompt_max_items() -> None:
    """Gemini INVALID_ARGUMENT when nested array maxItems is too large (e.g. 120)."""
    max_items = sentiment_schema_by_prompt_max_items()
    assert max_items is None or max_items <= _MAX_SERVABLE_BY_PROMPT_MAX_ITEMS
    assert_sentiment_schema_servable()


def test_assert_sentiment_schema_servable_rejects_large_max_items() -> None:
    with patch("insights_llm.sentiment_schema_by_prompt_max_items", return_value=120):
        with pytest.raises(ValueError, match="maxItems=120"):
            assert_sentiment_schema_servable()


def test_estimate_sentiment_request_chars_caps_bundle_and_chunks() -> None:
    # Carglass-sized multi-locale: several locales × ~25 prompts.
    per_prompt = []
    for loc in range(5):
        for i in range(25):
            per_prompt.append(
                {
                    "prompt_id": f"{loc}:{i}:glass",
                    "prompt": f"best windscreen repair near me locale {loc} prompt {i} " + ("x" * 200),
                    "index": loc * 25 + i,
                    "_locale_key": f"loc{loc}",
                    "gemini_response": "Carglass is recommended. " * 80,
                    "openai_response": "Consider Carglass or competitors. " * 80,
                    "claude_response": "Carglass appears frequently. " * 80,
                }
            )
    sizing = estimate_sentiment_request_chars([], per_prompt)
    assert sizing["prompt_count"] == 125
    assert sizing["bundle_chars"] <= _BUNDLE_MAX_CHARS
    assert sizing["overview_sample_size"] <= _OVERVIEW_SAMPLE_SIZE
    assert sizing["overview_bundle_chars"] <= _OVERVIEW_BUNDLE_MAX_CHARS
    assert sizing["overview_bundle_chars"] < sizing["bundle_chars"]
    assert sizing["chunk_count"] == (125 + _BY_PROMPT_CHUNK_SIZE - 1) // _BY_PROMPT_CHUNK_SIZE
    assert sizing["chunk_count"] > 1


def test_sample_rows_for_overview_evenly_spaced() -> None:
    rows = [{"i": i} for i in range(280)]
    sample = _sample_rows_for_overview(rows, sample_size=24)
    assert len(sample) == 24
    assert sample[0]["i"] == 0
    assert sample[-1]["i"] == 279


def test_chunk_rows_partitions_evenly() -> None:
    rows = [{"i": i} for i in range(65)]
    chunks = _chunk_rows(rows, 30)
    assert [len(c) for c in chunks] == [30, 30, 5]
    assert [r["i"] for c in chunks for r in c] == list(range(65))


def test_strip_markdown_json_fence() -> None:
    assert _strip_markdown_json_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_markdown_json_fence('{"a": 1}') == '{"a": 1}'


def test_extract_json_text_from_mixed_prose() -> None:
    raw = 'Here you go:\n{"overall_sentiment": "Neutral", "x": 1}\nThanks!'
    assert _extract_json_text(raw).startswith("{")
    assert json.loads(_extract_json_text(raw))["overall_sentiment"] == "Neutral"


def test_loads_json_lenient_fenced_and_trailing() -> None:
    assert _loads_json_lenient('```json\n{"ok": true}\n```') == {"ok": True}
    assert _loads_json_lenient('prefix {"ok": true} trailing notes') == {"ok": True}


def test_loads_json_lenient_raises_on_truncated() -> None:
    with pytest.raises(json.JSONDecodeError):
        _loads_json_lenient('{"overall_sentiment": "Positive", "by_category": [')


def test_generate_structured_retries_on_json_decode_then_succeeds() -> None:
    good = json.dumps(
        {
            "overall_sentiment": "Neutral",
            "overall_summary": "ok",
            "by_category": [],
        }
    )
    bad = '{"overall_sentiment": "Positive", "by_category": ['
    client = MagicMock()
    client.models.generate_content.side_effect = [
        SimpleNamespace(text=bad, parsed=None, candidates=[]),
        SimpleNamespace(text=good, parsed=None, candidates=[]),
    ]
    with (
        patch("insights_llm.build_genai_client", return_value=client),
        patch("insights_llm._model_candidates", return_value=["gemini-test"]),
        patch("insights_llm._generation_config", return_value=MagicMock()),
    ):
        out = _generate_structured("prompt", PromptSentimentOverviewResponse)
    assert isinstance(out, PromptSentimentOverviewResponse)
    assert out.overall_sentiment == "Neutral"
    assert client.models.generate_content.call_count == 2


def test_generate_structured_prefers_resp_parsed() -> None:
    parsed = PromptSentimentOverviewResponse(
        overall_sentiment="Positive",
        overall_summary="Good.",
        by_category=[],
    )
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        text="not-json",
        parsed=parsed,
        candidates=[],
    )
    with (
        patch("insights_llm.build_genai_client", return_value=client),
        patch("insights_llm._model_candidates", return_value=["gemini-test"]),
        patch("insights_llm._generation_config", return_value=MagicMock()),
    ):
        out = _generate_structured("prompt", PromptSentimentOverviewResponse)
    assert out.overall_sentiment == "Positive"
    assert client.models.generate_content.call_count == 1


def test_generate_prompt_sentiment_recovers_when_overview_fails() -> None:
    prompts = [f"prompt {i}" for i in range(5)]
    per_prompt = [
        {
            "prompt_id": f"{i}:p",
            "prompt": prompts[i],
            "index": i,
            "gemini_response": "Samsung phones are solid.",
            "openai_response": "Consider Samsung.",
            "claude_response": "Samsung is mentioned.",
            "mention_scores_gemini": {"brand_signal": 1},
        }
        for i in range(5)
    ]
    probed_rows = [{"product_or_service": "Phones", "prompts": prompts}]

    def _fake_structured(prompt: str, schema: type, *, model: str | None = None):
        if schema is PromptSentimentOverviewResponse:
            raise json.JSONDecodeError("Expecting value", "bad", 0)
        assert schema is ByPromptBatchResponse
        rows = []
        for line in prompt.splitlines():
            if "PROMPT_IDs to cover" in line:
                ids = line.split(":", 1)[1].strip().split(", ")
                rows = [
                    PerPromptSentimentRow(prompt_id=pid.strip(), sentiment="Positive", summary="Named.")
                    for pid in ids
                    if pid.strip()
                ]
        return ByPromptBatchResponse(by_prompt=rows)

    with (
        patch("insights_llm._generate_structured", side_effect=_fake_structured),
        patch("insights_llm._resolve_probed_rows", return_value=probed_rows),
    ):
        result = generate_prompt_sentiment(
            {"per_prompt": per_prompt},
            brand_name="Samsung",
            site_url="https://www.samsung.com",
            pss_rows=probed_rows,
            per_prompt_override=per_prompt,
        )
    assert len(result.by_prompt) == 5
    assert result.overall_sentiment == "Positive"
    assert "synthesised" in result.overall_summary.lower()

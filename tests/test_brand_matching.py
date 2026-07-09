"""Tests for flexible brand mention matching in live probes."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prompt_suggest import (  # noqa: E402
    collect_brand_detected_spellings,
    count_flexible_brand_mentions,
    discover_product_line_aliases,
    mention_scores_for_text,
    recompute_live_probe_mention_scores,
    text_mentions_brand,
)


def test_flexible_brand_matches_hyphen_variant() -> None:
    text = "Try La Roche-Posay Toleriane for sensitive skin."
    assert count_flexible_brand_mentions(text, "La Roche Posay") == 1
    assert collect_brand_detected_spellings(text, "La Roche Posay") == ["La Roche-Posay"]


def test_product_line_aliases_count_as_brand_signal() -> None:
    text = "The Toleriane cleanser and Cicaplast baume are gentle options."
    scores = mention_scores_for_text(
        text,
        brand_name="La Roche Posay",
        brand_site_url="https://www.laroche-posay.us/",
        competitor_urls=["https://www.cerave.com/"],
        competitor_brands=["CeraVe"],
        product_line_aliases=["Toleriane", "Cicaplast"],
    )
    assert scores["brand_name_hits"] == 0
    assert scores["product_line_hits"] == 2
    assert scores["brand_signal"] >= 2


def test_recompute_live_probe_updates_aggregate() -> None:
    live = {
        "brand_name": "La Roche Posay",
        "brand_site_url": "https://www.laroche-posay.us/",
        "competitor_urls": ["https://www.cerave.com/"],
        "competitor_brands": ["CeraVe"],
        "reply_detected_brand_names": ["CeraVe"],
        "per_prompt": [
            {
                "index": 1,
                "prompt": "best moisturizer",
                "gemini_response": "Use a ceramide cream.",
                "openai_response": "La Roche-Posay Toleriane is a strong choice. CeraVe is also good.",
            }
        ],
    }
    out = recompute_live_probe_mention_scores(live)
    openai = out["per_prompt"][0]["mention_scores_openai"]
    assert openai["brand_name_hits"] >= 1
    assert out["aggregate"]["openai"]["brand_hits"] >= 1
    assert "La Roche-Posay" in out["brand_detected_spellings"]
    assert text_mentions_brand(
        out["per_prompt"][0]["openai_response"],
        "La Roche Posay",
        out["brand_match_tokens"],
    )


def test_discover_product_lines_from_paths() -> None:
    blob = "The toleriane cleanser is gentle."
    aliases = discover_product_line_aliases(blob, "La Roche Posay", ["Toleriane", "Cicaplast"])
    assert "Toleriane" in aliases
    assert "Cicaplast" not in aliases

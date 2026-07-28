from __future__ import annotations

from api.prompt_selection import select_prompts_for_probing
from citation_context import infer_citation_brand_context
from prompt_suggest import count_mentions_ci, recommendation_brand_candidates


def test_custom_prompts_and_topics_bypass_generated_prompt_cap() -> None:
    rows = [
        {
            "product_or_service": "Existing topic",
            "prompts": ["g1", "g2", "g3", "g4", "g5", "custom"],
            "custom_prompts": ["custom"],
            "prompt_tags": {"custom": ["priority"]},
        },
        {
            "product_or_service": "User topic",
            "prompts": ["mine"],
            "custom_prompts": ["mine"],
            "prompt_tags": {"mine": ["priority"]},
            "is_custom_topic": True,
        },
    ]

    flat, selected = select_prompts_for_probing(rows)

    assert flat == ["g1", "g2", "g3", "g4", "g5", "custom", "mine"]
    assert selected[-1]["product_or_service"] == "User topic"
    assert selected[-1]["prompt_tags"] == {"mine": ["priority"]}


def test_twenty_five_generated_prompts_plus_custom_prompt_are_selected() -> None:
    rows = [
        {
            "product_or_service": f"Topic {topic}",
            "prompts": [f"topic-{topic}-prompt-{index}" for index in range(5)],
        }
        for topic in range(5)
    ]
    rows[2]["prompts"].append("custom prompt")
    rows[2]["custom_prompts"] = ["custom prompt"]

    flat, selected = select_prompts_for_probing(rows)

    assert len(flat) == 26
    assert flat[-1] == "custom prompt"
    assert sum(len(row["prompts"]) for row in selected) == 26


def test_google_ai_recommendation_lists_detect_all_leading_brands() -> None:
    response = """
**UK Recommendations for Hydrating Cleansers:**
- CeraVe Hydrating Cleanser
- Cetaphil Gentle Skin Cleanser
- Q+A Oat Milk Cream Cleanser
- Avène Tolérance Extremely Gentle Cleanser

**UK Recommendations for Gentle Foaming Cleansers:**
- Clarins Gentle Foaming Cleanser
- Caudalie Vinoclean Gentle Foam Cleanser
- Curél Foaming Facial Wash
- E45 Foaming Cleanser
"""

    detected = set(recommendation_brand_candidates(response))

    assert {"Cetaphil", "Q+A", "Avène", "Clarins", "Caudalie", "Curél", "E45"} <= detected


def test_recommendation_detection_rejects_prose_and_uses_link_entities() -> None:
    response = """
Recommendations:
- For this routine, use a gentle cleanser.
- This is known for hydrating dry skin.
- Where possible, patch test first.
- [La Roche-Posay](https://www.laroche-posay.co.uk) Toleriane Cleanser
"""

    detected = {name.lower() for name in recommendation_brand_candidates(response)}

    assert "la roche-posay" in detected
    assert not {"for", "this", "where", "known"} & detected


def test_mention_count_is_entity_bounded() -> None:
    assert count_mentions_ci("formula format for skin", "for") == 1
    assert count_mentions_ci("CeraVe and CeraVe.", "CeraVe") == 2


def test_product_brand_url_is_competitor_context_not_brand_source() -> None:
    response = """
1. **CeraVe Hydrating Cleanser** - A popular option.
6. **The Body Shop Almond Milk Gentle Face Wash** - Available at The Body Shop stores (https://www.thebodyshop.com).
"""

    context = infer_citation_brand_context(
        response,
        "https://www.thebodyshop.com",
        "thebodyshop.com",
        ["cerave"],
        ["the body shop", "thebodyshop"],
    )

    assert context == {"brand_cited": False, "competitor_cited": True}


def test_citation_context_associates_brand_listed_with_youtube_link() -> None:
    url = "https://www.youtube.com/watch?v=abc123"
    context = infer_citation_brand_context(
        f"- **Euro Car Parts**: installation advice in [this video]({url}).",
        url,
        "youtube.com",
        ["euro car parts", "eurocarparts"],
        ["halfords"],
    )

    assert context["brand_cited"] is True


def test_citation_context_uses_video_title_for_brand_reference() -> None:
    context = infer_citation_brand_context(
        "",
        "https://www.youtube.com/watch?v=abc123",
        "youtube.com",
        ["euro car parts", "eurocarparts"],
        ["halfords"],
        "Euro Car Parts battery fitting guide",
    )

    assert context == {"brand_cited": True, "competitor_cited": False}


def test_citation_context_combines_competitor_title_with_brand_response_context() -> None:
    url = "https://www.youtube.com/watch?v=abc123"
    context = infer_citation_brand_context(
        f"**Euro Car Parts** is demonstrated in [this video]({url}).",
        url,
        "youtube.com",
        ["euro car parts"],
        ["halfords"],
        "Halfords battery guide",
    )

    assert context == {"brand_cited": True, "competitor_cited": True}


def test_citation_title_brand_match_is_token_bounded() -> None:
    context = infer_citation_brand_context(
        "",
        "https://www.youtube.com/watch?v=abc123",
        "youtube.com",
        ["gap"],
        [],
        "Megapixel camera guide",
    )

    assert context["brand_cited"] is False


def test_citation_context_matches_flexible_brand_punctuation() -> None:
    url = "https://www.youtube.com/watch?v=abc123"
    context = infer_citation_brand_context(
        f"[This video]({url}) reviews La Roche-Posay.",
        url,
        "youtube.com",
        ["la roche posay"],
        [],
    )

    assert context["brand_cited"] is True

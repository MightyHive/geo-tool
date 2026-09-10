from __future__ import annotations

from prompt_locales import (
    _deterministic_market_rewrite,
    default_locale_from_market,
    locale_key,
    normalize_prompt_locales,
    regenerate_prompts_for_language,
)


def test_default_locale_is_market_english() -> None:
    loc = default_locale_from_market("United Kingdom", "GB")
    assert loc["key"] == "GB:en"
    assert loc["language"] == "en"
    assert "United Kingdom" in loc["label"]
    assert "English" in loc["label"]


def test_normalize_always_includes_primary_english() -> None:
    locales = normalize_prompt_locales(
        [
            {
                "country": "France",
                "country_code": "FR",
                "language": "fr",
                "language_name": "French",
            }
        ],
        market_country="United Kingdom",
        market_country_code="GB",
    )
    assert locales[0]["key"] == "GB:en"
    assert locales[1]["key"] == "FR:fr"
    assert locale_key("FR", "fr") == "FR:fr"


def test_normalize_keeps_configured_primary_language() -> None:
    locales = normalize_prompt_locales(
        [
            {
                "country": "Italy",
                "country_code": "IT",
                "language": "it",
                "language_name": "Italian",
            }
        ],
        market_country="Italy",
        market_country_code="IT",
    )
    assert locales[0]["key"] == "IT:it"
    assert locales[0]["language_name"] == "Italian"
    assert len(locales) == 1


def test_normalize_dedupes_and_caps(monkeypatch) -> None:
    monkeypatch.setattr("prompt_locales.MAX_PROMPT_LOCALES", 3)
    raw = [
        {"country": "France", "country_code": "FR", "language": "fr"},
        {"country": "France", "country_code": "FR", "language": "fr"},
        {"country": "Germany", "country_code": "DE", "language": "de"},
        {"country": "Spain", "country_code": "ES", "language": "es"},
    ]
    locales = normalize_prompt_locales(
        raw,
        market_country="United Kingdom",
        market_country_code="GB",
    )
    keys = [l["key"] for l in locales]
    assert keys[0] == "GB:en"
    assert len(keys) == 3
    assert len(set(keys)) == 3


def test_regenerate_english_same_market_is_noop() -> None:
    prompts = ["Best brake pads in the UK?", "Where to buy oil filters?"]
    assert (
        regenerate_prompts_for_language(
            prompts,
            target_language="en",
            target_language_name="English",
            market_country="United Kingdom",
            market_country_code="GB",
            source_market_country="United Kingdom",
            source_market_country_code="GB",
        )
        == prompts
    )


def test_regenerate_same_italian_source_is_noop() -> None:
    prompts = ["Quali sono i migliori conti correnti in Italia?"]
    assert (
        regenerate_prompts_for_language(
            prompts,
            target_language="it",
            target_language_name="Italian",
            market_country="Italy",
            market_country_code="IT",
            source_market_country="Italy",
            source_market_country_code="IT",
            source_language="it",
            source_language_name="Italian",
        )
        == prompts
    )


def test_deterministic_market_rewrite_swaps_belgium_for_netherlands() -> None:
    prompts = [
        "Which smartwatches support contactless mobile payments via Bancontact or Google Pay in Belgium?",
    ]
    out = _deterministic_market_rewrite(
        prompts,
        source_country="Belgium",
        source_code="BE",
        target_country="Netherlands",
        target_code="NL",
    )
    assert "Belgium" not in out[0]
    assert "Netherlands" in out[0]
    assert "Bancontact" in out[0]

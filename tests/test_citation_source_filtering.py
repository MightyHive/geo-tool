"""Citations must be platform-referenced sources, not answer recommendations."""

from __future__ import annotations

from backend.prompt_suggest import _merge_citations, extract_citations_from_reply


def test_merge_keeps_api_sources_and_ignores_recommended_text_domains() -> None:
    api = [
        {
            "url": "https://www.nytimes.com/2024/01/skin-care",
            "domain": "nytimes.com",
            "title": "Skin care guide",
        }
    ]
    response = (
        "According to recent reporting, CeraVe is widely recommended. "
        "Also try rival.com and https://competitor.example/shop."
    )

    merged = _merge_citations(api, response, "https://laroche-posay.com")

    domains = {c["domain"] for c in merged}
    assert domains == {"nytimes.com"}
    assert all(c.get("origin") == "api" for c in merged)
    assert "rival.com" not in domains
    assert "competitor.example" not in domains


def test_merge_fallback_extracts_full_urls_not_bare_domains() -> None:
    response = (
        "I recommend Rival (rival.com) for beginners. "
        "See also https://www.dermnetnz.org/topics/eczema for clinical detail."
    )

    merged = _merge_citations([], response, "https://laroche-posay.com")

    domains = {c["domain"] for c in merged}
    assert domains == {"dermnetnz.org"}
    assert all(c.get("origin") == "text" for c in merged)
    assert "rival.com" not in domains


def test_extract_bare_domains_opt_in_only() -> None:
    text = "Try cerave.com or visit https://www.aad.org/public/diseases."
    default = extract_citations_from_reply(text, "https://example.com")
    with_bare = extract_citations_from_reply(
        text,
        "https://example.com",
        include_bare_domains=True,
    )

    assert {c["domain"] for c in default} == {"aad.org"}
    assert {c["domain"] for c in with_bare} == {"cerave.com", "aad.org"}


def test_competitor_api_citation_is_kept() -> None:
    api = [
        {
            "url": "https://www.rival.com/research/study",
            "domain": "rival.com",
            "title": "Rival research",
        }
    ]
    response = "Sources include Rival's published study. Also consider otherbrand.com."

    merged = _merge_citations(api, response, "https://acme.com")

    assert len(merged) == 1
    assert merged[0]["domain"] == "rival.com"
    assert merged[0]["origin"] == "api"

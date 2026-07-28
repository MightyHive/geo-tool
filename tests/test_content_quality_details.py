from __future__ import annotations

import json

from api import geo_services


def test_content_quality_details_include_evidence_and_reddit_presence(tmp_path, monkeypatch) -> None:
    crawl = geo_services.load_crawl_site()
    signals = crawl.compute_page_content_signals(
        """
        <html><body><article>
          <h2>What did our team test?</h2>
          <p>We tested this method with 120 customers and found a 28 percent improvement.</p>
          <h2>How does the method work?</h2>
          <p>The method works by placing a direct answer beneath each descriptive heading.</p>
        </article></body></html>
        """
    )
    audit = {
        "base_url": "https://example.com",
        "pages": [{
            "url": "https://example.com/research",
            "final_url": "https://example.com/research",
            "page_title": "Research",
            "http_status": 200,
            "has_json_ld": True,
            "json_ld_blocks": 1,
            "json_ld_types": ["Article"],
            "same_as": [],
            "content_signals": signals,
        }],
        "summary": {"any_json_ld": True, "unique_same_as_urls": []},
        "brand_visibility": {
            "brand_query": "Example",
            "platforms": [{
                "platform": "Reddit",
                "present": False,
                "status": "No confident result",
                "impact": "Community corroboration",
            }],
        },
    }
    monkeypatch.setattr(geo_services, "load_audit_summary", lambda _audit_dir: audit)
    (tmp_path / "prompt_performance_live_probe.json").write_text(json.dumps({
        "live_probe": {
            "per_prompt": [{
                "citations_gemini": [{
                    "domain": "www.reddit.com",
                    "url": "https://www.reddit.com/r/example/comments/abc/example/",
                }],
            }],
        },
    }))

    details = geo_services.load_content_quality_details(tmp_path)

    structure = {row["key"]: row for row in details["structure_answerability"]}
    assert structure["original_information_gain"]["examples"]
    assert structure["passage_answerability"]["examples"]
    assert structure["content_formatting"]["examples"]
    assert details["schema_entity"]["evidence"][0]["types"] == ["Article"]
    reddit = next(
        row for row in details["brand_visibility_authority"]["rows"]
        if row["platform"] == "Reddit"
    )
    assert reddit["present"] is True
    assert reddit["citation_confirmed"] is True
    assert "Likely yes" in reddit["status"]
    assert sum(component["weight_pct"] for component in details["components"]) == 100
    expected_score = sum(
        component["score"] * component["weight_pct"] / 100
        for component in details["components"]
    )
    assert details["score"] == round(expected_score, 1)


def test_integrated_scores_use_the_same_content_score_and_components(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(geo_services, "load_category_score_details", lambda _audit_dir: {
        "ai_visibility": {"score": 70, "components": [{"key": "technical"}]},
        "technical_setup": {"score": 60, "components": []},
        "content_structure": {"score": 90, "components": [{"key": "legacy"}]},
    })
    monkeypatch.setattr(geo_services, "load_prompt_visibility_metrics", lambda _audit_dir: {"score": 50})
    monkeypatch.setattr(geo_services, "load_technical_display_data", lambda _audit_dir: {})
    monkeypatch.setattr(geo_services, "load_content_quality_details", lambda _audit_dir: {
        "score": 40,
        "components": [{"key": "aligned-content"}],
        "overall_cap": 45,
    })

    result = geo_services.load_integrated_scores(tmp_path)

    assert result["ai_visibility"] == 50
    assert result["technical_setup"] == 70
    assert result["content_structure"] == 40
    assert result["overall"] == 45
    assert result["details"]["content_structure"]["components"] == [{"key": "aligned-content"}]


def test_technical_score_uses_only_crawler_citability_and_platform_readiness(
    monkeypatch, tmp_path
) -> None:
    def component(key: str, score: float) -> dict:
        return {"key": key, "score": score, "weight_pct": 1}

    monkeypatch.setattr(geo_services, "load_category_score_details", lambda _audit_dir: {
        "ai_visibility": {
            "score": 99,
            "components": [
                component("ai_citability", 100),
                component("ai_search_success", 80),
                component("query_coverage_footprint", 60),
                component("platform_readiness", 40),
                component("brand_entity_visibility", 100),
            ],
        },
        "technical_setup": {
            "score": 20,
            "components": [component("ai_crawler_report", 20)],
        },
        "content_structure": {"score": 50, "components": []},
    })
    monkeypatch.setattr(geo_services, "load_prompt_visibility_metrics", lambda _audit_dir: {"score": 50})
    monkeypatch.setattr(geo_services, "load_technical_display_data", lambda _audit_dir: {})
    monkeypatch.setattr(geo_services, "load_content_quality_details", lambda _audit_dir: {
        "score": 50, "components": [], "overall_cap": None,
    })

    result = geo_services.load_integrated_scores(tmp_path)

    assert result["technical_setup"] == 57.5
    components = result["details"]["technical_setup"]["components"]
    assert sum(row["weight_pct"] for row in components) == 100
    assert "brand_entity_visibility" not in {row["key"] for row in components}

from __future__ import annotations

from api import geo_services


def test_ai_search_success_exposes_nine_weighted_criteria() -> None:
    create_report = geo_services.load_create_report()
    audit = {
        "base_url": "https://example.com",
        "pages": [{
            "url": "https://example.com/",
            "final_url": "https://example.com/",
            "page_title": "Example",
            "http_status": 200,
            "has_json_ld": True,
            "json_ld_blocks": 1,
            "json_ld_types": ["Organization"],
            "same_as": [],
            "content_signals": {},
        }],
        "summary": {"any_json_ld": True, "unique_same_as_urls": []},
    }

    create_report.score_audit(audit)

    criteria = audit["_ai_search_success_criteria"]
    assert len(criteria) == 9
    assert sum(row["weight_pct"] for row in criteria) == 100
    assert {row["key"] for row in criteria} == {
        "content_quality",
        "crawl_index",
        "structured_data",
        "snippet_eligibility",
        "page_experience",
        "multimodal",
        "entity_ecosystem",
        "visit_quality",
        "freshness",
    }
    assert all(0 <= row["score"] <= 100 for row in criteria)

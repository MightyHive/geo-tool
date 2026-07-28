from __future__ import annotations

import json

from api.geo_services import enrich_report_brand_visibility_from_citations


def test_brand_citation_fills_empty_reddit_visibility_row(tmp_path) -> None:
    probe = {
        "live_probe": {
            "brand_name": "Example",
            "brand_site_url": "https://example.com",
            "competitor_urls": [],
            "competitor_brands": [],
            "per_prompt": [
                {
                    "prompt": "Which brand is best?",
                    "gemini_response": "Example is recommended.",
                    "citations_gemini": [
                        {
                            "domain": "reddit.com",
                            "url": "https://www.reddit.com/r/example/comments/abc/post/",
                            "brand_cited": True,
                        }
                    ],
                }
            ],
        }
    }
    (tmp_path / "prompt_performance_live_probe.json").write_text(json.dumps(probe))
    report_html = (
        "<table><tbody>"
        "<tr><td><strong>Reddit</strong></td>"
        '<td><span class="score-pill pill-red">No / unclear</span></td>'
        "<td>No Reddit search hits.</td><td>Medium impact.</td></tr>"
        "</tbody></table>"
    )

    result = enrich_report_brand_visibility_from_citations(report_html, tmp_path)

    assert "pill-green" in result
    assert "1 brand-associated AI citation found" in result
    assert "Open citation" in result


def test_unverified_citation_adds_citation_signal_without_claiming_brand_presence(tmp_path) -> None:
    probe = {
        "live_probe": {
            "brand_name": "Example",
            "brand_site_url": "https://example.com",
            "per_prompt": [
                {
                    "openai_response": "A competitor is recommended.",
                    "citations_openai": [
                        {
                            "domain": "youtube.com",
                            "url": "https://www.youtube.com/watch?v=abcdefghijk",
                            "brand_cited": False,
                        }
                    ],
                }
            ],
        }
    }
    (tmp_path / "prompt_performance_live_probe.json").write_text(json.dumps(probe))
    report_html = (
        "<table><tbody>"
        "<tr><td><strong>YouTube</strong></td>"
        '<td><span class="score-pill pill-red">No / unclear</span></td>'
        "<td>No channel found.</td><td>Medium impact.</td></tr>"
        "</tbody></table>"
    )

    result = enrich_report_brand_visibility_from_citations(report_html, tmp_path)

    assert "Citation signal" in result
    assert "source-level brand mention not verified" in result
    assert "Likely yes" not in result


def test_aggregate_reddit_citation_without_context_is_not_a_brand_mention(tmp_path) -> None:
    probe = {
        "live_probe": {
            "top_cited_urls": [{
                "domain": "reddit.com",
                "url": "https://www.reddit.com/r/example/comments/abc/post/",
                "frequency": 2,
            }],
        }
    }
    (tmp_path / "prompt_performance_live_probe.json").write_text(json.dumps(probe))
    report_html = (
        "<table><tbody>"
        "<tr><td><strong>Reddit</strong></td>"
        '<td><span class="score-pill pill-red">No / unclear</span></td>'
        "<td>No Reddit search hits.</td><td>Medium impact.</td></tr>"
        "</tbody></table>"
    )

    result = enrich_report_brand_visibility_from_citations(report_html, tmp_path)

    assert "pill-yellow" in result
    assert "2 AI citations found; source-level brand mention not verified" in result

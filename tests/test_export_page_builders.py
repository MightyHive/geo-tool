"""Coverage for refreshed PDF/HTML section exporters."""

from __future__ import annotations

import json
from pathlib import Path

from api.export_builders import (
    FULL_REPORT_SECTIONS,
    build_all_pages_export,
    build_competitor_visibility_export,
    build_recommendations_export,
    build_section_body,
    build_summary_export,
)


def _write_minimal_audit(tmp_path: Path) -> Path:
    (tmp_path / "audit_summary.json").write_text(
        json.dumps({
            "brand_name": "Acme",
            "overall_score": 64.0,
            "base_url": "https://acme.example",
            "pages": [],
        }),
        encoding="utf-8",
    )
    (tmp_path / "executive_summary.json").write_text(
        json.dumps({
            "schema_version": 4,
            "paragraph_html": "Acme scores <strong>64</strong> out of 100 overall.",
            "key_findings": ["Visibility is strongest on Gemini."],
        }),
        encoding="utf-8",
    )
    # Website-backed competitor so SOV matches UI brandVisibilityRows rules.
    (tmp_path / "onboarding_context.json").write_text(
        json.dumps({
            "brand_name_used": "Acme",
            "brand_website_used": "https://acme.example",
            "competitors_detail": [
                {"competitor_brand": "RivalCo", "competitor_website": "https://rivalco.example"},
            ],
        }),
        encoding="utf-8",
    )
    (tmp_path / "prompt_performance_live_probe.json").write_text(
        json.dumps({
            "live_probe": {
                "brand_name": "Acme",
                "brand_match_tokens": ["Acme"],
                "per_prompt": [
                    {
                        "prompt": "best brand for widgets?",
                        "gemini_response": "Acme is the best. RivalCo is also good.",
                        "mention_scores_gemini": {
                            "brand_signal": 1,
                            "competitor_detail": {"RivalCo": 1, "noise-category": 9},
                        },
                        "citations_gemini": [
                            {
                                "url": "https://www.reddit.com/r/widgets/comments/abc/post/",
                                "domain": "reddit.com",
                                "title": "Widget thread",
                                "brand_cited": True,
                            },
                            {
                                "url": "https://www.youtube.com/watch?v=abcdefghijk",
                                "domain": "youtube.com",
                                "title": "Widget review",
                            },
                        ],
                    }
                ],
                "top_cited_sites": [
                    {
                        "domain": "example.com",
                        "count": 3,
                        "platforms": ["gemini"],
                        "brand_mentioned": True,
                        "competitor_names": ["RivalCo"],
                    },
                    {
                        "domain": "amazon.com",
                        "count": 5,
                        "platforms": ["gemini"],
                        "brand_mentioned": False,
                    },
                ],
                "top_cited_urls": [
                    {
                        "url": "https://example.com/a",
                        "domain": "example.com",
                        "frequency": 2,
                        "title": "Guide A",
                        "content_type": "Article",
                        "channel_type": "Publisher",
                    },
                    {
                        "url": "https://example.com/",
                        "domain": "example.com",
                        "frequency": 1,
                        "title": "Homepage",
                    },
                ],
                "aggregate": {},
            }
        }),
        encoding="utf-8",
    )
    return tmp_path


def test_summary_export_uses_per_platform_visibility(tmp_path: Path, monkeypatch) -> None:
    _write_minimal_audit(tmp_path)
    monkeypatch.setattr(
        "api.geo_services.load_integrated_scores",
        lambda _audit_dir: {
            "overall": 64.2,
            "ai_visibility": 58.0,
            "technical_setup": 71.0,
            "content_structure": 66.0,
            "prompt_metrics": {
                "visibility_pct": 42.0,
                "sov_pct": 35.0,
                "per_platform": {
                    "gemini": {"visibility_pct": 50.0, "sov_pct": 40.0, "response_count": 4},
                    "openai": {"visibility_pct": 25.0, "sov_pct": 20.0, "response_count": 4},
                },
            },
            "details": {
                "technical_setup": {
                    "components": [
                        {
                            "key": "ai_crawler_report",
                            "title": "Crawler access",
                            "score": 80,
                            "weight_pct": 25,
                            "strengths": ["GPTBot is allowed."],
                            "improvements": [],
                            "finding_summary": "Crawlers look healthy.",
                        },
                        {
                            "key": "ai_citability",
                            "title": "Citability",
                            "score": 40,
                            "weight_pct": 25,
                            "strengths": [],
                            "improvements": ["Add answer-ready passages."],
                            "finding_summary": "Citability needs work.",
                        },
                    ]
                },
                "content_structure": {
                    "components": [
                        {
                            "key": "eeat",
                            "title": "E-E-A-T Signals",
                            "score": 70,
                            "weight_pct": 35,
                            "strengths": ["Author bios present."],
                            "improvements": [],
                            "finding_summary": "E-E-A-T is solid.",
                        },
                        {
                            "key": "schema_entity_markup",
                            "title": "Schema & entity markup",
                            "score": 30,
                            "weight_pct": 15,
                            "strengths": [],
                            "improvements": ["Add Organization JSON-LD."],
                            "finding_summary": "Schema is thin.",
                        },
                    ]
                },
            },
        },
    )
    monkeypatch.setattr(
        "api.geo_services.load_audit_summary",
        lambda _audit_dir: {"brand_name": "Acme", "overall_score": 64.0},
    )
    html = build_summary_export(tmp_path)
    assert "Brand visibility by AI platform" in html
    assert "Gemini" in html
    assert "ChatGPT" in html
    assert "mentioned in 42% of analysed responses" in html
    assert "Citability needs work" in html or "needs work" in html
    assert "Schema" in html


def test_competitor_visibility_includes_visibility_and_sov(tmp_path: Path, monkeypatch) -> None:
    _write_minimal_audit(tmp_path)

    def fake_ctx(_audit_dir):
        return {
            "brand_name": "Acme",
            "brand_site_url": "https://acme.example",
            "competitors": [
                {"competitor_brand": "RivalCo", "competitor_website": "https://rivalco.example"},
            ],
            "live_probe": json.loads(
                (tmp_path / "prompt_performance_live_probe.json").read_text(encoding="utf-8")
            )["live_probe"],
        }

    monkeypatch.setattr("api.prompt_performance._build_context_response", fake_ctx)
    html = build_competitor_visibility_export(tmp_path)
    assert ">Overall<" not in html
    assert ">Visibility<" in html
    assert ">SOV<" in html
    assert "Acme" in html
    assert "RivalCo" in html
    assert "noise-category" not in html
    assert "60% × visibility" not in html
    assert "brand signal hits" in html
    # Raw SOV: Acme 1 + RivalCo 1 = 50% each (noise excluded)
    assert "50.0%" in html


def test_competitor_visibility_excludes_vendor_domains_from_sov(tmp_path: Path, monkeypatch) -> None:
    """Regression: PDF SOV must match UI brandVisibilityRows (vendors excluded from denominator)."""
    from api.export_builders import SECTION_BUILDERS, build_section_body
    from api.geo_services import accumulate_brand_visibility_rows

    _write_minimal_audit(tmp_path)
    probe = json.loads((tmp_path / "prompt_performance_live_probe.json").read_text(encoding="utf-8"))
    scores = probe["live_probe"]["per_prompt"][0]["mention_scores_gemini"]
    # Vendor + noise must not dilute SOV the way the pre-fix PDF path did.
    scores["competitor_detail"] = {
        "RivalCo": 1,
        "amazon.com": 5,
        "noise-category": 9,
    }
    (tmp_path / "prompt_performance_live_probe.json").write_text(json.dumps(probe), encoding="utf-8")

    ctx = {
        "brand_name": "Acme",
        "brand_site_url": "https://acme.example",
        "competitors": [
            {"competitor_brand": "RivalCo", "competitor_website": "https://rivalco.example"},
        ],
        "live_probe": probe["live_probe"],
    }
    monkeypatch.setattr("api.prompt_performance._build_context_response", lambda _d: ctx)

    rows = accumulate_brand_visibility_rows(ctx, tmp_path)
    by_name = {row["name"]: row for row in rows}
    assert "amazon.com" not in by_name
    assert "Amazon" not in by_name
    assert abs(by_name["Acme"]["sov"] - 50.0) < 1e-6
    assert abs(by_name["RivalCo"]["sov"] - 50.0) < 1e-6
    assert abs(by_name["Acme"]["visibility"] - 100.0) < 1e-6

    # Section download for competitor-visibility must use the fixed builder.
    _label, html = build_section_body(tmp_path, "competitor-visibility")
    assert "Competitor visibility" in _label or "visibility" in html.lower()
    assert "amazon" not in html.lower()
    assert "50.0%" in html
    # Would have been ~14.3% if amazon.com hits were wrongly included.
    assert "14.3%" not in html
    # Legacy section id still resolves to the same builder.
    _legacy_label, legacy_html = build_section_body(tmp_path, "competitor-performance")
    assert "50.0%" in legacy_html
    assert "amazon" not in legacy_html.lower()
    assert SECTION_BUILDERS.get("competitor-performance") is not None


def test_competitor_visibility_export_route_uses_page_builder() -> None:
    from api.export_builders import SECTION_BUILDERS, build_competitor_visibility_export as route_fn
    from api.export_page_builders import build_competitor_visibility_export as page_fn

    assert SECTION_BUILDERS["competitor-visibility"] is not None
    assert SECTION_BUILDERS["competitor-performance"] is not None
    # Route wrapper delegates to the page builder module.
    assert route_fn.__module__ == "api.export_builders"
    assert page_fn.__module__ == "api.export_page_builders"


def test_ai_visibility_overview_covers_live_ui_sections(tmp_path: Path, monkeypatch) -> None:
    _write_minimal_audit(tmp_path)
    monkeypatch.setattr(
        "api.geo_services.load_integrated_scores",
        lambda _audit_dir: {
            "ai_visibility": 55.0,
            "prompt_metrics": {
                "visibility_pct": 100.0,
                "sov_pct": 50.0,
                "score": 55.0,
                "per_platform": {
                    "gemini": {
                        "visibility_pct": 100.0,
                        "sov_pct": 50.0,
                        "response_count": 1,
                    },
                },
            },
        },
    )
    monkeypatch.setattr(
        "api.prompt_performance._build_context_response",
        lambda _audit_dir: {
            "brand_name": "Acme",
            "brand_site_url": "https://acme.example",
            "competitors": [
                {"competitor_brand": "RivalCo", "competitor_website": "https://rivalco.example"},
            ],
            "live_probe": json.loads(
                (tmp_path / "prompt_performance_live_probe.json").read_text(encoding="utf-8")
            )["live_probe"],
            "locale_spread": [{"label": "UK / English", "brand_share_pct": 50.0}],
        },
    )
    from api.export_builders import build_section_body

    _label, html = build_section_body(tmp_path, "ai-visibility-overview")
    for heading in (
        "AI Visibility Score",
        "Visibility",
        "SOV",
        "Position",
        "Sentiment",
        "Brand visibility by platform",
        "Visibility over time",
        "Brand &amp; competitor visibility",
        "Brand &amp; competitor visibility over time",
        "Top cited domains",
        "Citation Frequency Over Time",
        "Prompt visibility breakdown",
    ):
        assert heading in html, f"missing section: {heading}"
    assert "Market / language spread" not in html
    assert "RivalCo" in html
    assert "noise-category" not in html
    assert "50.0%" in html
    assert "UK / English" not in html


def test_recommendations_export_shows_all_three_tabs(tmp_path: Path, monkeypatch) -> None:
    _write_minimal_audit(tmp_path)
    monkeypatch.setattr(
        "api.geo_services.load_integrated_scores",
        lambda _audit_dir: {
            "prompt_metrics": {"visibility_pct": 20.0, "per_platform": {}},
            "details": {"technical_setup": {"components": []}},
            "crawler_access": {"improvements": ["Allow GPTBot"], "rows": []},
            "platform_readiness": [{"key": "openai", "name": "ChatGPT", "score": 35, "gap": "GPTBot blocked"}],
            "content_quality_details": {
                "components": [
                    {
                        "key": "eeat",
                        "title": "E-E-A-T Signals",
                        "score": 30,
                        "finding_summary": "Thin expertise signals.",
                        "improvements": ["Add author credentials."],
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        "api.prompt_performance._build_context_response",
        lambda _audit_dir: {
            "brand_name": "Acme",
            "live_probe": json.loads(
                (tmp_path / "prompt_performance_live_probe.json").read_text(encoding="utf-8")
            )["live_probe"],
            "use_pss": False,
            "pss_rows": [],
        },
    )
    html = build_recommendations_export(tmp_path)
    assert "AI Visibility" in html
    assert "Technical Setup" in html
    assert "Content Quality" in html


def test_full_report_contains_new_sections_not_workshop(tmp_path: Path, monkeypatch) -> None:
    _write_minimal_audit(tmp_path)
    monkeypatch.setattr(
        "api.geo_services.load_integrated_scores",
        lambda _audit_dir: {
            "overall": 50,
            "ai_visibility": 40,
            "technical_setup": 55,
            "content_structure": 60,
            "prompt_metrics": {"visibility_pct": 40, "per_platform": {}},
            "details": {"technical_setup": {"components": []}, "content_structure": {"components": []}},
            "crawler_access": {"rows": []},
            "platform_readiness": [],
            "content_quality_details": {"score": 60, "components": [], "eeat": [], "structure_answerability": []},
        },
    )
    monkeypatch.setattr(
        "api.geo_services.load_audit_summary",
        lambda _audit_dir: {"brand_name": "Acme"},
    )
    monkeypatch.setattr(
        "api.prompt_performance._build_context_response",
        lambda _audit_dir: {
            "brand_name": "Acme",
            "live_probe": json.loads(
                (tmp_path / "prompt_performance_live_probe.json").read_text(encoding="utf-8")
            )["live_probe"],
        },
    )
    html = build_all_pages_export(tmp_path)
    assert "data-export-section='reddit-citations'" in html
    assert "data-export-section='youtube-citations'" in html
    assert "data-export-section='platform-readiness'" in html
    assert "data-export-section='crawler-access'" in html
    assert "data-export-section='samples'" not in html
    assert "data-export-section='sample-scripts'" not in html
    ids = [sid for sid, _ in FULL_REPORT_SECTIONS]
    assert "samples" not in ids
    assert "sample-scripts" not in ids


def test_reddit_and_citations_builders_emit_tables(tmp_path: Path, monkeypatch) -> None:
    _write_minimal_audit(tmp_path)
    monkeypatch.setattr(
        "api.prompt_performance._build_context_response",
        lambda _audit_dir: {
            "brand_name": "Acme",
            "brand_site_url": "https://acme.example",
            "competitors": [
                {"competitor_brand": "RivalCo", "competitor_website": "https://rivalco.example"},
            ],
            "live_probe": json.loads(
                (tmp_path / "prompt_performance_live_probe.json").read_text(encoding="utf-8")
            )["live_probe"],
        },
    )
    _label, citations = build_section_body(tmp_path, "citations")
    assert "Top Domains" in citations
    assert "Top URLs" in citations
    assert "example.com" in citations
    assert "Brand mentioned" in citations
    assert "Content type" in citations
    assert "Channel type" in citations
    assert "Guide A" in citations
    # Vendor domain excluded; homepage URL without path excluded
    assert "amazon.com" not in citations
    assert "Homepage" not in citations

    _label, reddit = build_section_body(tmp_path, "reddit-citations")
    assert "Reddit" in reddit
    assert "widgets" in reddit or "reddit.com" in reddit or "Post" in reddit or "export-empty" not in reddit
    # Legacy alias still works
    _legacy_label, legacy_reddit = build_section_body(tmp_path, "reddit-insights")
    assert "Reddit" in legacy_reddit


def test_youtube_insights_export_unpacks_fetch_tuple(tmp_path: Path, monkeypatch) -> None:
    """Regression: _fetch_video_details returns (map, api_error); export must not .get on the tuple."""
    _write_minimal_audit(tmp_path)
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")

    def fake_fetch(video_ids, brand_name, api_key):
        assert video_ids
        return (
            {
                "abcdefghijk": {
                    "yt_title": "Acme widget review",
                    "channel_title": "Acme Channel",
                    "view_count": 12345,
                    "enriched": True,
                }
            },
            None,
        )

    monkeypatch.setattr("api.youtube_insights._fetch_video_details", fake_fetch)

    _label, html = build_section_body(tmp_path, "youtube-citations")
    assert "YouTube" in html
    assert "Acme widget review" in html
    assert "12,345" in html

    # Legacy alias uses the same builder
    _legacy_label, legacy_html = build_section_body(tmp_path, "youtube-insights")
    assert "Acme widget review" in legacy_html

"""Tests for competitor CQ Gemini enqueue and finding attribution filters."""

from __future__ import annotations

import json
from pathlib import Path

from api import content_quality_jobs, geo_services
from content_quality_llm import (
    apply_gemini_to_content_components,
    competitor_sample_cap,
    save_content_quality_gemini,
)


def test_competitor_sample_cap_defaults_to_brand_cap(monkeypatch) -> None:
    monkeypatch.delenv("CONTENT_QUALITY_SAMPLE_CAP", raising=False)
    monkeypatch.delenv("CONTENT_QUALITY_COMPETITOR_SAMPLE_CAP", raising=False)
    assert competitor_sample_cap() == 20

    monkeypatch.setenv("CONTENT_QUALITY_COMPETITOR_SAMPLE_CAP", "10")
    assert competitor_sample_cap() == 10

    monkeypatch.setenv("CONTENT_QUALITY_COMPETITOR_SAMPLE_CAP", "99")
    assert competitor_sample_cap() == 20


def test_filter_strips_brand_misattributed_findings() -> None:
    components = [
        {
            "key": "eeat",
            "title": "E-E-A-T Signals",
            "score": 70.0,
            "weight_pct": 35.0,
            "finding_summary": "Samsung shows strong expertise across product pages.",
            "evidence_example": "https://www.samsung.com/example",
        },
        {
            "key": "schema_entity_markup",
            "title": "Schema & Entity Markup",
            "score": 55.0,
            "weight_pct": 15.0,
            "finding_summary": "Apple schema coverage looks solid on product pages.",
            "evidence_example": "https://www.apple.com/iphone/",
        },
    ]
    brand_components = [
        {
            "key": "eeat",
            "title": "E-E-A-T Signals",
            "score": 80.0,
            "weight_pct": 35.0,
            "finding_summary": "Samsung shows strong expertise across product pages.",
            "evidence_example": "https://www.samsung.com/example",
        }
    ]
    filtered = geo_services.filter_competitor_verified_components(
        components,
        brand_name="Samsung",
        competitor_name="Apple",
        brand_components=brand_components,
        primary_host="samsung.com",
        competitor_host="apple.com",
    )
    by_key = {c["key"]: c for c in filtered}
    assert by_key["eeat"]["verified"] is False
    assert by_key["eeat"]["finding_summary"] == ""
    assert by_key["schema_entity_markup"]["verified"] is True
    assert "Apple" in by_key["schema_entity_markup"]["finding_summary"]


def test_filter_score_only_when_no_verified_findings() -> None:
    components = [
        {
            "key": "crawler_access",
            "title": "Crawler access",
            "score": 90.0,
            "weight_pct": 25.0,
            "finding_summary": "No specific finding was recorded.",
            "evidence_example": "",
        }
    ]
    filtered = geo_services.filter_competitor_verified_components(
        components,
        brand_name="Samsung",
        competitor_name="Apple",
    )
    assert filtered[0]["verified"] is False
    assert filtered[0]["finding_summary"] == ""
    assert filtered[0]["score"] == 90.0


def test_run_content_quality_analysis_soft_skips_no_pages(
    tmp_path: Path, monkeypatch
) -> None:
    """Empty competitor crawl must clear pending with skipped (exit-friendly), not error."""
    monkeypatch.setattr(content_quality_jobs.geo, "audit_dir_api_rel", lambda path: str(path.name))
    comp = tmp_path / "competitors" / "www.touringglass.be_b645cdd5baa2"
    comp.mkdir(parents=True)
    # No base_url and no pages → sampler returns [] → no_pages soft-fail.
    (comp / "audit_summary.json").write_text(
        json.dumps({"audit_label": "competitor_1", "pages": []}),
        encoding="utf-8",
    )
    request_id = "req-soft-skip"
    (comp / content_quality_jobs.CONTENT_QUALITY_PENDING_FILE).write_text(
        json.dumps({"request_id": request_id, "status": "queued"}),
        encoding="utf-8",
    )

    outcome = content_quality_jobs.run_content_quality_analysis(comp, request_id=request_id)
    assert outcome["content_quality"] == "skipped"
    assert outcome["reason"] == "no_pages"
    pending = json.loads((comp / content_quality_jobs.CONTENT_QUALITY_PENDING_FILE).read_text())
    assert pending["status"] == "skipped"
    assert pending.get("error") is None


def test_enqueue_competitor_content_quality_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CONTENT_QUALITY_FORCE_LOCAL", "1")
    monkeypatch.setattr(content_quality_jobs.geo, "audit_dir_api_rel", lambda path: str(path.name))
    monkeypatch.setattr(
        content_quality_jobs,
        "run_content_quality_analysis",
        lambda *_args, **_kwargs: {"content_quality": "done", "pages_analyzed": "1"},
    )

    (tmp_path / "onboarding_context.json").write_text(
        json.dumps(
            {
                "brand_name_used": "Samsung",
                "competitors_detail": [
                    {
                        "competitor_brand": "Apple",
                        "competitor_website": "https://www.apple.com",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    comp = tmp_path / "competitors" / "www.apple.com"
    comp.mkdir(parents=True)
    (comp / "audit_summary.json").write_text(
        json.dumps({"base_url": "https://www.apple.com", "pages": [], "audit_label": "competitor_1"}),
        encoding="utf-8",
    )

    results = content_quality_jobs.enqueue_competitor_content_quality_jobs(tmp_path)
    assert len(results) == 1
    assert results[0]["status"] == "queued"
    assert (comp / content_quality_jobs.CONTENT_QUALITY_PENDING_FILE).is_file()
    ctx = json.loads((comp / content_quality_jobs.CONTENT_QUALITY_JOB_CONTEXT_FILE).read_text())
    assert ctx["brand_name"] == "Apple"
    assert "apple.com" in ctx["site_url"]


def test_apply_gemini_to_content_components_replaces_eeat() -> None:
    components = [
        {
            "key": "eeat",
            "title": "E-E-A-T Signals",
            "score": 40.0,
            "weight_pct": 35.0,
            "finding_summary": "crawl",
        },
        {
            "key": "structure_answerability",
            "title": "Content Structure & Answerability",
            "score": 50.0,
            "weight_pct": 40.0,
            "finding_summary": "crawl structure",
        },
        {
            "key": "schema_entity_markup",
            "title": "Schema & Entity Markup",
            "score": 60.0,
            "weight_pct": 15.0,
            "finding_summary": "crawl schema",
        },
        {
            "key": "brand_visibility_authority",
            "title": "Brand Visibility & Authority",
            "score": 70.0,
            "weight_pct": 10.0,
            "finding_summary": "crawl brand",
        },
    ]
    gemini = {
        "pages_analyzed": 2,
        "merge_rule": "replace_eeat_and_answerability",
        "aggregate": {
            "eeat": 81.0,
            "experience": 80.0,
            "expertise": 82.0,
            "authoritativeness": 80.0,
            "trust": 82.0,
            "original_information_gain": 70.0,
            "passage_answerability": 74.0,
            "finding_summary": "Gemini E-E-A-T for competitor.",
        },
        "pages": [],
    }
    merged, score = apply_gemini_to_content_components(components, gemini)
    by_key = {c["key"]: c for c in merged}
    assert by_key["eeat"]["score"] == 81.0
    assert by_key["eeat"]["scoring_source"] == "gemini"
    assert by_key["schema_entity_markup"]["scoring_source"] == "crawl_heuristic"
    assert by_key["structure_answerability"]["scoring_source"] == "gemini"
    assert score is not None
    assert score > 50


def test_load_competitive_comparison_merges_competitor_gemini(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "audit_summary.json").write_text(
        json.dumps({"base_url": "https://www.samsung.com", "audit_label": "primary"}),
        encoding="utf-8",
    )
    (tmp_path / "onboarding_context.json").write_text(
        json.dumps(
            {
                "brand_name_used": "Samsung",
                "competitors_detail": [
                    {
                        "competitor_brand": "Apple",
                        "competitor_website": "https://www.apple.com",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "comparison.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "base_url": "https://www.samsung.com",
                        "audit_label": "primary",
                        "output_dir": str(tmp_path),
                    },
                    {
                        "base_url": "https://www.apple.com",
                        "audit_label": "competitor_1",
                        "output_dir": str(tmp_path / "competitors" / "www.apple.com"),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    comp = tmp_path / "competitors" / "www.apple.com"
    comp.mkdir(parents=True)
    summary = {
        "base_url": "https://www.apple.com",
        "audit_label": "competitor_1",
        "pages": [
            {
                "url": "https://www.apple.com/",
                "final_url": "https://www.apple.com/",
                "http_status": 200,
                "page_title": "Apple",
            }
        ],
        "robots_txt": {"exists": True},
        "llms_txt": {"exists": False},
        "summary": {},
    }
    (comp / "audit_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    save_content_quality_gemini(
        comp,
        {
            "sample_cap": 20,
            "pages_analyzed": 1,
            "aggregate": {
                "experience": 88.0,
                "expertise": 90.0,
                "authoritativeness": 86.0,
                "trust": 92.0,
                "eeat": 89.0,
                "original_information_gain": 70.0,
                "passage_answerability": 75.0,
                "languages_seen": ["en"],
                "finding_summary": "Gemini assessed Apple pages.",
            },
            "pages": [
                {
                    "url": "https://www.apple.com/",
                    "title": "Apple",
                    "source_language": "en",
                    "scores": {
                        "experience": 88.0,
                        "expertise": 90.0,
                        "authoritativeness": 86.0,
                        "trust": 92.0,
                        "original_information_gain": 70.0,
                        "passage_answerability": 75.0,
                    },
                    "findings": {
                        "experience": {
                            "score": 88.0,
                            "summary": "Strong product storytelling.",
                            "evidence": ["Built for Apple Intelligence."],
                        }
                    },
                }
            ],
        },
    )

    monkeypatch.setattr(
        geo_services,
        "load_integrated_scores",
        lambda _audit_dir: {
            "ai_visibility": 50.0,
            "technical_setup": 60.0,
            "content_structure": 55.0,
            "prompt_metrics": None,
            "details": {"technical_setup": {"components": []}, "content_structure": {"components": []}},
        },
    )

    payload = geo_services.load_competitive_comparison(tmp_path)
    peer = next(row for row in payload["rows"] if not row["is_primary"])
    content_components = peer["content_quality_rationale"]["components"]
    eeat = next(c for c in content_components if c.get("key") == "eeat")
    assert eeat["score"] == 89.0
    assert "Gemini" in str(eeat.get("finding_summary") or "")
    assert peer["content_quality_rationale"].get("gemini_overlay", {}).get("available") is True

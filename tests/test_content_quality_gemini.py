"""Tests for Gemini content-quality sampling, persistence, merge, and job enqueue."""

from __future__ import annotations

import json
import time
from pathlib import Path

from api import content_quality_jobs
from content_quality_llm import (
    CONTENT_QUALITY_GEMINI_FILE,
    CONTENT_QUALITY_SCHEMA_VERSION,
    is_content_quality_soft_fail,
    load_cached_content_quality_gemini,
    merge_gemini_into_content_quality,
    sample_pages_for_content_quality,
    save_content_quality_gemini,
)


def _page(url: str, *, editorial: bool = False, words: int = 200, title: str = "") -> dict:
    return {
        "url": url,
        "final_url": url,
        "page_title": title or url,
        "http_status": 200,
        "content_signals": {
            "has_editorial_content": editorial,
            "visible_words": words,
            "meaningful_sentence_n": 4 if editorial else 0,
        },
    }


def test_sample_pages_prefers_homepage_and_editorial() -> None:
    audit = {
        "base_url": "https://example.com",
        "pages": [
            _page("https://example.com/deep/a/b/c", editorial=False, words=50),
            _page("https://example.com/", editorial=True, words=400, title="Home"),
            _page("https://example.com/guide", editorial=True, words=800, title="Guide"),
            _page("https://example.com/cart", editorial=False, words=40),
        ],
    }
    selected = sample_pages_for_content_quality(audit, cap=2)
    urls = [str(p.get("final_url")) for p in selected]
    assert urls[0] == "https://example.com/"
    assert "https://example.com/guide" in urls


def test_sample_pages_respects_cap_and_skips_non_200() -> None:
    audit = {
        "base_url": "https://example.com",
        "pages": [
            _page("https://example.com/"),
            {**_page("https://example.com/x"), "http_status": 404},
            _page("https://example.com/a", editorial=True),
            _page("https://example.com/b", editorial=True),
            _page("https://example.com/c", editorial=True),
        ],
    }
    selected = sample_pages_for_content_quality(audit, cap=3)
    assert len(selected) == 3
    assert all(int(p.get("http_status") or 0) == 200 for p in selected)


def test_sample_pages_competitor_audit_dir_falls_back_to_non_200_urls(tmp_path: Path) -> None:
    """Competitor crawls often store homepage URLs with null/403 status — still sample them."""
    audit_dir = tmp_path / "competitors" / "www.touringglass.be_b645cdd5baa2"
    audit_dir.mkdir(parents=True)
    audit = {
        "base_url": "https://www.touringglass.be",
        "audit_label": "competitor_1",
        "pages": [
            {
                "url": "https://www.touringglass.be/",
                "http_status": None,
                "fetch_error": "The read operation timed out",
                "json_ld_saved": None,
                "og_images_saved": [],
            },
            {
                "url": "https://www.touringglass.be/services",
                "http_status": 403,
                "fetch_error": "HTTP Error 403: Forbidden",
            },
            {
                "url": "https://www.touringglass.be/about",
                "final_url": "https://www.touringglass.be/about",
                "http_status": 404,
            },
        ],
    }
    (audit_dir / "audit_summary.json").write_text(json.dumps(audit), encoding="utf-8")
    selected = sample_pages_for_content_quality(audit, audit_dir=audit_dir, cap=5)
    urls = [str(p.get("final_url") or p.get("url")) for p in selected]
    assert urls[0] == "https://www.touringglass.be/"
    assert "https://www.touringglass.be/services" in urls
    assert len(selected) == 3


def test_sample_pages_competitor_empty_pages_uses_base_url_homepage() -> None:
    audit = {
        "base_url": "https://www.touringglass.be",
        "audit_label": "competitor_1",
        "pages": [],
    }
    selected = sample_pages_for_content_quality(audit, cap=5)
    assert len(selected) == 1
    assert str(selected[0].get("url")) == "https://www.touringglass.be/"


def test_is_content_quality_soft_fail_for_no_pages() -> None:
    assert is_content_quality_soft_fail("no_pages") is True
    assert is_content_quality_soft_fail("no_page_results") is True
    assert is_content_quality_soft_fail("https://x/: no text (timeout)") is True
    assert is_content_quality_soft_fail("gemini_api_unavailable") is False
    assert is_content_quality_soft_fail(None) is False


def test_save_and_load_content_quality_cache_schema(tmp_path: Path) -> None:
    summary = tmp_path / "audit_summary.json"
    summary.write_text(json.dumps({"base_url": "https://example.com", "pages": []}), encoding="utf-8")
    payload = {
        "sample_cap": 20,
        "pages_analyzed": 1,
        "aggregate": {
            "experience": 70.0,
            "expertise": 65.0,
            "authoritativeness": 50.0,
            "trust": 80.0,
            "eeat": 66.3,
            "original_information_gain": 40.0,
            "passage_answerability": 72.0,
            "languages_seen": ["de"],
            "finding_summary": "Gemini assessed 1 page.",
        },
        "pages": [
            {
                "url": "https://example.com/",
                "title": "Home",
                "source_language": "de",
                "fetch_ok": True,
                "scores": {
                    "experience": 70.0,
                    "expertise": 65.0,
                    "authoritativeness": 50.0,
                    "trust": 80.0,
                    "original_information_gain": 40.0,
                    "passage_answerability": 72.0,
                },
                "findings": {
                    "experience": {
                        "score": 70.0,
                        "summary": "Clear first-hand process.",
                        "evidence": ["Wir haben getestet"],
                    },
                    "expertise": {"score": 65.0, "summary": "Solid depth.", "evidence": []},
                    "authoritativeness": {"score": 50.0, "summary": "Limited recognition.", "evidence": []},
                    "trust": {"score": 80.0, "summary": "Policies present.", "evidence": []},
                    "original_information_gain": {"score": 40.0, "summary": "Some data.", "evidence": []},
                    "passage_answerability": {
                        "score": 72.0,
                        "summary": "Answer-ready passages exist.",
                        "evidence": ["Die Methode funktioniert, weil"],
                    },
                },
            }
        ],
    }
    save_content_quality_gemini(tmp_path, payload)
    cached = load_cached_content_quality_gemini(tmp_path)
    assert cached is not None
    assert int(cached["schema_version"]) == CONTENT_QUALITY_SCHEMA_VERSION
    assert cached["merge_rule"] == "replace_eeat_and_answerability"
    assert cached["pages_analyzed"] == 1
    assert (tmp_path / CONTENT_QUALITY_GEMINI_FILE).is_file()

    # Stale when audit_summary mtime changes.
    time.sleep(0.05)
    summary.write_text(json.dumps({"base_url": "https://example.com", "pages": [{"x": 1}]}), encoding="utf-8")
    assert load_cached_content_quality_gemini(tmp_path) is None


def test_merge_replaces_eeat_and_answerability_keeps_schema() -> None:
    details = {
        "score": 50.0,
        "components": [
            {
                "key": "eeat",
                "title": "E-E-A-T Signals",
                "score": 40.0,
                "weight_pct": 35,
                "detail": "d",
                "finding_summary": "crawl",
                "evidence_example": "crawl snip",
            },
            {
                "key": "original_information_gain",
                "title": "Original information gain",
                "score": 20.0,
                "weight_pct": 15,
                "detail": "d",
                "finding_summary": "crawl",
                "evidence_example": "",
            },
            {
                "key": "passage_answerability",
                "title": "Passage-level answerability",
                "score": 30.0,
                "weight_pct": 15,
                "detail": "d",
                "finding_summary": "crawl",
                "evidence_example": "",
            },
            {
                "key": "content_formatting",
                "title": "Content formatting",
                "score": 60.0,
                "weight_pct": 10,
                "detail": "d",
                "finding_summary": "crawl",
                "evidence_example": "",
            },
            {
                "key": "schema_entity_markup",
                "title": "Schema",
                "score": 80.0,
                "weight_pct": 15,
                "detail": "d",
                "finding_summary": "crawl schema",
                "evidence_example": "",
            },
            {
                "key": "brand_visibility_authority",
                "title": "Brand",
                "score": 70.0,
                "weight_pct": 10,
                "detail": "d",
                "finding_summary": "crawl brand",
                "evidence_example": "",
            },
        ],
        "eeat": [
            {
                "name": "Experience",
                "tagline": "t",
                "what_it_means": "m",
                "how_scored": "h",
                "score": 10.0,
                "evidence": [{"url": "https://example.com/", "title": "Home", "snippet": "crawl"}],
                "evidence_note": "crawl note",
            },
            {
                "name": "Expertise",
                "tagline": "t",
                "what_it_means": "m",
                "how_scored": "h",
                "score": 10.0,
                "evidence": [],
                "evidence_note": "none",
            },
            {
                "name": "Authoritativeness",
                "tagline": "t",
                "what_it_means": "m",
                "how_scored": "h",
                "score": 10.0,
                "evidence": [],
                "evidence_note": "none",
            },
            {
                "name": "Trust",
                "tagline": "t",
                "what_it_means": "m",
                "how_scored": "h",
                "score": 10.0,
                "evidence": [],
                "evidence_note": "none",
            },
        ],
        "structure_answerability": [
            {
                "key": "original_information_gain",
                "title": "Original information gain",
                "score": 20.0,
                "description": "d",
                "examples": [],
                "empty_message": "none",
            },
            {
                "key": "passage_answerability",
                "title": "Passage-level answerability",
                "score": 30.0,
                "description": "d",
                "examples": [],
                "empty_message": "none",
            },
            {
                "key": "content_formatting",
                "title": "Content formatting",
                "score": 60.0,
                "description": "d",
                "examples": [{"url": "https://example.com/", "title": "Home", "snippet": "Heading: How"}],
                "empty_message": "none",
            },
        ],
        "schema_entity": {"score": 80.0, "summary": "s", "strengths": ["ok"], "improvements": [], "evidence": []},
    }
    gemini = {
        "schema_version": 1,
        "merge_rule": "replace_eeat_and_answerability",
        "pages_analyzed": 1,
        "aggregate": {
            "experience": 88.0,
            "expertise": 77.0,
            "authoritativeness": 66.0,
            "trust": 55.0,
            "eeat": 71.5,
            "original_information_gain": 44.0,
            "passage_answerability": 91.0,
            "languages_seen": ["de"],
            "finding_summary": "Gemini assessed 1 page (languages: de).",
        },
        "pages": [
            {
                "url": "https://example.com/",
                "title": "Home",
                "source_language": "de",
                "findings": {
                    "experience": {
                        "score": 88.0,
                        "summary": "Strong first-hand testing narrative.",
                        "evidence": ["Wir haben getestet"],
                    },
                    "expertise": {"score": 77.0, "summary": "Credible depth.", "evidence": []},
                    "authoritativeness": {"score": 66.0, "summary": "Some recognition.", "evidence": []},
                    "trust": {"score": 55.0, "summary": "Basic trust signals.", "evidence": []},
                    "original_information_gain": {
                        "score": 44.0,
                        "summary": "Limited original data.",
                        "evidence": [],
                    },
                    "passage_answerability": {
                        "score": 91.0,
                        "summary": "Clear extractable answers.",
                        "evidence": ["Die Methode funktioniert"],
                    },
                },
            }
        ],
    }

    merged = merge_gemini_into_content_quality(details, gemini)
    assert merged["gemini_overlay"]["available"] is True
    assert merged["gemini_overlay"]["status"] == "applied"

    by_name = {row["name"]: row for row in merged["eeat"]}
    assert by_name["Experience"]["score"] == 88.0
    assert by_name["Experience"]["scoring_source"] == "gemini"
    assert by_name["Experience"]["evidence"][0]["snippet"] == "Wir haben getestet"

    components = {c["key"]: c for c in merged["components"]}
    assert components["eeat"]["score"] == 71.5
    assert components["eeat"]["scoring_source"] == "gemini"
    assert components["original_information_gain"]["score"] == 44.0
    assert components["passage_answerability"]["score"] == 91.0
    # Crawl heuristics preserved
    assert components["content_formatting"]["score"] == 60.0
    assert components["content_formatting"]["scoring_source"] == "crawl_heuristic"
    assert components["schema_entity_markup"]["score"] == 80.0
    assert components["brand_visibility_authority"]["score"] == 70.0
    assert merged["schema_entity"]["score"] == 80.0

    structure = {row["key"]: row for row in merged["structure_answerability"]}
    assert structure["passage_answerability"]["score"] == 91.0
    assert structure["content_formatting"]["score"] == 60.0
    assert structure["content_formatting"]["examples"][0]["snippet"] == "Heading: How"

    expected = sum(c["score"] * c["weight_pct"] / 100 for c in merged["components"])
    assert merged["score"] == round(expected, 1)


def test_merge_falls_back_when_gemini_missing() -> None:
    details = {
        "score": 50.0,
        "components": [{"key": "eeat", "score": 40.0, "weight_pct": 35}],
        "eeat": [{"name": "Experience", "score": 40.0, "evidence": [], "evidence_note": "crawl"}],
    }
    merged = merge_gemini_into_content_quality(details, None)
    assert merged["gemini_overlay"]["available"] is False
    assert merged["eeat"][0]["score"] == 40.0


def test_enqueue_content_quality_job_persists_manifest_and_uses_local_thread(
    monkeypatch, tmp_path: Path
) -> None:
    audit_dir = tmp_path / "example-com"
    audit_dir.mkdir()
    (audit_dir / "audit_summary.json").write_text(
        json.dumps({"base_url": "https://example.com", "pages": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(content_quality_jobs.geo, "audit_dir_api_rel", lambda _path: "example-com")
    monkeypatch.delenv("CONTENT_QUALITY_JOB_NAME", raising=False)
    monkeypatch.setenv("CONTENT_QUALITY_FORCE_LOCAL", "1")

    ran: dict[str, str] = {}

    def fake_run(audit_dir: Path, *, request_id: str | None = None, **_kwargs) -> dict[str, str]:
        ran["request_id"] = str(request_id or "")
        return {"content_quality": "done"}

    monkeypatch.setattr(content_quality_jobs, "run_content_quality_analysis", fake_run)

    result = content_quality_jobs.enqueue_content_quality_job(audit_dir)
    assert result["status"] == "queued"
    assert result["execution"] == "local-thread"
    assert result["already_running"] is False
    pending = json.loads((audit_dir / content_quality_jobs.CONTENT_QUALITY_PENDING_FILE).read_text())
    assert pending["request_id"] == result["request_id"]
    manifests = list((audit_dir / content_quality_jobs.CONTENT_QUALITY_JOB_REQUESTS_DIR).glob("*.json"))
    assert len(manifests) == 1

    # Allow daemon thread to run.
    deadline = time.time() + 2
    while time.time() < deadline and not ran:
        time.sleep(0.05)
    assert ran.get("request_id") == result["request_id"]


def test_enqueue_reuses_active_request(monkeypatch, tmp_path: Path) -> None:
    audit_dir = tmp_path / "example-com"
    audit_dir.mkdir()
    monkeypatch.setattr(content_quality_jobs.geo, "audit_dir_api_rel", lambda _path: "example-com")
    (audit_dir / content_quality_jobs.CONTENT_QUALITY_PENDING_FILE).write_text(
        json.dumps(
            {
                "status": "running",
                "request_id": "existing",
                "execution": "executions/existing",
            }
        ),
        encoding="utf-8",
    )
    result = content_quality_jobs.enqueue_content_quality_job(audit_dir)
    assert result["already_running"] is True
    assert result["request_id"] == "existing"


def test_enqueue_skips_when_cache_current(monkeypatch, tmp_path: Path) -> None:
    audit_dir = tmp_path / "example-com"
    audit_dir.mkdir()
    (audit_dir / "audit_summary.json").write_text(
        json.dumps({"base_url": "https://example.com", "pages": []}),
        encoding="utf-8",
    )
    save_content_quality_gemini(
        audit_dir,
        {
            "pages_analyzed": 1,
            "aggregate": {"eeat": 50},
            "pages": [{"url": "https://example.com/", "scores": {}, "findings": {}}],
        },
    )
    monkeypatch.setattr(content_quality_jobs.geo, "audit_dir_api_rel", lambda _path: "example-com")
    result = content_quality_jobs.enqueue_content_quality_job(audit_dir)
    assert result["status"] == "done"
    assert result.get("cached") is True


def test_content_quality_jobs_enabled(monkeypatch) -> None:
    monkeypatch.delenv("CONTENT_QUALITY_FORCE_LOCAL", raising=False)
    monkeypatch.setenv("CONTENT_QUALITY_JOB_NAME", "geo-audit-content-quality")
    assert content_quality_jobs.content_quality_jobs_enabled() is True
    monkeypatch.setenv("CONTENT_QUALITY_FORCE_LOCAL", "1")
    assert content_quality_jobs.content_quality_jobs_enabled() is False


def test_content_quality_job_env_helpers(monkeypatch) -> None:
    monkeypatch.setenv("CONTENT_QUALITY_JOB_NAME", "geo-audit-content-quality")
    monkeypatch.setenv("CONTENT_QUALITY_JOB_PROJECT", "proj")
    monkeypatch.setenv("CONTENT_QUALITY_JOB_REGION", "europe-west1")
    assert content_quality_jobs.content_quality_job_name() == "geo-audit-content-quality"
    assert content_quality_jobs.content_quality_job_project() == "proj"
    assert content_quality_jobs.content_quality_job_region() == "europe-west1"


def test_enqueue_launches_cloud_run_when_enabled(monkeypatch, tmp_path: Path) -> None:
    audit_dir = tmp_path / "example-com"
    audit_dir.mkdir()
    (audit_dir / "audit_summary.json").write_text(
        json.dumps({"base_url": "https://example.com", "pages": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(content_quality_jobs.geo, "audit_dir_api_rel", lambda _path: "example-com")
    monkeypatch.setenv("CONTENT_QUALITY_JOB_NAME", "geo-audit-content-quality")
    monkeypatch.delenv("CONTENT_QUALITY_FORCE_LOCAL", raising=False)
    monkeypatch.setattr(
        content_quality_jobs,
        "_execute_content_quality_job",
        lambda *, audit_id, request_id, **_kwargs: f"executions/{audit_id}-{request_id}",
    )

    result = content_quality_jobs.enqueue_content_quality_job(audit_dir)
    assert result["status"] == "queued"
    assert result["execution"].startswith("executions/example-com-")
    assert result["already_running"] is False
    pending = json.loads((audit_dir / content_quality_jobs.CONTENT_QUALITY_PENDING_FILE).read_text())
    assert pending["execution"].startswith("executions/example-com-")
    manifest = json.loads(
        next((audit_dir / content_quality_jobs.CONTENT_QUALITY_JOB_REQUESTS_DIR).glob("*.json")).read_text()
    )
    assert manifest["status"] == "started"

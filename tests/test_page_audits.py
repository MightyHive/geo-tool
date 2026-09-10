import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api import geo_services as geo
from api import page_audits


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _primary_audit(tmp_path: Path) -> Path:
    audit_dir = tmp_path / "www.example.com_abc"
    _write(
        audit_dir / "audit_summary.json",
        {
            "audit_label": "primary",
            "base_url": "https://www.example.com",
            "brand_name": "Example",
            "pages": [],
        },
    )
    _write(audit_dir / "onboarding_context.json", {"brand_name_used": "Example"})
    return audit_dir


def test_urls_are_same_site_www_insensitive() -> None:
    assert page_audits.urls_are_same_site(
        "https://example.com/guides/brakes",
        "https://www.example.com",
    )
    assert page_audits.urls_are_same_site(
        "https://www.example.com/a",
        "https://example.com",
    )
    assert not page_audits.urls_are_same_site(
        "https://other.example.org/page",
        "https://www.example.com",
    )


def test_enqueue_rejects_off_site_url(tmp_path: Path) -> None:
    audit_dir = _primary_audit(tmp_path)
    with pytest.raises(HTTPException) as exc:
        page_audits.enqueue_page_audit(audit_dir, "https://evil.example/phishing")
    assert exc.value.status_code == 400
    assert "same site" in str(exc.value.detail)


def test_citation_filter_matches_this_page_only(tmp_path: Path) -> None:
    audit_dir = _primary_audit(tmp_path)
    page_url = "https://www.example.com/guides/brakes"
    _write(
        audit_dir / "prompt_performance_citations.json",
        {
            "top_cited_urls": [
                {"url": "https://example.com/guides/brakes", "prompt": "best brake pads"},
                {"url": "https://example.com/other", "prompt": "unrelated"},
            ]
        },
    )
    _write(
        audit_dir / "prompt_performance_metrics.json",
        {
            "live_probe": {
                "per_prompt": [
                    {
                        "prompt": "where to buy pads",
                        "citations_openai": [
                            {"url": "https://www.example.com/guides/brakes"},
                            {"url": "https://competitor.com/brakes"},
                        ],
                    }
                ]
            }
        },
    )
    matches = page_audits.citations_matching_page(audit_dir, page_url)
    assert len(matches) == 2
    assert all("guides/brakes" in item["url"] for item in matches)


def test_citation_presence_and_overall_mix() -> None:
    assert page_audits.citation_presence_score(0) == 0.0
    assert page_audits.citation_presence_score(2) == 85.0
    assert page_audits.overall_score(56.0, 70.0, 90.0) == 70.4


def test_page_probe_metrics_matches_main_visibility_model_by_surface(tmp_path: Path) -> None:
    audit_dir = _primary_audit(tmp_path)
    _write(
        audit_dir / "onboarding_context.json",
        {
            "brand_name_used": "Example",
            "brand_website_used": "https://example.com",
            "competitors_detail": [
                {
                    "competitor_brand": "Rival",
                    "competitor_website": "https://rival.example",
                }
            ],
        },
    )
    live = {
        "brand_name": "Example",
        "brand_site_url": "https://example.com",
        "per_prompt": [
            {
                "prompt": "best brake pads",
                "gemini_response": "Example and Rival make brake pads.",
                "google_aio_response": "Rival makes brake pads.",
                "mention_scores_gemini": {
                    "brand_signal": 1,
                    "competitor_detail": {"Rival": 1, "Incidental Name": 10},
                },
                "mention_scores_google_aio": {
                    "brand_signal": 0,
                    "competitor_detail": {"Rival": 1},
                },
            }
        ]
    }
    overall, surfaces, platforms = page_audits.page_probe_metrics(audit_dir, live)
    assert overall["visibility_pct"] == 50.0
    assert surfaces["chatbots"]["visibility_pct"] == 100.0
    assert surfaces["overviews"]["visibility_pct"] == 0.0
    assert surfaces["chatbots"]["response_count"] == 1
    assert surfaces["overviews"]["response_count"] == 1
    assert platforms["gemini"]["visibility_pct"] == 100.0
    assert platforms["google_aio"]["visibility_pct"] == 0.0
    assert overall["competitor_count"] == 1
    assert overall["detected_competitor_count"] == 1


def test_score_page_audit_marks_inherited_mix(monkeypatch: pytest.MonkeyPatch) -> None:
    categories = [
        SimpleNamespace(
            key="ai_visibility",
            score=50,
            subs=[SimpleNamespace(key="ai_citability", score=80)],
        ),
        SimpleNamespace(key="technical_setup", score=70, subs=[]),
        SimpleNamespace(key="content_structure", score=90, subs=[]),
    ]
    monkeypatch.setattr(
        page_audits.geo,
        "load_create_report",
        lambda: SimpleNamespace(score_audit=lambda _audit: ({}, categories)),
    )
    scores = page_audits.score_page_audit(
        parent_audit={"base_url": "https://example.com", "pages": [{"url": "https://example.com/a"}]},
        page_row={"url": "https://example.com/a"},
        citations=[],
    )
    assert scores["ai_visibility"] == 56.0
    assert scores["technical_setup"] == 70.0
    assert scores["content_quality"] == 90.0
    assert scores["overall"] == 70.4
    assert scores["details"]["citation_count"] == 0
    assert scores["details"]["citation_presence"] == 0.0


def test_list_primary_audits_ignores_page_audits_and_non_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary = _primary_audit(tmp_path)
    nested = primary / "page_audits" / "guides_brakes_abc"
    _write(
        nested / "audit_summary.json",
        {"audit_label": "single_page", "base_url": "https://example.com/guides/brakes"},
    )
    competitor = tmp_path / "competitor.com_xyz"
    _write(
        competitor / "audit_summary.json",
        {"audit_label": "competitor", "base_url": "https://competitor.com"},
    )
    monkeypatch.setattr(geo, "audit_output_base", lambda: tmp_path)
    rows = geo.list_primary_audits()
    assert [row["id"] for row in rows] == [primary.name]


def test_index_write_and_global_list_isolated_from_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _primary_audit(tmp_path)
    record = {
        "id": "guides_brakes_abc",
        "url": "https://example.com/guides/brakes",
        "parent_audit_id": audit_dir.name,
        "status": "done",
        "created_at": "2026-08-24T12:00:00+00:00",
        "updated_at": "2026-08-24T12:00:00+00:00",
        "title": "Brake guide",
        "scores": {
            "ai_visibility": 56.0,
            "technical_setup": 70.0,
            "content_quality": 90.0,
            "overall": 70.4,
        },
        "inherited": list(page_audits.INHERITED_KEYS),
        "page_specific": list(page_audits.PAGE_SPECIFIC_KEYS),
    }
    page_audits._write_json(
        page_audits.page_audits_root(audit_dir) / record["id"] / page_audits.PAGE_AUDIT_FILE,
        record,
    )
    page_audits._upsert_index_item(audit_dir, page_audits._index_summary(record))

    monkeypatch.setattr(geo, "audit_output_base", lambda: tmp_path)
    listed = page_audits.list_all_page_audits()
    assert len(listed) == 1
    assert listed[0]["id"] == "guides_brakes_abc"
    assert listed[0]["parent_brand_name"] == "Example"
    assert geo.list_primary_audits()[0]["id"] == audit_dir.name
    assert all("page_audits" not in row["id"] for row in geo.list_primary_audits())


def test_run_page_audit_job_requires_generated_prompts_and_queues_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _primary_audit(tmp_path)
    page_id = "guides_brakes_abc"
    page_url = "https://example.com/guides/brakes"
    monkeypatch.setattr(
        page_audits,
        "fetch_page_snapshot",
        lambda url, fallback=None: {
            "url": url,
            "page_title": "Brake guide",
            "http_status": 200,
        },
    )
    monkeypatch.setattr(page_audits, "_maybe_gemini_overlay", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        page_audits,
        "suggest_prompts_for_page_audit",
        lambda *_args, **_kwargs: {
            "prompts": ["How do I change brake pads in the UK?"],
            "source": "page_content",
        },
    )
    queued: dict[str, object] = {}

    def fake_enqueue(_audit_dir, *, page_id, prompts):
        queued.update({"page_id": page_id, "prompts": prompts})
        return {"status": "queued"}

    monkeypatch.setattr("api.prompt_jobs.enqueue_page_prompt_job", fake_enqueue)

    record = page_audits.run_page_audit_job(audit_dir, page_id, page_url)
    assert record["status"] == "running"
    assert record["stage"] == "probing"
    assert record["inherited"] == list(page_audits.INHERITED_KEYS)
    assert "ai_citability" in record["page_specific"]
    assert record["scores"] is None
    assert queued["page_id"] == page_id
    assert queued["prompts"] == ["How do I change brake pads in the UK?"]
    saved = page_audits.load_page_audit(audit_dir, page_id)
    assert saved["status"] == "running"
    assert saved["probe_status"] == "starting"
    index = page_audits.list_page_audits_for_parent(audit_dir)
    assert index[0]["id"] == page_id
    assert not (
        page_audits.page_audits_root(audit_dir) / page_id / page_audits.PAGE_BREAKDOWN_FILE
    ).exists()


def test_page_audit_cannot_finish_when_required_prompt_generation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _primary_audit(tmp_path)
    page_id = "guides_brakes_abc"
    page_url = "https://example.com/guides/brakes"
    monkeypatch.setattr(
        page_audits,
        "fetch_page_snapshot",
        lambda url, fallback=None: {
            "url": url,
            "page_title": "Brake guide",
            "http_status": 200,
        },
    )
    monkeypatch.setattr(page_audits, "_maybe_gemini_overlay", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        page_audits,
        "suggest_prompts_for_page_audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("generation unavailable")),
    )

    record = page_audits.run_page_audit_job(audit_dir, page_id, page_url)
    assert record["status"] == "error"
    assert record["scores"] is None
    assert "generation unavailable" in record["error"]
    assert not (
        page_audits.page_audits_root(audit_dir) / page_id / page_audits.PAGE_PROBE_FILE
    ).exists()


def test_build_page_breakdown_stamps_scope_and_filters_examples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_dir = tmp_path / "page"
    page_dir.mkdir()
    page_url = "https://example.com/guides/brakes"
    monkeypatch.setattr(
        page_audits.geo,
        "load_category_score_details",
        lambda _d: {
            "ai_visibility": {
                "score": 50,
                "components": [
                    {
                        "key": "ai_citability",
                        "title": "Citability",
                        "score": 80,
                        "weight_pct": 30,
                        "detail": "",
                        "finding_summary": "Extractable.",
                        "evidence_example": "",
                        "site_examples": [
                            {
                                "url": "https://example.com/guides/brakes",
                                "title": "Brakes",
                                "excerpt": "pads",
                                "context": "this page",
                            },
                            {
                                "url": "https://example.com/other",
                                "title": "Other",
                                "excerpt": "no",
                                "context": "other page",
                            },
                        ],
                    },
                    {
                        "key": "platform_readiness",
                        "title": "Platform",
                        "score": 40,
                        "weight_pct": 25,
                        "detail": "",
                        "finding_summary": "",
                        "evidence_example": "",
                    },
                ],
            },
            "technical_setup": {
                "score": 70,
                "components": [
                    {
                        "key": "ai_crawler_report",
                        "title": "Crawler",
                        "score": 60,
                        "weight_pct": 25,
                        "detail": "",
                        "finding_summary": "",
                        "evidence_example": "",
                    }
                ],
            },
            "content_structure": {
                "score": 90,
                "components": [
                    {
                        "key": "eeat",
                        "title": "E-E-A-T",
                        "score": 88,
                        "weight_pct": 35,
                        "detail": "",
                        "finding_summary": "",
                        "evidence_example": "",
                    }
                ],
            },
        },
    )
    monkeypatch.setattr(
        page_audits.geo,
        "load_technical_display_data",
        lambda _d: {
            "platform_readiness": [{"key": "openai", "name": "ChatGPT", "score": 40, "gap": ""}],
            "crawler_access": {"rows": [{"crawler": "GPTBot"}]},
        },
    )
    monkeypatch.setattr(
        page_audits.geo,
        "load_content_quality_details",
        lambda _d: {
            "score": 91,
            "components": [{"key": "eeat", "score": 88, "finding_summary": "ok"}],
            "eeat": [],
            "structure_answerability": [
                {
                    "key": "passage_answerability",
                    "examples": [
                        {"url": "https://example.com/guides/brakes", "snippet": "yes"},
                        {"url": "https://example.com/other", "snippet": "no"},
                    ],
                }
            ],
            "schema_entity": {
                "score": 50,
                "evidence": [{"url": "https://example.com/guides/brakes"}],
            },
            "brand_visibility_authority": {"score": 40, "rows": []},
            "gemini_overlay": {"available": True, "status": "applied"},
        },
    )
    scores = {
        "ai_visibility": 56.0,
        "technical_setup": 70.0,
        "content_quality": 90.0,
        "overall": 70.4,
        "prompt_metrics": {
            "score": 56.0,
            "visibility_pct": 60.0,
            "sov_pct": 35.0,
            "sov_performance_score": 50.0,
            "visible_response_count": 3,
            "response_count": 5,
        },
        "surface_metrics": {
            "chatbots": {"score": 60.0, "response_count": 4},
            "overviews": {"score": 40.0, "response_count": 1},
        },
        "details": {"page_citability": 80, "citation_presence": 0, "citation_count": 0},
    }
    payload = page_audits.build_page_breakdown(
        page_dir, scores=scores, citations=[], page_url=page_url
    )
    assert payload["ai_visibility"] == 56.0
    assert payload["overall"] == 70.7
    assert payload["prompt_metrics"]["score"] == 56.0
    assert payload["surface_metrics"]["chatbots"]["score"] == 60.0
    assert payload["component_scope"]["ai_crawler_report"] == "inherited"
    assert payload["component_scope"]["ai_citability"] == "page_specific"
    citability = next(
        row
        for row in payload["details"]["ai_visibility"]["components"]
        if row["key"] == "ai_citability"
    )
    assert citability["scope"] == "page_specific"
    assert citability["weight_pct"] == 0.0
    assert [ex["url"] for ex in citability["site_examples"]] == [
        "https://example.com/guides/brakes"
    ]
    crawler = next(
        row
        for row in payload["details"]["technical_setup"]["components"]
        if row["key"] == "ai_crawler_report"
    )
    assert crawler["scope"] == "inherited"
    examples = payload["content_quality_details"]["structure_answerability"][0]["examples"]
    assert [ex["url"] for ex in examples] == ["https://example.com/guides/brakes"]
    assert payload["content_quality_details"]["brand_visibility_authority"]["scope"] == "inherited"
    assert payload["crawler_access"]["scope"] == "inherited"
    page_citations = next(
        row
        for row in payload["details"]["ai_visibility"]["components"]
        if row["key"] == "page_citations"
    )
    assert page_citations["score"] == 0.0
    brand_visibility = next(
        row
        for row in payload["details"]["ai_visibility"]["components"]
        if row["key"] == "brand_visibility"
    )
    relative_sov = next(
        row
        for row in payload["details"]["ai_visibility"]["components"]
        if row["key"] == "share_of_voice"
    )
    assert brand_visibility["weight_pct"] == 60.0
    assert relative_sov["weight_pct"] == 40.0


def test_ensure_page_breakdown_backfills_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _primary_audit(tmp_path)
    page_id = "guides_brakes_abc"
    page_dir = page_audits.page_audits_root(audit_dir) / page_id
    record = {
        "id": page_id,
        "url": "https://example.com/guides/brakes",
        "status": "done",
        "scores": {
            "ai_visibility": 56,
            "technical_setup": 70,
            "content_quality": 90,
            "details": {"page_citability": 80, "citation_presence": 0, "citation_count": 0},
        },
    }
    page_audits._write_json(page_dir / page_audits.PAGE_AUDIT_FILE, record)
    page_audits._write_json(
        page_dir / page_audits.PAGE_SNAPSHOT_FILE,
        {"url": record["url"], "http_status": 200, "page_title": "Brakes"},
    )
    calls = {"n": 0}

    def fake_build(*_args, **_kwargs):
        calls["n"] += 1
        return {"overall": 70.4, "component_scope": {"eeat": "page_specific"}}

    monkeypatch.setattr(page_audits, "build_page_breakdown", fake_build)
    first = page_audits.ensure_page_breakdown(audit_dir, page_id, record)
    assert first["overall"] == 70.4
    assert (page_dir / page_audits.PAGE_BREAKDOWN_FILE).is_file()
    second = page_audits.ensure_page_breakdown(audit_dir, page_id, record)
    assert second["overall"] == 70.4
    assert calls["n"] == 1


def test_enqueue_queues_same_site_without_running_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _primary_audit(tmp_path)

    class FakeThread:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def start(self) -> None:
            return None

    monkeypatch.setattr(page_audits.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        page_audits.geo,
        "load_crawl_site",
        lambda: SimpleNamespace(slug_from_url=lambda _url: "guides_brakes_abc"),
    )
    record = page_audits.enqueue_page_audit(audit_dir, "https://www.example.com/guides/brakes")
    assert record["status"] == "queued"
    assert record["id"] == "guides_brakes_abc"
    assert (page_audits.page_audits_root(audit_dir) / "guides_brakes_abc" / "page_audit.json").is_file()


def test_enqueue_existing_page_clears_stale_artifacts_and_reruns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _primary_audit(tmp_path)
    page_id = "guides_brakes_abc"
    page_url = _done_page(audit_dir, page_id)
    page_dir = page_audits.page_audits_root(audit_dir) / page_id
    for name in (
        page_audits.PAGE_PROBE_FILE,
        page_audits.PAGE_PROBE_PENDING_FILE,
        page_audits.PAGE_PROMPTS_FILE,
        page_audits.PAGE_BREAKDOWN_FILE,
    ):
        page_audits._write_json(page_dir / name, {"stale": True})

    started: list[str] = []

    class FakeThread:
        def __init__(self, *_args, **kwargs) -> None:
            started.append(str(kwargs.get("name") or "thread"))

        def start(self) -> None:
            return None

    monkeypatch.setattr(page_audits.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        page_audits.geo,
        "load_crawl_site",
        lambda: SimpleNamespace(slug_from_url=lambda _url: page_id),
    )

    record = page_audits.enqueue_page_audit(audit_dir, page_url)

    assert record["status"] == "queued"
    assert record["scores"] is None
    assert record["probe_status"] is None
    assert started, "a fresh full audit job should be started"
    for name in page_audits.PAGE_RERUN_STALE_FILES:
        assert not (page_dir / name).is_file(), f"{name} should be cleared before a re-run"


def test_page_prompt_job_keeps_all_prompts_up_to_the_page_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api import prompt_jobs

    audit_dir = tmp_path / "example-com"
    audit_dir.mkdir()
    monkeypatch.setattr(prompt_jobs.geo, "audit_dir_api_rel", lambda _path: "example-com")
    monkeypatch.setattr(
        prompt_jobs,
        "_execute_prompt_job",
        lambda *, audit_id, request_id: f"executions/{audit_id}-{request_id}",
    )
    prompts = [f"Which brake pads suit a Ford Focus variant {i} in the UK?" for i in range(25)]

    prompt_jobs.enqueue_page_prompt_job(audit_dir, page_id="guides_brakes_abc", prompts=prompts)

    manifests = list((audit_dir / prompt_jobs.PROMPT_JOB_REQUESTS_DIR).glob("*.json"))
    manifest = json.loads(manifests[0].read_text())
    assert len(manifest["prompts"]) == page_audits.MAX_PAGE_PROMPTS == 25
    assert manifest["max_prompts"] == 25


def test_page_audit_generates_full_prompt_set_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _primary_audit(tmp_path)
    page_id = "guides_brakes_abc"
    _done_page(audit_dir, page_id)
    seen: dict[str, int] = {}

    def fake_suggest(**kwargs) -> list[str]:
        seen["max_prompts"] = int(kwargs.get("max_prompts") or 0)
        return [f"Prompt number {i} in the UK?" for i in range(seen["max_prompts"])]

    monkeypatch.setattr("prompt_suggest.suggest_prompts_for_page", fake_suggest)
    payload = page_audits.suggest_prompts_for_page_audit(audit_dir, page_id)

    assert seen["max_prompts"] == 25
    assert len(payload["prompts"]) == 25


def _done_page(audit_dir: Path, page_id: str = "guides_brakes_abc") -> str:
    page_url = "https://www.example.com/guides/brakes"
    page_dir = page_audits.page_audits_root(audit_dir) / page_id
    page_audits._write_json(
        page_dir / page_audits.PAGE_AUDIT_FILE,
        {
            "id": page_id,
            "url": page_url,
            "parent_audit_id": audit_dir.name,
            "status": "done",
            "scores": {
                "ai_visibility": 56.0,
                "technical_setup": 70.0,
                "content_quality": 90.0,
                "overall": 70.4,
                "details": {"page_citability": 80, "citation_presence": 0, "citation_count": 0},
            },
        },
    )
    page_audits._write_json(
        page_dir / page_audits.PAGE_SNAPSHOT_FILE,
        {
            "url": page_url,
            "page_title": "How to change brake pads",
            "meta_description": "A DIY guide to brake pads",
            "headings": {"h1": ["How to change brake pads"], "h2": ["Tools you need"]},
            "json_ld_types": ["Article"],
            "content_signals": {"representative_excerpt": "Replace pads when the lining is thin."},
        },
    )
    return page_url


def test_suggest_prompts_for_page_applies_geo_locator(monkeypatch: pytest.MonkeyPatch) -> None:
    import prompt_suggest

    monkeypatch.setattr(
        prompt_suggest,
        "_gemini_generate",
        lambda **_kwargs: json.dumps(
            {"prompts": ["How do I change brake pads?", "What tools do I need for pads"]}
        ),
    )
    prompts = prompt_suggest.suggest_prompts_for_page(
        page_url="https://example.com/guides/brakes",
        brand_name="Example",
        page_title="How to change brake pads",
        headings={"h1": ["How to change brake pads"]},
        market_country="United Kingdom",
        market_country_code="GB",
        max_prompts=5,
    )
    assert len(prompts) == 2
    assert all("in the UK" in item for item in prompts)


def test_page_prompt_token_budget_scales_with_requested_count() -> None:
    import prompt_suggest

    assert prompt_suggest._prompt_token_budget(5) == 1170
    assert prompt_suggest._prompt_token_budget(25) == 4170
    assert prompt_suggest._prompt_token_budget(1) >= 1024


def test_suggest_prompts_for_page_salvages_truncated_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import prompt_suggest

    truncated = (
        '{"prompts": ["Which banks offer digital asset custody in the UK?", '
        '"How can investors tokenise real-world assets in the UK?", "What are'
    )
    monkeypatch.setattr(prompt_suggest, "_gemini_generate", lambda **_kwargs: truncated)
    prompts = prompt_suggest.suggest_prompts_for_page(
        page_url="https://www.sc.com/en/corporate-investment-banking/digital-assets/",
        brand_name="Standard Chartered",
        market_country="United Kingdom",
        market_country_code="GB",
        max_prompts=25,
    )
    assert prompts == [
        "Which banks offer digital asset custody in the UK?",
        "How can investors tokenise real-world assets in the UK?",
    ]


def test_suggest_prompts_for_page_still_errors_without_salvageable_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import prompt_suggest

    monkeypatch.setattr(prompt_suggest, "_gemini_generate", lambda **_kwargs: "not json at all")
    with pytest.raises(ValueError, match="did not return valid JSON"):
        prompt_suggest.suggest_prompts_for_page(
            page_url="https://example.com/guides/brakes",
            brand_name="Example",
            max_prompts=5,
        )


def test_suggest_prompts_for_page_audit_persists_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _primary_audit(tmp_path)
    page_id = "guides_brakes_abc"
    _done_page(audit_dir, page_id)

    def fake_suggest(**_kwargs):
        return ["Best brake pads in the UK?", "How to change pads in the UK?"]

    monkeypatch.setattr("prompt_suggest.suggest_prompts_for_page", fake_suggest)
    payload = page_audits.suggest_prompts_for_page_audit(audit_dir, page_id)
    assert payload["source"] == "page_content"
    stored = page_audits._read_json(
        page_audits.page_audits_root(audit_dir) / page_id / page_audits.PAGE_PROMPTS_FILE
    )
    assert stored["prompts"] == payload["prompts"]
    assert len(stored["prompts"]) == 2


def test_suggest_prompts_rejects_incomplete_audit(tmp_path: Path) -> None:
    audit_dir = _primary_audit(tmp_path)
    page_id = "guides_brakes_abc"
    page_audits._write_json(
        page_audits.page_audits_root(audit_dir) / page_id / page_audits.PAGE_AUDIT_FILE,
        {"id": page_id, "url": "https://example.com/a", "status": "running"},
    )
    with pytest.raises(HTTPException) as exc:
        page_audits.suggest_prompts_for_page_audit(audit_dir, page_id)
    assert exc.value.status_code == 400


def test_merge_includes_page_probe_citations(tmp_path: Path) -> None:
    audit_dir = _primary_audit(tmp_path)
    page_id = "guides_brakes_abc"
    page_url = _done_page(audit_dir, page_id)
    _write(
        audit_dir / "prompt_performance_citations.json",
        {"top_cited_urls": [{"url": page_url, "prompt": "master pads", "platform": "gemini"}]},
    )
    page_dir = page_audits.page_audits_root(audit_dir) / page_id
    page_audits._write_json(
        page_dir / page_audits.PAGE_PROBE_FILE,
        {
            "live_probe": {
                "per_prompt": [
                    {
                        "prompt": "how to change pads",
                        "citations_openai": [{"url": page_url}],
                    }
                ]
            }
        },
    )
    merged = page_audits.merge_page_citations(audit_dir, page_id, page_url)
    sources = {item.get("source") for item in merged}
    assert len(merged) == 2
    assert "page_probe" in sources


def test_run_page_prompt_job_writes_only_page_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _primary_audit(tmp_path)
    page_id = "guides_brakes_abc"
    page_url = _done_page(audit_dir, page_id)
    master_probe = {"live_probe": {"per_prompt": [{"prompt": "leave me"}]}}
    _write(audit_dir / "prompt_performance_live_probe.json", master_probe)
    categories = [
        SimpleNamespace(
            key="ai_visibility",
            score=50,
            subs=[SimpleNamespace(key="ai_citability", score=80)],
        ),
        SimpleNamespace(key="technical_setup", score=70, subs=[]),
        SimpleNamespace(key="content_structure", score=90, subs=[]),
    ]
    monkeypatch.setattr(
        page_audits.geo,
        "load_create_report",
        lambda: SimpleNamespace(score_audit=lambda _audit: ({}, categories)),
    )
    monkeypatch.setattr(
        page_audits.geo,
        "load_category_score_details",
        lambda _d: {"ai_visibility": {"components": []}, "technical_setup": {"components": []}},
    )
    monkeypatch.setattr(page_audits.geo, "load_technical_display_data", lambda _d: {})
    monkeypatch.setattr(page_audits.geo, "load_content_quality_details", lambda _d: {"score": 90})

    def fake_probes(prompts, **_kwargs):
        return {
            "per_prompt": [
                {
                    "prompt": prompts[0],
                    "openai_response": "Example explains how to change brake pads.",
                    "citations_openai": [{"url": page_url}],
                    "mention_scores_openai": {
                        "brand_signal": 1,
                        "competitor_detail": {},
                    },
                }
            ]
        }

    monkeypatch.setattr("prompt_suggest.run_live_prompt_probes", fake_probes)
    page_audits.run_page_prompt_job(audit_dir, page_id, ["How do I change brake pads in the UK?"])
    page_dir = page_audits.page_audits_root(audit_dir) / page_id
    assert (page_dir / page_audits.PAGE_PROBE_FILE).is_file()
    assert json.loads((audit_dir / "prompt_performance_live_probe.json").read_text()) == master_probe
    assert not (audit_dir / "prompt_performance_metrics.json").exists()
    record = page_audits.load_page_audit(audit_dir, page_id)
    assert record["status"] == "done"
    assert record["probe_status"] == "done"
    assert record["scores"]["ai_visibility"] == 100.0
    assert record["scores"]["prompt_metrics"]["visibility_pct"] == 100.0
    assert record["scores"]["surface_metrics"]["chatbots"]["score"] == 100.0
    assert record["scores"]["surface_metrics"]["overviews"]["response_count"] == 0
    assert record["scores"]["platform_metrics"]["openai"]["score"] == 100.0
    assert record["scores"]["details"]["citation_count"] >= 1


def test_enqueue_page_prompts_rejects_empty(tmp_path: Path) -> None:
    audit_dir = _primary_audit(tmp_path)
    page_id = "guides_brakes_abc"
    _done_page(audit_dir, page_id)
    with pytest.raises(HTTPException) as exc:
        page_audits.enqueue_page_prompts(audit_dir, page_id, ["  hi  "])
    assert exc.value.status_code == 400


def test_parent_competitors_attached_on_page_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _primary_audit(tmp_path)
    _write(
        audit_dir / "onboarding_context.json",
        {
            "brand_name_used": "Example",
            "competitors_detail": [
                {"competitor_brand": "Rival", "competitor_website": "https://rival.com"},
                {"competitor_brand": "", "competitor_website": ""},
            ],
        },
    )
    page_id = "guides_brakes_abc"
    _write(
        page_audits.page_audits_root(audit_dir) / page_id / page_audits.PAGE_AUDIT_FILE,
        {
            "id": page_id,
            "url": "https://example.com/guides/brakes",
            "status": "running",
            "title": "Brake guide",
        },
    )
    assert page_audits._parent_competitors(audit_dir) == [
        {"competitor_brand": "Rival", "competitor_website": "https://rival.com"},
    ]
    monkeypatch.setattr(page_audits, "_audit_dir_or_404", lambda _audit_id: audit_dir)
    record = page_audits.get_page_audit("www.example.com_abc", page_id)
    assert record["parent_brand_name"] == "Example"
    assert record["parent_competitors"] == [
        {"competitor_brand": "Rival", "competitor_website": "https://rival.com"},
    ]

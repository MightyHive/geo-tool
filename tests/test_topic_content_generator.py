from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import topic_content_jobs, topic_content_samples
import topic_content_generator
from topic_content_generator import (
    ARTIFACT_FILE,
    compute_topic_visibility,
    generate_topic_outline,
    load_artifact,
    parse_structure,
    refresh_evidence,
    topic_sample_path,
)


def _write_probe(audit_dir: Path, *, include_gamma: bool = True) -> None:
    rows = [
        {"product_or_service": "Alpha", "prompts": ["alpha question"]},
        {"product_or_service": "Beta", "prompts": ["beta question"]},
        {"product_or_service": "Gamma", "prompts": ["gamma question"]},
        {"product_or_service": "No responses", "prompts": ["silent question"]},
    ]
    en_rows = [
        {
            "prompt": "alpha question",
            "runs": {
                "gemini": [
                    {
                        "response": "Acme is relevant.",
                        "mention_scores": {"brand_signal": 1},
                        "citations": [{"url": "https://source.example/alpha"}],
                    }
                ],
                "openai": [{"response": "No named brand.", "mention_scores": {}}],
            },
        },
        {
            "prompt": "beta question",
            "runs": {"gemini": [{"response": "No mention.", "mention_scores": {}}]},
        },
        {
            "prompt": "gamma question",
            "runs": {"claude": [{"response": "No mention.", "mention_scores": {}}]},
        },
        {"prompt": "silent question", "runs": {"gemini": [{"error": "timeout"}]}},
    ]
    if not include_gamma:
        en_rows = [row for row in en_rows if row["prompt"] != "gamma question"]
        rows = [row for row in rows if row["product_or_service"] != "Gamma"]
    payload = {
        "brand_name": "Acme",
        "brand_site_url": "https://acme.example",
        "use_pss": True,
        "pss_rows": rows,
        "probed_pss_rows": rows,
        "category_labels": [row["product_or_service"] for row in rows],
        "competitors": [
            {
                "competitor_brand": "Other Co",
                "competitor_website": "https://other.example",
            }
        ],
        "primary_market": {"country": "Belgium", "country_id": "BE"},
        "prompt_locales": [
            {
                "key": "BE:en",
                "country": "Belgium",
                "language": "en",
                "language_name": "English",
            },
            {
                "key": "BE:fr",
                "country": "Belgium",
                "language": "fr",
                "language_name": "French",
            },
        ],
        "default_locale_key": "BE:en",
        "locale_probes": {
            "BE:en": {
                "source_prompts": [row["prompts"][0] for row in rows],
                "prompts_probed": [row["prompts"][0] for row in rows],
                "live_probe": {
                    "brand_match_tokens": ["Acme"],
                    "per_prompt": en_rows,
                },
            },
            "BE:fr": {
                "source_prompts": ["alpha question"],
                "prompts_probed": ["question alpha traduite"],
                "live_probe": {
                    "brand_match_tokens": ["Acme"],
                    "per_prompt": [
                        {
                            "prompt": "question alpha traduite",
                            "runs": {
                                "claude": [
                                    {
                                        "response": "ACME apparaît dans la réponse.",
                                        "mention_scores": {},
                                    }
                                ],
                                "google_aio": [
                                    {
                                        "response": "Aucune marque.",
                                        "mention_scores": {},
                                    }
                                ],
                            },
                        }
                    ],
                },
            },
        },
    }
    (audit_dir / "prompt_performance_live_probe.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_response_weighting_translation_ties_and_no_responses(tmp_path: Path) -> None:
    _write_probe(tmp_path)
    topics, provenance = compute_topic_visibility(tmp_path)
    by_topic = {row["topic"]: row for row in topics}

    assert "No responses" not in by_topic
    assert by_topic["Alpha"]["responses"] == 4
    assert by_topic["Alpha"]["brand_mentions"] == 2
    assert by_topic["Alpha"]["visibility"] == 50.0
    assert by_topic["Alpha"]["suggested"] is True
    assert "question alpha traduite" in by_topic["Alpha"]["related_prompts"]
    assert by_topic["Alpha"]["evidence_examples"][0]["response_excerpt"] == "Acme is relevant."
    assert by_topic["Alpha"]["evidence_examples"][0]["citations"][0]["url"].startswith("https://")
    assert by_topic["Beta"]["visibility"] == 0.0
    assert by_topic["Gamma"]["visibility"] == 0.0
    assert by_topic["Beta"]["suggested"] is True
    assert by_topic["Beta"]["rank"] == by_topic["Gamma"]["rank"] == 1
    assert [row["topic"] for row in topics[:2]] == ["Beta", "Gamma"]
    assert provenance["primary_file"] == "prompt_performance_live_probe.json"
    assert provenance["primary_mtime"] is not None
    assert provenance["metrics_file"] is None


def test_topics_merge_from_metrics_when_probe_lacks_pss(tmp_path: Path) -> None:
    """Live-probe-only audits still resolve PSS topics from metrics (Recommendations source)."""
    pss_rows = [
        {"product_or_service": "Car Batteries", "prompts": ["best car battery uk"]},
        {"product_or_service": "Engine Oil", "prompts": ["best engine oil uk"]},
    ]
    (tmp_path / "prompt_performance_live_probe.json").write_text(
        json.dumps(
            {
                "brand_name": "Euro Car Parts",
                "live_probe": {
                    "brand_match_tokens": ["Euro Car Parts"],
                    "per_prompt": [
                        {
                            "prompt": "best car battery uk",
                            "runs": {
                                "gemini": [
                                    {
                                        "response": "No brand.",
                                        "mention_scores": {},
                                    }
                                ]
                            },
                        },
                        {
                            "prompt": "best engine oil uk",
                            "runs": {
                                "gemini": [
                                    {
                                        "response": "Euro Car Parts stocks oil.",
                                        "mention_scores": {"brand_signal": 1},
                                    }
                                ]
                            },
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "prompt_performance_metrics.json").write_text(
        json.dumps(
            {
                "brand_name": "Euro Car Parts",
                "use_pss": True,
                "pss_rows": pss_rows,
                "probed_pss_rows": pss_rows,
                "category_labels": ["Car Batteries", "Engine Oil"],
            }
        ),
        encoding="utf-8",
    )

    topics, provenance = compute_topic_visibility(tmp_path)
    by_topic = {row["topic"]: row for row in topics}

    assert "Other" not in by_topic
    assert set(by_topic) == {"Car Batteries", "Engine Oil"}
    assert by_topic["Car Batteries"]["visibility"] == 0.0
    assert by_topic["Car Batteries"]["suggested"] is True
    assert by_topic["Engine Oil"]["visibility"] == 100.0
    assert by_topic["Engine Oil"]["suggested"] is False
    assert topics[0]["topic"] == "Car Batteries"
    assert provenance["metrics_file"] == "prompt_performance_metrics.json"


def test_refresh_retains_sample_only_for_surviving_topic(tmp_path: Path) -> None:
    _write_probe(tmp_path)
    artifact = refresh_evidence(tmp_path)
    artifact["topics"]["Beta"]["sample"] = {"topic": "Beta", "outline": {"title": "Saved"}}
    artifact["topics"]["Gamma"]["sample"] = {"topic": "Gamma", "outline": {"title": "Removed"}}
    (tmp_path / ARTIFACT_FILE).write_text(json.dumps(artifact), encoding="utf-8")
    topic_sample_path(tmp_path, "Gamma").parent.mkdir(parents=True, exist_ok=True)
    topic_sample_path(tmp_path, "Gamma").write_text(
        json.dumps({"topic": "Gamma", "outline": {"title": "Removed"}}),
        encoding="utf-8",
    )

    _write_probe(tmp_path, include_gamma=False)
    refreshed = refresh_evidence(tmp_path)
    assert refreshed["topics"]["Beta"]["sample"]["outline"]["title"] == "Saved"
    assert "Gamma" not in refreshed["topics"]
    assert not topic_sample_path(tmp_path, "Gamma").exists()


class _FakeModels:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        title = f"Outline {len(self.calls)}"
        value = {
            "title": title,
            "meta_description": "A sufficiently detailed meta description for this topic outline.",
            "audience": "Prospective customers",
            "intent": "Informational",
            "sections": [
                {
                    "id": "overview",
                    "heading": "Overview",
                    "level": "H2",
                    "instructions": "Explain the topic without unsupported claims.",
                    "sample_copy": "Verify all brand-specific details before publication.",
                    "evidence_citation_opportunities": ["Add a primary source."],
                }
            ],
            "faqs": [],
            "internal_links": [
                {
                    "anchor_text": "Related service",
                    "target_url_or_path": "/suggested-service",
                    "rationale": "Suggested target; verify the final URL.",
                }
            ],
        }
        return type("Response", (), {"text": json.dumps(value)})()


class _FakeClient:
    def __init__(self) -> None:
        self.models = _FakeModels()


def test_generation_replaces_current_sample_and_uses_exact_default_model(
    monkeypatch, tmp_path: Path
) -> None:
    _write_probe(tmp_path)
    calls: list[str] = []
    monkeypatch.delenv("GEMINI_TOPIC_CONTENT_MODEL", raising=False)

    def fake_request(_prompt: str, model: str | None = None):
        calls.append(model or "gemini-3.5-flash")
        payload = json.loads(_FakeModels().generate_content().text)
        payload["title"] = f"Outline {len(calls)}"
        return topic_content_generator.TopicContentOutline.model_validate(payload), calls[-1]

    monkeypatch.setattr(topic_content_generator, "_request_outline", fake_request)
    first = generate_topic_outline(tmp_path, "Beta")
    second = generate_topic_outline(
        tmp_path,
        "Beta",
        structure=[
            {
                "heading": "Custom evidence section",
                "level": 2,
                "instructions": "Use the requested custom structure.",
            }
        ],
    )
    artifact = load_artifact(tmp_path)

    assert first["outline"]["title"] == "Outline 1"
    assert second["outline"]["title"] == "Outline 2"
    assert second["outline"]["sections"][0]["heading"] == "Custom evidence section"
    assert second["outline"]["sections"][0]["id"] == "1-custom-evidence-section"
    assert artifact
    assert artifact["topics"]["Beta"]["sample"]["outline"]["title"] == "Outline 2"
    assert calls[0] == "gemini-3.5-flash"
    assert len([entry for entry in artifact["topics"].values() if entry.get("sample")]) == 1


def test_parse_structure_accepts_wrapper_and_normalizes_frontend_shape() -> None:
    parsed = parse_structure(
        {
            "sections": [
                {
                    "id": "Buying Guide",
                    "heading": "Buying guide",
                    "level": "H2",
                    "instructions": "Explain selection criteria.",
                    "evidence_citation_opportunities": ["Independent test data"],
                },
                {
                    "id": "comparison",
                    "heading": "Comparison",
                    "level": "H3",
                },
            ]
        }
    )
    assert parsed and parsed[0]["id"] == "buying-guide"

    normalized = parse_structure([{"heading": "Child", "level": 3}])
    assert normalized
    assert normalized[0]["id"] == "1-child"
    assert normalized[0]["level"] == "H2"


def test_job_dedupes_active_topic(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(topic_content_jobs.geo, "audit_dir_api_rel", lambda _path: "audit-1")
    (tmp_path / topic_content_jobs.PENDING_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "topics": {
                    "Beta": {
                        "topic": "Beta",
                        "request_id": "existing",
                        "status": "running",
                        "execution": "executions/existing",
                        "updated_at": topic_content_jobs._now(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    result = topic_content_jobs.enqueue_topic_content_job(tmp_path, topic="Beta")
    assert result["already_running"] is True
    assert result["request_id"] == "existing"


def test_stale_job_state_allows_retry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(topic_content_jobs.geo, "audit_dir_api_rel", lambda _path: "audit-1")
    monkeypatch.setenv("TOPIC_CONTENT_FORCE_LOCAL", "1")
    stale = {
        "topic": "Beta",
        "request_id": "stale",
        "status": "running",
        "updated_at": "2020-01-01T00:00:00+00:00",
    }
    topic_content_jobs._write_json(
        topic_content_jobs.topic_state_path(tmp_path, "Beta"),
        stale,
    )
    monkeypatch.setattr(threading.Thread, "start", lambda _self: None)

    result = topic_content_jobs.enqueue_topic_content_job(tmp_path, topic="Beta")

    assert result["already_running"] is False
    assert result["request_id"] != "stale"


def test_topic_claim_is_exclusive(tmp_path: Path) -> None:
    assert topic_content_jobs._try_claim_topic(tmp_path, "Beta", "first") is True
    assert topic_content_jobs._try_claim_topic(tmp_path, "Beta", "second") is False
    topic_content_jobs._release_topic_claim(tmp_path, "Beta", "first")
    assert topic_content_jobs._try_claim_topic(tmp_path, "Beta", "second") is True


def test_router_get_auto_enqueues_and_post_returns_202(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "audit_summary.json").write_text("{}", encoding="utf-8")
    artifact = {
        "version": 1,
        "updated_at": "now",
        "source_probe": {"primary_mtime": 1},
        "topics": {
            "Beta": {
                "evidence": {
                    "topic": "Beta",
                    "rank": 1,
                    "visibility": 0,
                    "responses": 1,
                    "brand_mentions": 0,
                },
                "sample": None,
            }
        },
    }
    calls: list[str] = []
    monkeypatch.setattr(topic_content_samples.geo, "resolve_audit_dir", lambda _audit_id: tmp_path)
    monkeypatch.setattr(topic_content_samples, "ensure_evidence", lambda *_args, **_kwargs: artifact)
    monkeypatch.setattr(topic_content_samples, "get_topic_content_job_states", lambda _path: {})

    def fake_enqueue(_path: Path, *, topic: str, **_kwargs):
        calls.append(topic)
        return {"topic": topic, "status": "queued", "request_id": "request-1"}

    monkeypatch.setattr(topic_content_samples, "enqueue_topic_content_job", fake_enqueue)
    app = FastAPI()
    app.include_router(topic_content_samples.router)
    client = TestClient(app)

    get_response = client.get("/api/audits/audit-1/topic-content-samples")
    assert get_response.status_code == 200
    assert get_response.json()["has_probe_data"] is True
    assert get_response.json()["topics"][0]["topic"] == "Beta"
    assert get_response.json()["auto_enqueued"]["status"] == "queued"
    assert calls == ["Beta"]

    post_response = client.post(
        "/api/audits/audit-1/topic-content-samples",
        json={"topic": "Beta", "refresh": True},
    )
    assert post_response.status_code == 202
    assert post_response.json()["topics"][0]["topic"] == "Beta"
    assert calls == ["Beta", "Beta"]

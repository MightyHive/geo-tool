"""Tests for locale fan-out of prompt probe Cloud Run jobs."""

from __future__ import annotations

import json
from pathlib import Path

from api import prompt_jobs
import api.prompt_performance as pp


def _seed_multi_locale_audit(tmp_path: Path) -> Path:
    audit_dir = tmp_path / "samsung-com"
    audit_dir.mkdir()
    onboarding = {
        "brand_name_used": "Samsung",
        "brand_website_used": "https://www.samsung.com/",
        "geo_market_country": "Belgium",
        "geo_market_country_code": "BE",
        "products_and_services_rows": [
            {"product_or_service": "Phone", "prompts": ["best phone brand"]},
        ],
        "prompt_locales": [
            {
                "country": "Belgium",
                "country_code": "BE",
                "language": "en",
                "language_name": "English",
                "key": "BE:en",
                "label": "Belgium: English",
            },
            {
                "country": "Belgium",
                "country_code": "BE",
                "language": "fr",
                "language_name": "French",
                "key": "BE:fr",
                "label": "Belgium: French",
            },
            {
                "country": "Netherlands",
                "country_code": "NL",
                "language": "nl",
                "language_name": "Dutch",
                "key": "NL:nl",
                "label": "Netherlands: Dutch",
            },
        ],
    }
    (audit_dir / "onboarding_context.json").write_text(
        json.dumps(onboarding), encoding="utf-8"
    )
    (audit_dir / "products_and_services.json").write_text(
        json.dumps({"rows": onboarding["products_and_services_rows"]}),
        encoding="utf-8",
    )
    return audit_dir


def test_plan_locale_job_payloads_fans_out_n_locales(monkeypatch, tmp_path: Path) -> None:
    audit_dir = _seed_multi_locale_audit(tmp_path)
    monkeypatch.setattr(prompt_jobs.geo, "audit_dir_api_rel", lambda _p: "samsung-com")

    payloads = prompt_jobs.plan_locale_job_payloads(
        audit_dir,
        mode="post_audit",
        report_mode=True,
        completion={"primary": "https://www.samsung.com/"},
    )

    assert len(payloads) == 3
    assert [p["locale_key"] for p in payloads] == ["BE:en", "BE:fr", "NL:nl"]
    assert all(p["locale_total"] == 3 for p in payloads)
    assert [p["locale_index"] for p in payloads] == [1, 2, 3]
    assert all(p["mode"] == "post_audit" for p in payloads)
    assert all(p["completion"]["primary"] == "https://www.samsung.com/" for p in payloads)


def test_enqueue_prompt_job_launches_one_execution_per_locale(
    monkeypatch, tmp_path: Path
) -> None:
    audit_dir = _seed_multi_locale_audit(tmp_path)
    monkeypatch.setattr(prompt_jobs.geo, "audit_dir_api_rel", lambda _p: "samsung-com")
    launches: list[tuple[str, str]] = []

    def fake_execute(*, audit_id: str, request_id: str) -> str:
        launches.append((audit_id, request_id))
        return f"executions/{audit_id}-{request_id}"

    monkeypatch.setattr(prompt_jobs, "_execute_prompt_job", fake_execute)

    result = prompt_jobs.enqueue_prompt_job(
        audit_dir,
        mode="post_audit",
        report_mode=True,
        completion={"primary": "https://www.samsung.com/", "brand_name": "Samsung"},
    )

    assert result["fanout"] is True
    assert result["locale_count"] == 3
    assert result["already_running"] is False
    assert len(launches) == 3
    assert len(result["launched"]) == 3

    pending = json.loads((audit_dir / prompt_jobs.PROMPT_PENDING_FILE).read_text())
    assert pending["fanout"] is True
    assert pending["batch_id"] == result["batch_id"]
    assert set(pending["locales"]) == {"BE:en", "BE:fr", "NL:nl"}

    fanout = json.loads((audit_dir / prompt_jobs.FANOUT_STATUS_FILE).read_text())
    assert fanout["batch_id"] == result["batch_id"]
    assert fanout["finalize_status"] == "pending"
    assert set(fanout["locales"]) == {"BE:en", "BE:fr", "NL:nl"}

    shards = list((audit_dir / prompt_jobs.FANOUT_SHARDS_DIR).glob("*.json"))
    assert {p.stem for p in shards} == {"BE_en", "BE_fr", "NL_nl"}
    merged = prompt_jobs.load_fanout_status(audit_dir)
    assert merged is not None
    assert set(merged["locales"]) == {"BE:en", "BE:fr", "NL:nl"}

    # Idempotent while running
    again = prompt_jobs.enqueue_prompt_job(audit_dir, mode="post_audit")
    assert again["already_running"] is True
    assert len(launches) == 3


def test_enqueue_single_locale_still_uses_fanout_path(
    monkeypatch, tmp_path: Path
) -> None:
    audit_dir = tmp_path / "acme"
    audit_dir.mkdir()
    (audit_dir / "onboarding_context.json").write_text(
        json.dumps(
            {
                "brand_name_used": "Acme",
                "geo_market_country": "United Kingdom",
                "geo_market_country_code": "GB",
                "products_and_services_rows": [
                    {"product_or_service": "Widget", "prompts": ["best widget"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(prompt_jobs.geo, "audit_dir_api_rel", lambda _p: "acme")
    monkeypatch.setattr(
        prompt_jobs,
        "_execute_prompt_job",
        lambda *, audit_id, request_id: f"exec/{request_id}",
    )

    result = prompt_jobs.enqueue_prompt_job(audit_dir, mode="live")
    assert result["fanout"] is True
    assert result["locale_count"] == 1
    assert len(result["launched"]) == 1
    manifest_files = list((audit_dir / prompt_jobs.PROMPT_JOB_REQUESTS_DIR).glob("*.json"))
    assert len(manifest_files) == 1
    manifest = json.loads(manifest_files[0].read_text())
    assert manifest["locale_key"] == "GB:en"
    assert manifest["batch_id"] == result["batch_id"]


def test_merge_locale_probe_artifacts_and_finalize_gate(
    monkeypatch, tmp_path: Path
) -> None:
    audit_dir = _seed_multi_locale_audit(tmp_path)
    monkeypatch.setattr(pp.geo, "audit_dir_api_rel", lambda _p: "samsung-com")

    def _write_locale(key: str, prompt: str) -> None:
        path = prompt_jobs.locale_probe_artifact_path(audit_dir, key)
        path.write_text(
            json.dumps(
                {
                    "locale": {"key": key},
                    "live_probe": {
                        "per_prompt": [{"prompt": prompt, "platforms": {}}],
                        "brand_match_tokens": ["Samsung"],
                    },
                    "source_prompts": ["best phone brand"],
                    "prompts_probed": [prompt],
                    "highlight_brand": "Samsung",
                    "highlight_comp_urls": [],
                    "highlight_comp_brands": [],
                }
            ),
            encoding="utf-8",
        )

    # Incomplete — gate closed
    _write_locale("BE:en", "prompt-en")
    assert pp.merge_locale_probe_artifacts(audit_dir, require_all=True) is None
    assert not (audit_dir / pp.LIVE_PROBE_FILE).exists()

    # Soft merge still writes partial
    soft = pp.merge_locale_probe_artifacts(audit_dir, require_all=False)
    assert soft is not None
    assert set(soft["locale_probes"]) == {"BE:en"}
    assert soft["missing_locales"] == ["BE:fr", "NL:nl"]

    _write_locale("BE:fr", "prompt-fr")
    _write_locale("NL:nl", "prompt-nl")
    merged = pp.merge_locale_probe_artifacts(audit_dir, require_all=True)
    assert merged is not None
    assert set(merged["locale_probes"]) == {"BE:en", "BE:fr", "NL:nl"}
    assert merged["default_locale_key"] == "BE:en"
    payload = json.loads((audit_dir / pp.LIVE_PROBE_FILE).read_text(encoding="utf-8"))
    assert set(payload["locale_probes"]) == {"BE:en", "BE:fr", "NL:nl"}
    assert payload["live_probe"]["per_prompt"][0]["prompt"] == "prompt-en"


def test_maybe_complete_locale_fanout_waits_then_finalizes(
    monkeypatch, tmp_path: Path
) -> None:
    audit_dir = _seed_multi_locale_audit(tmp_path)
    monkeypatch.setattr(pp.geo, "audit_dir_api_rel", lambda _p: "samsung-com")
    monkeypatch.setattr(prompt_jobs.geo, "audit_dir_api_rel", lambda _p: "samsung-com")

    batch_id = "batch123"
    locales = {
        "BE:en": {"status": "running", "request_id": "r1", "locale_index": 1},
        "BE:fr": {"status": "running", "request_id": "r2", "locale_index": 2},
        "NL:nl": {"status": "running", "request_id": "r3", "locale_index": 3},
    }
    prompt_jobs._write_json(
        prompt_jobs.fanout_status_path(audit_dir),
        {
            "batch_id": batch_id,
            "mode": "live",
            "report_mode": True,
            "completion": {},
            "locale_total": 3,
            "locales": locales,
            "finalize_status": "pending",
        },
    )
    (audit_dir / prompt_jobs.PROMPT_PENDING_FILE).write_text(
        json.dumps({"status": "running", "batch_id": batch_id, "fanout": True})
    )

    for key in ("BE:en", "BE:fr", "NL:nl"):
        prompt_jobs.locale_probe_artifact_path(audit_dir, key).write_text(
            json.dumps(
                {
                    "locale": {"key": key},
                    "live_probe": {
                        "per_prompt": [{"prompt": f"p-{key}", "platforms": {}}],
                    },
                    "highlight_brand": "Samsung",
                    "highlight_comp_urls": [],
                    "highlight_comp_brands": [],
                }
            ),
            encoding="utf-8",
        )

    side_calls: list[Path] = []
    monkeypatch.setattr(
        pp,
        "run_post_probe_side_effects",
        lambda d: side_calls.append(d) or {"sentiment": "done"},
    )
    monkeypatch.setattr(
        "api.probe_history.save_probe_to_history",
        lambda *a, **k: None,
    )

    first = pp.maybe_complete_locale_fanout(
        audit_dir, batch_id=batch_id, completing_locale_key="BE:en", request_id="r1"
    )
    assert first["action"] == "waiting"

    second = pp.maybe_complete_locale_fanout(
        audit_dir, batch_id=batch_id, completing_locale_key="BE:fr", request_id="r2"
    )
    assert second["action"] == "waiting"

    third = pp.maybe_complete_locale_fanout(
        audit_dir, batch_id=batch_id, completing_locale_key="NL:nl", request_id="r3"
    )
    assert third["action"] == "finalized"
    assert side_calls == [audit_dir]
    assert not (audit_dir / prompt_jobs.PROMPT_PENDING_FILE).exists()
    fanout = json.loads((audit_dir / prompt_jobs.FANOUT_STATUS_FILE).read_text())
    assert fanout["finalize_status"] == "finalized"
    assert (audit_dir / pp.LIVE_PROBE_FILE).is_file()


def test_maybe_complete_partial_failure_preserves_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    audit_dir = _seed_multi_locale_audit(tmp_path)
    monkeypatch.setattr(pp.geo, "audit_dir_api_rel", lambda _p: "samsung-com")

    batch_id = "batch-fail"
    prompt_jobs._write_json(
        prompt_jobs.fanout_status_path(audit_dir),
        {
            "batch_id": batch_id,
            "mode": "live",
            "locale_total": 2,
            "locales": {
                "BE:en": {"status": "completed", "request_id": "r1"},
                "BE:fr": {"status": "running", "request_id": "r2"},
            },
            "finalize_status": "pending",
        },
    )
    artifact = prompt_jobs.locale_probe_artifact_path(audit_dir, "BE:en")
    artifact.write_text(
        json.dumps(
            {
                "locale": {"key": "BE:en"},
                "live_probe": {"per_prompt": [{"prompt": "ok"}]},
                "highlight_brand": "Samsung",
            }
        ),
        encoding="utf-8",
    )

    result = pp.maybe_complete_locale_fanout(
        audit_dir,
        batch_id=batch_id,
        completing_locale_key="BE:fr",
        request_id="r2",
        locale_error="timeout",
    )
    assert result["action"] == "partial_failure"
    assert result["failed_locales"] == ["BE:fr"]
    assert artifact.is_file()  # successful locale kept
    fanout = prompt_jobs.load_fanout_status(audit_dir)
    assert fanout is not None
    assert fanout["finalize_status"] == "blocked_partial_failure"
    assert fanout["locales"]["BE:fr"]["status"] == "failed"


def test_history_mode_still_single_execution(monkeypatch, tmp_path: Path) -> None:
    audit_dir = tmp_path / "hist"
    audit_dir.mkdir()
    monkeypatch.setattr(prompt_jobs.geo, "audit_dir_api_rel", lambda _p: "hist")
    launches: list[str] = []
    monkeypatch.setattr(
        prompt_jobs,
        "_execute_prompt_job",
        lambda *, audit_id, request_id: launches.append(request_id) or f"e/{request_id}",
    )

    result = prompt_jobs.enqueue_prompt_job(audit_dir, mode="history", all_prompts=True)
    assert result["fanout"] is False
    assert len(launches) == 1
    assert result["locale_count"] == 1

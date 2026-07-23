from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from api import crawl_jobs


def test_accepted_operation_name_supports_library_metadata_variants() -> None:
    operation = SimpleNamespace(
        metadata=SimpleNamespace(),
        operation=SimpleNamespace(name="projects/p/locations/r/operations/123"),
    )

    assert crawl_jobs._accepted_operation_name(operation, "jobs/j", "request") == (
        "projects/p/locations/r/operations/123"
    )


def test_enqueue_crawl_job_persists_manifest_and_pending(monkeypatch, tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit_output" / "example-com"
    audit_dir.mkdir(parents=True)
    monkeypatch.setattr(crawl_jobs.geo, "audit_dir_api_rel", lambda _path: "example-com")
    monkeypatch.setattr(
        crawl_jobs,
        "_execute_crawl_job",
        lambda *, audit_id, request_id: f"executions/{audit_id}-{request_id}",
    )

    result = crawl_jobs.enqueue_crawl_job(
        audit_dir,
        mode="competitors_only",
        payload={},
    )

    assert result["status"] == "queued"
    assert result["execution"].startswith("executions/example-com-")
    pending = json.loads((audit_dir / crawl_jobs.COMPETITOR_CRAWL_PENDING_FILE).read_text())
    assert pending["request_id"] == result["request_id"]
    assert pending["execution"] == result["execution"]
    manifest = json.loads(
        (
            audit_dir
            / crawl_jobs.CRAWL_JOB_REQUESTS_DIR
            / f"{result['request_id']}.json"
        ).read_text()
    )
    assert manifest["mode"] == "competitors_only"
    assert manifest["status"] == "started"


def test_enqueue_crawl_job_reuses_active_request(monkeypatch, tmp_path: Path) -> None:
    audit_dir = tmp_path / "example-com"
    audit_dir.mkdir()
    monkeypatch.setattr(crawl_jobs.geo, "audit_dir_api_rel", lambda _path: "example-com")
    (audit_dir / crawl_jobs.FULL_AUDIT_PENDING_FILE).write_text(
        json.dumps(
            {
                "status": "running",
                "request_id": "existing",
                "execution": "executions/existing",
            }
        )
    )

    result = crawl_jobs.enqueue_crawl_job(audit_dir, mode="full_audit")

    assert result == {
        "status": "running",
        "audit_id": "example-com",
        "request_id": "existing",
        "execution": "executions/existing",
        "already_running": True,
        "mode": "full_audit",
    }


def test_launch_failure_clears_pending(monkeypatch, tmp_path: Path) -> None:
    audit_dir = tmp_path / "example-com"
    audit_dir.mkdir()
    monkeypatch.setattr(crawl_jobs.geo, "audit_dir_api_rel", lambda _path: "example-com")

    def fail(**_kwargs):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(crawl_jobs, "_execute_crawl_job", fail)

    try:
        crawl_jobs.enqueue_crawl_job(audit_dir, mode="full_audit")
    except RuntimeError as exc:
        assert "permission denied" in str(exc)
    else:
        raise AssertionError("enqueue should fail")

    assert not (audit_dir / crawl_jobs.FULL_AUDIT_PENDING_FILE).exists()
    manifests = list((audit_dir / crawl_jobs.CRAWL_JOB_REQUESTS_DIR).glob("*.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["status"] == "launch_failed"


def test_persist_ga4_adc_copies_into_audit_runtime(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    src = tmp_path / "temp-adc.json"
    src.write_text('{"type":"authorized_user"}')

    rel = crawl_jobs.persist_ga4_adc(audit_dir, src)

    assert rel == f".runtime/{crawl_jobs.GA4_ADC_FILENAME}"
    assert (audit_dir / rel).read_text() == '{"type":"authorized_user"}'


def test_crawl_jobs_enabled_respects_env(monkeypatch) -> None:
    monkeypatch.delenv("AUDIT_CRAWL_JOB_NAME", raising=False)
    monkeypatch.delenv("AUDIT_CRAWL_FORCE_LOCAL", raising=False)
    assert crawl_jobs.crawl_jobs_enabled() is False

    monkeypatch.setenv("AUDIT_CRAWL_JOB_NAME", "geo-audit-site-crawls")
    assert crawl_jobs.crawl_jobs_enabled() is True

    monkeypatch.setenv("AUDIT_CRAWL_FORCE_LOCAL", "1")
    assert crawl_jobs.crawl_jobs_enabled() is False

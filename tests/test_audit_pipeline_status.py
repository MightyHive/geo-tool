"""Tests for unified audit pipeline still-running / complete detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_pipeline_idle_when_no_pending_files(tmp_path: Path) -> None:
    from api.audit_pipeline_status import (
        audit_pipeline_is_running,
        get_audit_run_phase,
    )

    phase = get_audit_run_phase(tmp_path)
    assert phase["still_running"] is False
    assert phase["phase"] == "idle"
    assert audit_pipeline_is_running(tmp_path) is False


def test_pipeline_running_for_crawl_pending(tmp_path: Path) -> None:
    from api.audit_pipeline_status import get_audit_run_phase

    _write(
        tmp_path / "audit_crawl_pending.json",
        {"status": "running", "request_id": "r1"},
    )
    phase = get_audit_run_phase(tmp_path)
    assert phase["still_running"] is True
    assert phase["phase"] == "crawl"
    assert phase["components"]["crawl"] is True


def test_pipeline_running_for_prompt_pending(tmp_path: Path) -> None:
    from api.audit_pipeline_status import get_audit_run_phase

    _write(
        tmp_path / "prompt_performance_probe_pending.json",
        {"status": "running", "batch_id": "b1", "fanout": True},
    )
    # Fan-out not idle → probes active
    _write(
        tmp_path / "prompt_probe_fanout.json",
        {
            "batch_id": "b1",
            "finalize_status": "pending",
            "locale_total": 1,
            "locales": {"BE:en": {"status": "running"}},
        },
    )
    phase = get_audit_run_phase(tmp_path)
    assert phase["still_running"] is True
    assert phase["components"]["prompt_probes"] is True
    assert phase["phase"] == "prompt_probes"


def test_pipeline_running_for_content_quality_brand(tmp_path: Path) -> None:
    from api.audit_pipeline_status import get_audit_run_phase

    _write(
        tmp_path / "content_quality_gemini_pending.json",
        {"status": "running", "request_id": "cq1"},
    )
    phase = get_audit_run_phase(tmp_path)
    assert phase["still_running"] is True
    assert phase["phase"] == "content_quality"
    assert phase["components"]["content_quality"] is True


def test_pipeline_running_for_competitor_content_quality(tmp_path: Path) -> None:
    from api.audit_pipeline_status import get_audit_run_phase

    comp = tmp_path / "competitors" / "example.com"
    _write(comp / "audit_summary.json", {"base_url": "https://example.com", "audit_label": "competitor"})
    _write(
        comp / "content_quality_gemini_pending.json",
        {"status": "queued", "request_id": "cq-comp"},
    )
    phase = get_audit_run_phase(tmp_path)
    assert phase["still_running"] is True
    assert phase["components"]["content_quality"] is True


def test_pipeline_running_for_sentiment_pending(tmp_path: Path) -> None:
    from api.audit_pipeline_status import get_audit_run_phase

    _write(
        tmp_path / "prompt_sentiment_pending.json",
        {"status": "running", "request_id": "s1"},
    )
    phase = get_audit_run_phase(tmp_path)
    assert phase["still_running"] is True
    assert phase["phase"] == "sentiment"
    assert phase["components"]["sentiment"] is True


def test_pipeline_running_for_competitor_crawl_status(tmp_path: Path) -> None:
    from api.audit_pipeline_status import get_audit_run_phase

    _write(
        tmp_path / "competitor_crawl_status.json",
        {"status": "running", "detail": "Crawling…"},
    )
    phase = get_audit_run_phase(tmp_path)
    assert phase["still_running"] is True
    assert phase["phase"] == "competitor_crawl"


def test_phase_priority_prefers_crawl_over_cq(tmp_path: Path) -> None:
    from api.audit_pipeline_status import get_audit_run_phase

    _write(tmp_path / "audit_crawl_pending.json", {"status": "running"})
    _write(tmp_path / "content_quality_gemini_pending.json", {"status": "running"})
    phase = get_audit_run_phase(tmp_path)
    assert phase["phase"] == "crawl"


def test_attach_pipeline_status_keeps_done_as_running_while_cq_active(
    tmp_path: Path,
) -> None:
    from api.audit_pipeline_status import attach_pipeline_status

    _write(
        tmp_path / "content_quality_gemini_pending.json",
        {"status": "running", "request_id": "cq1"},
    )
    out = attach_pipeline_status(
        {
            "status": "done",
            "detail": "Audit complete",
            "percent": 100,
            "current_step": "finish",
        },
        tmp_path,
    )
    assert out["status"] == "running"
    assert out["still_running"] is True
    assert out["pipeline_phase"] == "content_quality"
    assert out["percent"] == 99
    assert "Content quality" in (out.get("detail") or "")


def test_attach_pipeline_status_preserves_error(tmp_path: Path) -> None:
    from api.audit_pipeline_status import attach_pipeline_status

    _write(
        tmp_path / "content_quality_gemini_pending.json",
        {"status": "running"},
    )
    out = attach_pipeline_status(
        {"status": "error", "detail": "failed", "error": "boom"},
        tmp_path,
    )
    assert out["status"] == "error"
    assert out["still_running"] is True


def test_include_run_status_false_ignores_orphan_run_file(tmp_path: Path) -> None:
    from api.audit_pipeline_status import audit_pipeline_is_running

    _write(
        tmp_path / "audit_run_status.json",
        {"status": "running", "detail": "orphaned"},
    )
    assert audit_pipeline_is_running(tmp_path, include_run_status=True) is True
    assert audit_pipeline_is_running(tmp_path, include_run_status=False) is False


def test_list_primary_audits_exposes_still_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from api import geo_services as geo
    from api.audit_pipeline_status import get_audit_run_phase

    audit = tmp_path / "www.example.com_abc"
    audit.mkdir()
    _write(
        audit / "audit_summary.json",
        {
            "audit_label": "primary",
            "base_url": "https://www.example.com",
            "overall_score": 70,
        },
    )
    _write(
        audit / "content_quality_gemini_pending.json",
        {"status": "running", "request_id": "cq1"},
    )
    monkeypatch.setattr(geo, "audit_output_base", lambda: tmp_path)

    rows = geo.list_primary_audits()
    assert len(rows) == 1
    assert rows[0]["still_running"] is True
    assert rows[0]["pipeline_phase"] == get_audit_run_phase(audit)["phase"]


def test_read_run_status_not_done_while_sentiment_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api import audit_runner

    monkeypatch.setattr(
        "api.geo_services.audit_dir_api_rel",
        lambda d: str(d.name),
    )
    _write(
        tmp_path / "audit_run_status.json",
        {
            "status": "done",
            "detail": "Audit complete",
            "percent": 100,
            "audit_dir": tmp_path.name,
        },
    )
    _write(
        tmp_path / "prompt_sentiment_pending.json",
        {"status": "queued", "request_id": "s1"},
    )
    status = audit_runner.read_run_status(tmp_path)
    assert status is not None
    assert status["status"] == "running"
    assert status["still_running"] is True
    assert status["pipeline_phase"] == "sentiment"

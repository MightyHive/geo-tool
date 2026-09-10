"""Tests for scheduled daily/monthly exclusion handling and helpers."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from api import probe_history
from api.probe_history import (
    _audit_is_excluded,
    parse_excluded_audits,
)


def test_parse_excluded_audits_normalizes_folder_and_path() -> None:
    result = parse_excluded_audits(
        "www.example.com_abc, audit_output/other.com_xyz",
        "  ",
        None,
    )
    assert result == {
        "www.example.com_abc",
        "audit_output/other.com_xyz",
        "other.com_xyz",
    }


def test_audit_is_excluded_matches_id_or_dir() -> None:
    excluded = parse_excluded_audits("www.example.com_abc")
    assert _audit_is_excluded(
        {"id": "www.example.com_abc", "audit_dir": "audit_output/www.example.com_abc"},
        excluded,
    )
    assert _audit_is_excluded(
        {"id": "other", "audit_dir": "audit_output/www.example.com_abc"},
        excluded,
    )
    assert not _audit_is_excluded(
        {"id": "other", "audit_dir": "audit_output/other"},
        excluded,
    )


def test_scheduled_daily_rerun_skips_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    audits = [
        {"id": "keep.com_1", "audit_dir": "audit_output/keep.com_1"},
        {"id": "skip.com_2", "audit_dir": "audit_output/skip.com_2"},
    ]
    monkeypatch.setattr(probe_history.geo, "list_primary_audits", lambda: audits)
    monkeypatch.setattr(
        probe_history.geo,
        "resolve_audit_dir",
        lambda rel: Path("/tmp") / Path(rel).name,
    )
    monkeypatch.setattr(probe_history, "_should_rerun_today", lambda _d: True)
    enqueued: list[str] = []

    def _enqueue(audit_dir: Path, **_kwargs: object) -> dict[str, str]:
        enqueued.append(audit_dir.name)
        return {"request_id": "r1"}

    monkeypatch.setattr("api.prompt_jobs.enqueue_prompt_job", _enqueue)

    result = probe_history.scheduled_daily_rerun(excluded_audits="skip.com_2")
    assert result["status"] == "ok"
    assert result["queued"] == 1
    assert result["skipped"] == 1
    assert result["queued_ids"] == ["audit_output/keep.com_1"]
    assert enqueued == ["keep.com_1"]


def test_scheduled_monthly_crawl_skips_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    audits = [
        {"id": "keep.com_1", "audit_dir": "audit_output/keep.com_1"},
        {"id": "skip.com_2", "audit_dir": "audit_output/skip.com_2"},
    ]
    monkeypatch.setattr(probe_history.geo, "list_primary_audits", lambda: audits)
    monkeypatch.setattr(
        probe_history.geo,
        "resolve_audit_dir",
        lambda rel: Path("/tmp") / Path(rel).name,
    )
    monkeypatch.setattr(
        "api.automated_refresh.is_automated_tracking_eligible",
        lambda _d: True,
    )
    monkeypatch.setattr(
        "api.automated_refresh.rebuild_full_audit_crawl_payload",
        lambda _d: {"primary": "https://keep.com", "follow_on_competitor_crawl": False},
    )
    monkeypatch.setattr("api.crawl_jobs.crawl_jobs_enabled", lambda: True)
    enqueued: list[str] = []

    def _enqueue(audit_dir: Path, **_kwargs: object) -> dict[str, object]:
        enqueued.append(audit_dir.name)
        return {"request_id": "c1", "already_running": False}

    monkeypatch.setattr("api.crawl_jobs.enqueue_crawl_job", _enqueue)

    result = probe_history.scheduled_monthly_crawl(
        excluded_audits="audit_output/skip.com_2"
    )
    assert result["status"] == "ok"
    assert result["queued"] == 1
    assert result["skipped"] == 1
    assert result["queued_ids"][0]["audit_id"] == "audit_output/keep.com_1"
    assert enqueued == ["keep.com_1"]


def test_runner_forwards_excluded_to_both_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jobs.scheduler_runner import run_job

    seen: dict[str, str | None] = {}

    def _daily(excluded_audits: str | None = None) -> dict[str, object]:
        seen["daily"] = excluded_audits
        return {"status": "ok", "queued": 0, "skipped": 0}

    def _monthly(excluded_audits: str | None = None) -> dict[str, object]:
        seen["monthly"] = excluded_audits
        return {"status": "ok", "queued": 0, "skipped": 0}

    monkeypatch.setattr(run_job.probe_history, "scheduled_daily_rerun", _daily)
    monkeypatch.setattr(run_job.probe_history, "scheduled_monthly_crawl", _monthly)
    monkeypatch.setenv("EXCLUDED_AUDITS", "skip.com_2")
    monkeypatch.setenv("SCHEDULER_RUNNER_ACTION", "daily-rerun")
    assert run_job.main() == 0
    assert seen["daily"] == "skip.com_2"

    monkeypatch.setenv("SCHEDULER_RUNNER_ACTION", "monthly-crawl")
    assert run_job.main() == 0
    assert seen["monthly"] == "skip.com_2"


def _load_scheduler_fn(module_name: str):
    """Import functions/scheduler_runner/main.py with Cloud deps stubbed."""
    ff = types.ModuleType("functions_framework")
    ff.cloud_event = lambda fn: fn
    sys.modules["functions_framework"] = ff

    ce = types.ModuleType("cloudevents")
    ce_http = types.ModuleType("cloudevents.http")
    ce_http.CloudEvent = object
    sys.modules["cloudevents"] = ce
    sys.modules["cloudevents.http"] = ce_http

    class RunJobRequest:
        def __init__(self, name=None, overrides=None):
            self.name = name
            self.overrides = overrides

    run_v2 = types.ModuleType("google.cloud.run_v2")
    run_v2.RunJobRequest = RunJobRequest
    run_v2.JobsClient = object
    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    sys.modules.setdefault("google", google)
    sys.modules.setdefault("google.cloud", cloud)
    sys.modules["google.cloud.run_v2"] = run_v2

    mod_path = (
        Path(__file__).resolve().parents[1] / "functions" / "scheduler_runner" / "main.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, mod_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_launcher_routes_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_scheduler_fn("scheduler_runner_fn")
    captured: dict[str, object] = {}

    class _FakeOp:
        metadata = MagicMock(name="ops/1")

    class _FakeClient:
        def run_job(self, request: object) -> _FakeOp:
            captured["request"] = request
            return _FakeOp()

    monkeypatch.setattr(mod, "_job_client", lambda: _FakeClient())
    monkeypatch.setenv("SCHEDULER_JOB_PROJECT", "geo-tool-emea-ds")
    monkeypatch.setenv("SCHEDULER_JOB_REGION", "europe-west1")
    monkeypatch.setenv("DEV_SCHEDULER_JOB_NAME", "geo-audit-scheduler-runner-dev")
    monkeypatch.setenv("STAGING_SCHEDULER_JOB_NAME", "geo-audit-scheduler-runner-staging")

    payload = {"action": "daily-rerun", "environment": "staging", "excluded_audits": ""}
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
    event = MagicMock()
    event.data = {"message": {"data": encoded}}
    mod.pubsub_handler(event)

    request = captured["request"]
    assert request.name.endswith("/jobs/geo-audit-scheduler-runner-staging")
    env_map = {
        e["name"]: e["value"]
        for e in request.overrides["container_overrides"][0]["env"]
    }
    assert env_map["SCHEDULER_RUNNER_ACTION"] == "daily-rerun"
    assert env_map["EXCLUDED_AUDITS"] == ""
    assert "PROMPT_PROBE_JOB_NAME" not in env_map
    assert "BUCKET" not in env_map


def test_launcher_rejects_invalid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_scheduler_fn("scheduler_runner_fn2")
    monkeypatch.setenv("SCHEDULER_JOB_PROJECT", "geo-tool-emea-ds")
    monkeypatch.setenv("SCHEDULER_JOB_REGION", "europe-west1")

    with pytest.raises(RuntimeError, match="Unsupported or missing environment"):
        mod._run_scheduler_job("daily-rerun", {"environment": "prod"})

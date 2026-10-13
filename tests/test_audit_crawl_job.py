from __future__ import annotations

import json
import logging
import signal

import pytest

from jobs.audit_crawl import run_job


def test_graceful_timeout_raises_before_platform_kill() -> None:
    if not hasattr(signal, "SIGALRM"):
        pytest.skip("SIGALRM is unavailable on this platform")

    with pytest.raises(TimeoutError, match="internal 60-second runtime limit"):
        with run_job._graceful_timeout(60):
            signal.raise_signal(signal.SIGALRM)


def test_run_full_audit_propagates_persisted_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from api import audit_runner

    def failed_run(**kwargs) -> None:
        audit_dir = kwargs["audit_dir"]
        (audit_dir / "audit_run_status.json").write_text(
            json.dumps(
                {
                    "status": "error",
                    "error": "competitor crawl timed out",
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(audit_runner, "_run_audit_job", failed_run)

    with pytest.raises(RuntimeError, match="competitor crawl timed out"):
        run_job._run_full_audit(tmp_path, {})


def test_audit_failure_retains_progress_and_logs_subprocess_output(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from api import audit_runner

    statuses: list[dict] = []
    captured_kwargs: dict = {}

    def pipeline_logs(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        yield "Running: python backend/crawl-site.py https://www.bmw.co.uk\n"
        yield "Competitor 1 (https://www.mercedes-benz.co.uk) …\n"
        raise TimeoutError("Crawl job exceeded the internal runtime limit")

    monkeypatch.setattr(audit_runner.geo, "iter_pipeline_logs", pipeline_logs)
    monkeypatch.setattr(
        audit_runner,
        "_write_run_status",
        lambda _audit_dir, payload: statuses.append(dict(payload)),
    )
    caplog.set_level(logging.INFO, logger="api.audit_runner")

    audit_runner._run_audit_job(
        audit_dir=tmp_path,
        primary="https://www.bmw.co.uk",
        competitors=["https://www.mercedes-benz.co.uk"],
        body_dict={},
        ga4_prop=None,
        ga4_ch=None,
        ga4_cred_path=None,
        owner_email=None,
        notification_email=None,
        stream_progress=True,
    )

    failure = statuses[-1]
    assert failure["status"] == "error"
    assert failure["current_step"] == "competitors"
    assert failure["percent"] > 0
    assert "runtime limit" in failure["detail"]
    assert captured_kwargs["target_audit_dir"] == tmp_path
    assert "Audit pipeline" in caplog.text
    assert "Competitor 1" in caplog.text

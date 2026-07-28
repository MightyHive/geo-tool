from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from api import prompt_jobs


def test_accepted_operation_name_supports_library_metadata_variants() -> None:
    operation = SimpleNamespace(
        metadata=SimpleNamespace(),
        operation=SimpleNamespace(name="projects/p/locations/r/operations/123"),
    )

    assert prompt_jobs._accepted_operation_name(operation, "jobs/j", "request") == (
        "projects/p/locations/r/operations/123"
    )


def test_enqueue_prompt_job_persists_manifest_and_pending(monkeypatch, tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit_output" / "example-com"
    audit_dir.mkdir(parents=True)
    (audit_dir / "onboarding_context.json").write_text(
        json.dumps(
            {
                "brand_name_used": "Example",
                "geo_market_country": "United Kingdom",
                "geo_market_country_code": "GB",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(prompt_jobs.geo, "audit_dir_api_rel", lambda _path: "example-com")
    monkeypatch.setattr(
        prompt_jobs,
        "_execute_prompt_job",
        lambda *, audit_id, request_id: f"executions/{audit_id}-{request_id}",
    )

    result = prompt_jobs.enqueue_prompt_job(
        audit_dir,
        mode="live",
        report_mode=True,
    )

    assert result["status"] == "queued"
    assert result["fanout"] is True
    assert result["execution"].startswith("executions/example-com-")
    pending = json.loads((audit_dir / prompt_jobs.PROMPT_PENDING_FILE).read_text())
    assert pending["batch_id"] == result["batch_id"]
    assert pending["fanout"] is True
    manifests = list((audit_dir / prompt_jobs.PROMPT_JOB_REQUESTS_DIR).glob("*.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["mode"] == "live"
    assert manifest["status"] == "started"
    assert manifest["locale_key"] == "GB:en"


def test_enqueue_prompt_job_reuses_active_request(monkeypatch, tmp_path: Path) -> None:
    audit_dir = tmp_path / "example-com"
    audit_dir.mkdir()
    monkeypatch.setattr(prompt_jobs.geo, "audit_dir_api_rel", lambda _path: "example-com")
    (audit_dir / prompt_jobs.PROMPT_PENDING_FILE).write_text(
        json.dumps(
            {
                "status": "running",
                "request_id": "existing",
                "execution": "executions/existing",
            }
        )
    )

    result = prompt_jobs.enqueue_prompt_job(audit_dir, mode="live")

    assert result["status"] == "running"
    assert result["audit_id"] == "example-com"
    assert result["request_id"] == "existing"
    assert result["execution"] == "executions/existing"
    assert result["already_running"] is True


def test_launch_failure_clears_pending(monkeypatch, tmp_path: Path) -> None:
    audit_dir = tmp_path / "example-com"
    audit_dir.mkdir()
    monkeypatch.setattr(prompt_jobs.geo, "audit_dir_api_rel", lambda _path: "example-com")

    def fail(**_kwargs):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(prompt_jobs, "_execute_prompt_job", fail)

    try:
        prompt_jobs.enqueue_prompt_job(audit_dir, mode="history")
    except RuntimeError as exc:
        assert "permission denied" in str(exc)
    else:
        raise AssertionError("enqueue should fail")

    assert not (audit_dir / prompt_jobs.PROMPT_PENDING_FILE).exists()
    manifests = list((audit_dir / prompt_jobs.PROMPT_JOB_REQUESTS_DIR).glob("*.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["status"] == "launch_failed"


def test_write_json_retries_estale(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "status.json"
    calls = {"n": 0}
    real_write_text = Path.write_text

    def flaky_write_text(self: Path, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(116, "Stale file handle")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)
    monkeypatch.setattr(prompt_jobs.time, "sleep", lambda _s: None)

    prompt_jobs._write_json(target, {"ok": True})
    assert json.loads(target.read_text()) == {"ok": True}
    assert calls["n"] == 2
    assert not list(tmp_path.glob(".status.json.*.tmp"))


def test_write_json_uses_unique_temp_names(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "shared.json"
    seen: list[str] = []
    real_replace = os.replace

    def capture_replace(src, dst):
        seen.append(Path(src).name)
        return real_replace(src, dst)

    monkeypatch.setattr(prompt_jobs.os, "replace", capture_replace)
    prompt_jobs._write_json(target, {"a": 1})
    prompt_jobs._write_json(target, {"a": 2})
    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert all(name.startswith(".shared.json.") and name.endswith(".tmp") for name in seen)


def test_read_json_retries_estale(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "status.json"
    target.write_text('{"ok": true}\n', encoding="utf-8")
    calls = {"n": 0}
    real_read_text = Path.read_text

    def flaky_read_text(self: Path, *args, **kwargs):
        if self == target:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(116, "Stale file handle")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    monkeypatch.setattr(prompt_jobs.time, "sleep", lambda _s: None)

    assert prompt_jobs._read_json(target) == {"ok": True}
    assert calls["n"] == 2


def test_update_fanout_locale_progress_writes_per_locale_shards(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    path = prompt_jobs.fanout_status_path(audit_dir)
    prompt_jobs._write_json(
        path,
        {
            "batch_id": "b1",
            "locales": {
                "BE:en": {"status": "queued", "request_id": "r1", "completed_calls": 0},
                "BE:fr": {"status": "queued", "request_id": "r2", "completed_calls": 0},
            },
        },
    )

    errors: list[BaseException] = []

    def worker(key: str, request_id: str, completed: int) -> None:
        try:
            prompt_jobs.update_fanout_locale_progress(
                audit_dir,
                locale_key=key,
                status="running",
                completed_calls=completed,
                planned_calls=10,
                request_id=request_id,
            )
        except BaseException as exc:  # noqa: BLE001 — collect for assert
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("BE:en", "r1", 3)),
        threading.Thread(target=worker, args=("BE:fr", "r2", 7)),
        threading.Thread(target=worker, args=("BE:en", "r1", 5)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # Shared base file must not be rewritten by progress ticks.
    base = json.loads(path.read_text())
    assert base["locales"]["BE:en"]["status"] == "queued"
    assert base["locales"]["BE:fr"]["status"] == "queued"

    en_shard = prompt_jobs.fanout_locale_shard_path(audit_dir, "BE:en")
    fr_shard = prompt_jobs.fanout_locale_shard_path(audit_dir, "BE:fr")
    assert en_shard.is_file()
    assert fr_shard.is_file()

    data = prompt_jobs.load_fanout_status(audit_dir)
    assert data is not None
    assert data["locales"]["BE:en"]["status"] == "running"
    assert data["locales"]["BE:fr"]["status"] == "running"
    assert data["locales"]["BE:en"]["completed_calls"] in {3, 5}
    assert data["locales"]["BE:fr"]["completed_calls"] == 7
    assert data["locales"]["BE:en"]["planned_calls"] == 10
    assert data["locales"]["BE:fr"]["planned_calls"] == 10


def test_load_fanout_status_merges_shards_over_base(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    prompt_jobs._write_json(
        prompt_jobs.fanout_status_path(audit_dir),
        {
            "batch_id": "b1",
            "finalize_status": "pending",
            "locales": {
                "BE:en": {"status": "queued", "request_id": "r1", "completed_calls": 0},
                "NL:nl": {"status": "queued", "request_id": "r2", "completed_calls": 0},
            },
        },
    )
    prompt_jobs._write_fanout_locale_shard(
        audit_dir,
        "BE:en",
        {"status": "completed", "request_id": "r1", "completed_calls": 12, "planned_calls": 12},
    )
    merged = prompt_jobs.load_fanout_status(audit_dir)
    assert merged is not None
    assert merged["finalize_status"] == "pending"
    assert merged["locales"]["BE:en"]["status"] == "completed"
    assert merged["locales"]["BE:en"]["completed_calls"] == 12
    assert merged["locales"]["NL:nl"]["status"] == "queued"


def test_touch_fanout_pending_running_preserves_locales(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    pending = audit_dir / prompt_jobs.PROMPT_PENDING_FILE
    prompt_jobs._write_json(
        pending,
        {
            "status": "queued",
            "batch_id": "batch-1",
            "fanout": True,
            "locales": {"BE:en": {"status": "queued"}, "BE:fr": {"status": "queued"}},
        },
    )
    prompt_jobs.touch_fanout_pending_running(audit_dir, mode="live", batch_id="batch-1")
    data = json.loads(pending.read_text())
    assert data["status"] == "running"
    assert data["batch_id"] == "batch-1"
    assert set(data["locales"]) == {"BE:en", "BE:fr"}


def test_touch_fanout_pending_running_skips_when_already_running(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    pending = audit_dir / prompt_jobs.PROMPT_PENDING_FILE
    prompt_jobs._write_json(
        pending,
        {
            "status": "running",
            "batch_id": "batch-1",
            "fanout": True,
            "locales": {"BE:en": {"status": "queued"}},
        },
    )
    mtime_before = pending.stat().st_mtime
    prompt_jobs.touch_fanout_pending_running(audit_dir, mode="live", batch_id="batch-1")
    assert pending.stat().st_mtime == mtime_before


def test_claim_fanout_finalize_only_one_winner(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    prompt_jobs._write_json(
        prompt_jobs.fanout_status_path(audit_dir),
        {"batch_id": "b1", "finalize_status": "pending", "locales": {}},
    )
    first = prompt_jobs.claim_fanout_finalize(audit_dir, batch_id="b1", claim="claim-a")
    second = prompt_jobs.claim_fanout_finalize(audit_dir, batch_id="b1", claim="claim-b")
    assert first is not None
    assert first["finalize_claim"] == "claim-a"
    assert second is None
    data = json.loads(prompt_jobs.fanout_status_path(audit_dir).read_text())
    assert data["finalize_claim"] == "claim-a"
    assert data["finalize_status"] == "finalizing"


def test_update_json_clears_stale_lock(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "status.json"
    prompt_jobs._write_json(target, {"v": 1})
    lock_path = target.with_name(target.name + ".lock")
    lock_path.write_text("dead:0\n", encoding="utf-8")
    # Pretend the lock is older than the stale threshold.
    old = time.time() - (prompt_jobs._LOCK_STALE_SECONDS + 5)
    os.utime(lock_path, (old, old))
    monkeypatch.setattr(prompt_jobs.time, "sleep", lambda _s: None)

    def bump(data: dict) -> dict:
        data["v"] = int(data.get("v") or 0) + 1
        return data

    updated = prompt_jobs._update_json(target, bump)
    assert updated is not None
    assert updated["v"] == 2
    assert not lock_path.exists()


def test_update_json_retries_after_lock_timeout(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "status.json"
    prompt_jobs._write_json(target, {"v": 1})
    calls = {"n": 0}
    real_lock = prompt_jobs._exclusive_file_lock

    @contextmanager
    def flaky_lock(lock_path, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError(f"Timed out waiting for lock {lock_path}")
        with real_lock(lock_path, **kwargs):
            yield

    monkeypatch.setattr(prompt_jobs, "_exclusive_file_lock", flaky_lock)
    monkeypatch.setattr(prompt_jobs.time, "sleep", lambda _s: None)

    def bump(data: dict) -> dict:
        data["v"] = int(data.get("v") or 0) + 1
        return data

    updated = prompt_jobs._update_json(target, bump)
    assert updated is not None
    assert updated["v"] == 2
    assert calls["n"] == 2


def test_aggregate_fanout_progress_complete_when_all_locales_done() -> None:
    summary = prompt_jobs.aggregate_fanout_progress(
        {
            "locale_total": 2,
            "finalize_status": "finalized",
            "locales": {
                "BE:en": {
                    "status": "completed",
                    "completed_calls": 10,
                    "planned_calls": 10,
                    "locale_index": 1,
                    "locale_label": "BE:en",
                },
                "BE:fr": {
                    "status": "completed",
                    "completed_calls": 10,
                    "planned_calls": 10,
                    "locale_index": 2,
                    "locale_label": "BE:fr",
                },
            },
        }
    )
    assert summary["status"] == "complete"
    assert summary["locales_done"] == 2
    # Parallel markets: wall-clock planned/completed use max across locales.
    assert summary["completed_calls"] == 10
    assert summary["planned_calls"] == 10
    assert summary["eta_seconds"] is None
    assert summary["eta_total_seconds"] is None


def test_aggregate_fanout_progress_running_while_locale_in_flight() -> None:
    summary = prompt_jobs.aggregate_fanout_progress(
        {
            "locale_total": 2,
            "finalize_status": "pending",
            "locales": {
                "BE:en": {"status": "completed", "completed_calls": 4, "planned_calls": 4},
                "BE:fr": {
                    "status": "running",
                    "completed_calls": 1,
                    "planned_calls": 4,
                    "locale_label": "BE:fr",
                    "locale_index": 2,
                },
            },
        }
    )
    assert summary["status"] == "running"
    assert summary["locale_label"] == "BE:fr"
    # Slowest market has 3 remaining of 4 planned → wall-clock 1/4.
    assert summary["planned_calls"] == 4
    assert summary["completed_calls"] == 1
    assert summary["eta_seconds"] == 21  # 3 × 7
    assert summary["eta_total_seconds"] == 28


def test_fanout_batch_is_idle_for_terminal_and_finalize_states() -> None:
    assert prompt_jobs.fanout_batch_is_idle(None) is False
    assert (
        prompt_jobs.fanout_batch_is_idle(
            {
                "finalize_status": "pending",
                "locales": {
                    "BE:en": {"status": "running"},
                    "BE:fr": {"status": "completed"},
                },
            }
        )
        is False
    )
    assert (
        prompt_jobs.fanout_batch_is_idle(
            {
                "finalize_status": "pending",
                "locales": {
                    "BE:en": {"status": "completed"},
                    "BE:fr": {"status": "failed"},
                },
            }
        )
        is True
    )
    assert (
        prompt_jobs.fanout_batch_is_idle({"finalize_status": "finalized", "locales": {}})
        is True
    )
    assert prompt_jobs.fanout_batch_is_idle({"finalize_status": "pending", "locales": {}}) is False
    # Hung finalize must not keep the batch "busy" once every locale finished.
    assert (
        prompt_jobs.fanout_batch_is_idle(
            {
                "finalize_status": "finalizing",
                "locales": {
                    "BE:en": {"status": "completed"},
                    "LU:fr": {"status": "completed"},
                },
            }
        )
        is True
    )


def test_live_probe_is_pending_clears_stale_running_when_fanout_idle(
    tmp_path: Path,
) -> None:
    from api import prompt_performance as pp

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / pp.PROBE_PENDING_FILE).write_text(
        json.dumps({"status": "running", "batch_id": "b1", "fanout": True}),
        encoding="utf-8",
    )
    prompt_jobs._write_json(
        prompt_jobs.fanout_status_path(audit_dir),
        {
            "batch_id": "b1",
            "finalize_status": "finalized",
            "locales": {
                "BE:en": {"status": "completed"},
                "LU:fr": {"status": "completed"},
            },
        },
    )

    assert pp.live_probe_is_pending(audit_dir) is False
    assert not (audit_dir / pp.PROBE_PENDING_FILE).is_file()


def test_live_probe_is_pending_clears_when_locales_terminal_incomplete_calls(
    tmp_path: Path,
) -> None:
    """Pending says running + incomplete 387/480 counters, but locales are done."""
    from api import prompt_performance as pp

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / pp.PROBE_PENDING_FILE).write_text(
        json.dumps({"status": "running", "batch_id": "b1", "fanout": True}),
        encoding="utf-8",
    )
    locales = {
        f"L{i}:en": {
            "status": "completed",
            "completed_calls": 387 if i == 0 else 480,
            "planned_calls": 480,
            "locale_index": i + 1,
        }
        for i in range(7)
    }
    prompt_jobs._write_json(
        prompt_jobs.fanout_status_path(audit_dir),
        {
            "batch_id": "b1",
            "finalize_status": "pending",
            "locale_total": 7,
            "locales": locales,
        },
    )

    assert prompt_jobs.fanout_batch_is_idle(
        prompt_jobs.load_fanout_status(audit_dir)
    )
    summary = prompt_jobs.aggregate_fanout_progress(
        prompt_jobs.load_fanout_status(audit_dir) or {}
    )
    assert summary["status"] == "complete"
    assert summary["completed_calls"] == summary["planned_calls"] == 480
    assert summary["eta_seconds"] is None

    assert pp.live_probe_is_pending(audit_dir) is False
    assert not (audit_dir / pp.PROBE_PENDING_FILE).is_file()


def test_live_probe_is_pending_clears_orphaned_running_when_artifact_exists(
    tmp_path: Path,
) -> None:
    from api import prompt_performance as pp

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / pp.PROBE_PENDING_FILE).write_text(
        json.dumps({"status": "running", "batch_id": "b1", "fanout": True}),
        encoding="utf-8",
    )
    prompt_jobs._write_json(
        prompt_jobs.fanout_status_path(audit_dir),
        {
            "batch_id": "b1",
            "finalize_status": "pending",
            "locales": {
                "BE:en": {
                    "status": "running",
                    "completed_calls": 387,
                    "planned_calls": 480,
                },
            },
        },
    )
    artifact = prompt_jobs.locale_probe_artifact_path(audit_dir, "BE:en")
    prompt_jobs._write_json(
        artifact,
        {"locale": {"key": "BE:en"}, "live_probe": {"per_prompt": []}},
    )

    assert pp.live_probe_is_pending(audit_dir) is False
    assert not (audit_dir / pp.PROBE_PENDING_FILE).is_file()


def test_live_probe_is_pending_stays_true_while_locale_running(tmp_path: Path) -> None:
    from api import prompt_performance as pp

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / pp.PROBE_PENDING_FILE).write_text(
        json.dumps({"status": "running", "batch_id": "b1", "fanout": True}),
        encoding="utf-8",
    )
    prompt_jobs._write_json(
        prompt_jobs.fanout_status_path(audit_dir),
        {
            "batch_id": "b1",
            "finalize_status": "pending",
            "locales": {
                "BE:en": {"status": "completed"},
                "LU:fr": {"status": "running"},
            },
        },
    )

    assert pp.live_probe_is_pending(audit_dir) is True
    assert (audit_dir / pp.PROBE_PENDING_FILE).is_file()

from __future__ import annotations

import threading
import time

import api.prompt_performance_metrics as metrics


def _setup(tmp_path, monkeypatch, existing):
    (tmp_path / metrics.LIVE_PROBE_FILE).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(metrics, "read_metrics_file", lambda d: existing)
    monkeypatch.setattr(metrics, "metrics_are_fresh", lambda d, m: False)
    metrics._last_background_rebuild.clear()


def test_stale_metrics_are_served_immediately_and_rebuilt_in_background(tmp_path, monkeypatch):
    stale = {"version": metrics.METRICS_VERSION, "overall_metrics": {"score": 1}}
    _setup(tmp_path, monkeypatch, stale)
    built = threading.Event()
    release = threading.Event()

    def slow_build(_d):
        release.wait(2)
        return {"ctx": True}

    def persist(_d, _ctx):
        built.set()
        return {"version": metrics.METRICS_VERSION}

    monkeypatch.setattr(metrics, "persist_metrics_from_full_context", persist)

    start = time.monotonic()
    result = metrics.ensure_persisted_metrics(tmp_path, build_full_context=slow_build)
    assert result is stale
    assert time.monotonic() - start < 0.5  # did not wait for the rebuild
    release.set()
    assert built.wait(2)


def test_background_rebuild_has_cooldown(tmp_path, monkeypatch):
    stale = {"version": metrics.METRICS_VERSION, "locales": {}}
    _setup(tmp_path, monkeypatch, stale)
    calls = []
    monkeypatch.setattr(metrics, "persist_metrics_from_full_context", lambda d, c: {})
    started = [
        metrics.schedule_background_metrics_rebuild(tmp_path, lambda d: calls.append(1) or {})
        for _ in range(3)
    ]
    assert started == [True, False, False]


def test_missing_metrics_are_built_once_for_concurrent_requests(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, None)
    builds = []
    lock = threading.Lock()

    def build(_d):
        with lock:
            builds.append(1)
        time.sleep(0.2)
        return {"ctx": True}

    written = {}

    def persist(_d, _ctx):
        written["doc"] = {"version": metrics.METRICS_VERSION, "fresh": True}
        return written["doc"]

    # After the first build persists, later waiters see fresh metrics.
    monkeypatch.setattr(metrics, "persist_metrics_from_full_context", persist)
    monkeypatch.setattr(metrics, "read_metrics_file", lambda d: written.get("doc"))
    monkeypatch.setattr(metrics, "metrics_are_fresh", lambda d, m: bool(m and m.get("fresh")))

    results = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                metrics.ensure_persisted_metrics(tmp_path, build_full_context=build)
            )
        )
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(builds) == 1
    assert all(r and r.get("fresh") for r in results)

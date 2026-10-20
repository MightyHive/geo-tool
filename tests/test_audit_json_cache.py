from __future__ import annotations

import json
import os
import threading

from api import audit_json_cache as cache


def test_cache_hit_until_file_changes(tmp_path) -> None:
    cache.clear_all()
    path = tmp_path / "doc.json"
    path.write_text(json.dumps({"v": 1}), encoding="utf-8")
    assert cache.load_json_cached(path) == {"v": 1}

    calls = []
    assert cache.load_json_cached(path, loader=lambda p: calls.append(1) or {"x": 1}) == {"v": 1}
    assert calls == []  # served from cache

    path.write_text(json.dumps({"v": 2, "pad": "x"}), encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert cache.load_json_cached(path) == {"v": 2, "pad": "x"}


def test_concurrent_misses_read_once(tmp_path) -> None:
    cache.clear_all()
    path = tmp_path / "big.json"
    path.write_text("{}", encoding="utf-8")
    reads = []
    gate = threading.Event()

    def loader(p):
        reads.append(1)
        gate.wait(1)
        return {"ok": True}

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(cache.load_json_cached(path, loader=loader)))
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join()
    assert len(reads) == 1
    assert results == [{"ok": True}] * 5


def test_cache_bounded_by_size(tmp_path, monkeypatch) -> None:
    cache.clear_all()
    monkeypatch.setenv("AUDIT_JSON_CACHE_MAX_MB", "0.001")  # ~1 KB
    paths = []
    for i in range(3):
        p = tmp_path / f"f{i}.json"
        p.write_text(json.dumps({"pad": "x" * 600}), encoding="utf-8")
        cache.load_json_cached(p)
        paths.append(p)
    cached = [cache.get_cached(p) is not None for p in paths]
    assert cached[-1] is True
    assert sum(cached) == 1

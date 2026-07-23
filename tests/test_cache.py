"""Optional Redis cache helpers — graceful no-op without REDIS_URL."""

from __future__ import annotations

import api.cache as cache
import api.prompt_performance_metrics as metrics


def test_cache_noop_without_redis(monkeypatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    cache.reset_client_for_tests()
    assert cache.redis_enabled() is False
    assert cache.cache_get("pp-slim:x") is None
    assert cache.cache_set("pp-slim:x", {"a": 1}) is False
    assert cache.cache_delete("pp-slim:x") is False


def test_slim_response_cache_key_includes_locale_version_mtime() -> None:
    key = metrics.slim_response_cache_key(
        "acme_abc",
        locale_key="BE:en",
        include_all_locales=False,
        version=1,
        probe_mtime_value=1700000000.123,
    )
    assert key == "pp-slim:acme_abc:BE:en:v1:1700000000.123"
    all_key = metrics.slim_response_cache_key(
        "acme_abc",
        locale_key=None,
        include_all_locales=True,
        version=1,
        probe_mtime_value=None,
    )
    assert ":__all__:" in all_key
    assert all_key.endswith(":none")


def test_invalidate_slim_response_caches_removes_gcs_blobs(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    cache.reset_client_for_tests()
    blob = tmp_path / "prompt_performance_slim_BE_en.json"
    blob.write_text("{}", encoding="utf-8")
    metrics.invalidate_slim_response_caches(tmp_path)
    assert not blob.exists()

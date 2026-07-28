"""Optional Redis / Memorystore cache helpers (graceful no-op without REDIS_URL).

Used to serve slim prompt-performance metrics JSON across Cloud Run instances
without re-reading GCS FUSE on every GET.

Environment
-----------
REDIS_URL
    Redis connection URL, e.g. ``redis://10.0.0.3:6379/0`` (Memorystore).
    When unset or Redis is unreachable, get/set/delete are no-ops.
REDIS_TTL_SEC
    Default TTL for cached values (default ``300``, clamped 30–3600).
SLIM_METRICS_REDIS
    Set to ``0`` / ``false`` to disable Redis for slim metrics even when
    ``REDIS_URL`` is set (default: enabled when URL is present).
SLIM_METRICS_GCS_CACHE
    When ``1`` / ``true``, also write per-locale slim response JSON under the
    audit dir (GCS FUSE mount on Cloud Run) for optional CDN / signed-URL
    serving. Default off.
SLIM_METRICS_CDN_BASE_URL
    Optional public/CDN base (no trailing slash). When set with GCS cache
    enabled, GET responses may include ``metrics_cdn_url`` pointing at
    ``{base}/{audit_relative_path}``.

Memorystore must be provisioned separately for DEV/staging/production and
``REDIS_URL`` injected into Cloud Run. Code paths are safe without it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

log = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()
_client_failed = False


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def redis_url() -> str:
    return (os.getenv("REDIS_URL") or "").strip()


def redis_enabled() -> bool:
    if not redis_url():
        return False
    if os.getenv("SLIM_METRICS_REDIS") is not None and not _env_flag("SLIM_METRICS_REDIS", True):
        return False
    return True


def cache_ttl_seconds() -> int:
    raw = (os.getenv("REDIS_TTL_SEC") or "").strip()
    try:
        ttl = int(raw) if raw else 300
    except ValueError:
        ttl = 300
    return max(30, min(3600, ttl))


def gcs_slim_cache_enabled() -> bool:
    return _env_flag("SLIM_METRICS_GCS_CACHE", False)


def slim_metrics_cdn_base_url() -> str:
    return (os.getenv("SLIM_METRICS_CDN_BASE_URL") or "").strip().rstrip("/")


def _get_client() -> Any | None:
    """Return a Redis client or None (missing URL / import / connection)."""
    global _client, _client_failed
    if not redis_enabled() or _client_failed:
        return None
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None or _client_failed:
            return _client
        try:
            import redis  # type: ignore[import-untyped]
        except ImportError:
            log.warning("REDIS_URL set but redis package not installed; cache disabled")
            _client_failed = True
            return None
        try:
            client = redis.Redis.from_url(
                redis_url(),
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=1.0,
            )
            client.ping()
            _client = client
            return _client
        except Exception:
            log.warning("Redis unavailable at REDIS_URL; slim metrics cache disabled", exc_info=True)
            _client_failed = True
            return None


def reset_client_for_tests() -> None:
    """Drop cached client state (unit tests only)."""
    global _client, _client_failed
    with _client_lock:
        _client = None
        _client_failed = False


def cache_get(key: str) -> Any | None:
    """Return JSON-deserialized value for ``key``, or None on miss / error."""
    client = _get_client()
    if client is None or not key:
        return None
    try:
        raw = client.get(key)
    except Exception:
        log.debug("Redis GET failed for %s", key, exc_info=True)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def cache_set(key: str, value: Any, *, ttl: int | None = None) -> bool:
    """Store JSON-serialized ``value``. Returns True on success."""
    client = _get_client()
    if client is None or not key:
        return False
    try:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        client.setex(key, int(ttl if ttl is not None else cache_ttl_seconds()), payload)
        return True
    except Exception:
        log.debug("Redis SET failed for %s", key, exc_info=True)
        return False


def cache_delete(key: str) -> bool:
    client = _get_client()
    if client is None or not key:
        return False
    try:
        client.delete(key)
        return True
    except Exception:
        log.debug("Redis DELETE failed for %s", key, exc_info=True)
        return False


def cache_delete_prefix(prefix: str) -> int:
    """Best-effort delete of keys matching ``prefix*`` (SCAN). Returns count deleted."""
    client = _get_client()
    if client is None or not prefix:
        return 0
    deleted = 0
    try:
        for key in client.scan_iter(match=f"{prefix}*", count=100):
            try:
                client.delete(key)
                deleted += 1
            except Exception:
                continue
    except Exception:
        log.debug("Redis SCAN/DELETE failed for prefix %s", prefix, exc_info=True)
    return deleted

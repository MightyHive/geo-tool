"""In-process cache for audit JSON reads (GCS FUSE is slow under concurrency).

Entries are revalidated against the file's mtime/size on every hit, so they can
live much longer than a blind TTL without serving stale data written by another
instance or job. Concurrent misses for the same file share one read/parse, and the
cache is bounded by total on-disk size so multi-MB probe files cannot exhaust memory.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

_DEFAULT_TTL_SEC = 900.0
_MIN_TTL_SEC = 30.0
_MAX_TTL_SEC = 3600.0
_MAX_ENTRIES = 96
_DEFAULT_MAX_BYTES = 150 * 1024 * 1024  # on-disk bytes; parsed size is several x

_lock = threading.RLock()
# key -> (expires_at, (mtime_ns, size) | None, size_bytes, value)
_entries: dict[str, tuple[float, tuple[int, int] | None, int, Any]] = {}
_load_locks: dict[str, threading.Lock] = {}


def cache_ttl_seconds() -> float:
    raw = (os.getenv("AUDIT_JSON_CACHE_TTL_SEC") or "").strip()
    try:
        ttl = float(raw) if raw else _DEFAULT_TTL_SEC
    except ValueError:
        ttl = _DEFAULT_TTL_SEC
    return max(_MIN_TTL_SEC, min(_MAX_TTL_SEC, ttl))


def cache_max_bytes() -> int:
    raw = (os.getenv("AUDIT_JSON_CACHE_MAX_MB") or "").strip()
    try:
        return int(float(raw) * 1024 * 1024) if raw else _DEFAULT_MAX_BYTES
    except ValueError:
        return _DEFAULT_MAX_BYTES


def _key_for(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _evict_if_needed() -> None:
    max_bytes = cache_max_bytes()
    total = sum(entry[2] for entry in _entries.values())
    if len(_entries) <= _MAX_ENTRIES and total <= max_bytes:
        return
    # Drop soonest-expiring first until under both limits.
    for key, entry in sorted(_entries.items(), key=lambda item: item[1][0]):
        if len(_entries) <= _MAX_ENTRIES and total <= max_bytes:
            break
        _entries.pop(key, None)
        total -= entry[2]


def get_cached(path: Path) -> Any | None:
    key = _key_for(path)
    now = time.monotonic()
    with _lock:
        hit = _entries.get(key)
    if not hit:
        return None
    expires_at, signature, _size, value = hit
    if expires_at <= now:
        with _lock:
            _entries.pop(key, None)
        return None
    if signature is not None and _file_signature(path) != signature:
        # Rewritten elsewhere (another instance, a job, or a direct write).
        with _lock:
            _entries.pop(key, None)
        return None
    return value


def set_cached(
    path: Path,
    value: Any,
    *,
    ttl: float | None = None,
    signature: tuple[int, int] | None = None,
) -> None:
    key = _key_for(path)
    ttl_sec = cache_ttl_seconds() if ttl is None else ttl
    if signature is None:
        signature = _file_signature(path)
    size = signature[1] if signature else 0
    with _lock:
        _entries[key] = (time.monotonic() + ttl_sec, signature, size, value)
        _evict_if_needed()


def invalidate_path(path: Path) -> None:
    key = _key_for(path)
    with _lock:
        _entries.pop(key, None)


def invalidate_audit(audit_dir: Path) -> None:
    """Drop every cached entry under this audit directory."""
    try:
        root = str(audit_dir.resolve())
    except OSError:
        root = str(audit_dir)
    prefix = root.rstrip("/") + "/"
    with _lock:
        doomed = [key for key in _entries if key == root or key.startswith(prefix)]
        for key in doomed:
            _entries.pop(key, None)


def clear_all() -> None:
    with _lock:
        _entries.clear()


def _load_lock(key: str) -> threading.Lock:
    with _lock:
        lock = _load_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _load_locks[key] = lock
        return lock


def load_json_cached(
    path: Path,
    *,
    loader: Callable[[Path], Any] | None = None,
) -> Any | None:
    """
    Return parsed JSON for ``path``, using the in-process cache.

    Missing / unreadable files are not cached. Concurrent callers missing the
    same file wait for a single read instead of each parsing it.
    """
    cached = get_cached(path)
    if cached is not None:
        return cached

    def _default_loader(p: Path) -> Any | None:
        if not p.is_file():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw

    with _load_lock(_key_for(path)):
        cached = get_cached(path)  # filled by the request we waited on
        if cached is not None:
            return cached
        # Signature before the read: a concurrent rewrite then invalidates on next hit.
        signature = _file_signature(path)
        value = (loader or _default_loader)(path)
        if value is None:
            return None
        set_cached(path, value, signature=signature)
        return value

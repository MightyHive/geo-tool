"""Short in-process TTL cache for audit JSON reads (GCS FUSE is slow under concurrency)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

_DEFAULT_TTL_SEC = 60.0
_MIN_TTL_SEC = 30.0
_MAX_TTL_SEC = 120.0
_MAX_ENTRIES = 96

_lock = threading.RLock()
_entries: dict[str, tuple[float, Any]] = {}


def cache_ttl_seconds() -> float:
    raw = (os.getenv("AUDIT_JSON_CACHE_TTL_SEC") or "").strip()
    try:
        ttl = float(raw) if raw else _DEFAULT_TTL_SEC
    except ValueError:
        ttl = _DEFAULT_TTL_SEC
    return max(_MIN_TTL_SEC, min(_MAX_TTL_SEC, ttl))


def _key_for(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _evict_if_needed() -> None:
    if len(_entries) <= _MAX_ENTRIES:
        return
    # Drop oldest by expiry timestamp.
    ordered = sorted(_entries.items(), key=lambda item: item[1][0])
    for key, _ in ordered[: max(1, len(_entries) - _MAX_ENTRIES)]:
        _entries.pop(key, None)


def get_cached(path: Path) -> Any | None:
    key = _key_for(path)
    now = time.monotonic()
    with _lock:
        hit = _entries.get(key)
        if not hit:
            return None
        expires_at, value = hit
        if expires_at <= now:
            _entries.pop(key, None)
            return None
        return value


def set_cached(path: Path, value: Any, *, ttl: float | None = None) -> None:
    key = _key_for(path)
    ttl_sec = cache_ttl_seconds() if ttl is None else ttl
    with _lock:
        _entries[key] = (time.monotonic() + ttl_sec, value)
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


def load_json_cached(
    path: Path,
    *,
    loader: Callable[[Path], Any] | None = None,
) -> Any | None:
    """
    Return parsed JSON for ``path``, using the in-process TTL cache.

    Missing / unreadable files are not cached.
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

    value = (loader or _default_loader)(path)
    if value is None:
        return None
    set_cached(path, value)
    return value

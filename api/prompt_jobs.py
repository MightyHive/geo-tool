"""Launch durable Cloud Run Job executions for all AI prompt probes.

For ``post_audit`` and ``live`` modes, enqueues one Cloud Run Job execution per
configured prompt locale (fan-out). Platforms stay together inside each locale job.
Modes ``history`` and ``aio`` remain single-execution jobs.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import random
import re
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from api import geo_services as geo

log = logging.getLogger(__name__)

PromptJobMode = Literal["post_audit", "live", "history", "aio"]
PROMPT_JOB_REQUESTS_DIR = "prompt_job_requests"
PROMPT_PENDING_FILE = "prompt_performance_probe_pending.json"
AIO_PENDING_FILE = "prompt_performance_aio_pending.json"
FANOUT_STATUS_FILE = "prompt_probe_fanout.json"
FANOUT_SHARDS_DIR = "prompt_probe_fanout_shards"
LOCALE_PROBE_FILE_PREFIX = "prompt_performance_live_probe_locale_"

# Modes that fan out one Job execution per prompt locale.
_LOCALE_FANOUT_MODES: frozenset[str] = frozenset({"post_audit", "live"})

# ESTALE is 116 on Linux (Cloud Run / GCS FUSE) and often 70 on macOS.
# ENOENT during replace races shows up as "storage: object doesn't exist".
_TRANSIENT_FS_ERRNOS = frozenset(
    {
        getattr(errno, "ESTALE", 116),
        116,
        getattr(errno, "EBUSY", 16),
        getattr(errno, "EAGAIN", 11),
    }
)
_WRITE_TRANSIENT_FS_ERRNOS = _TRANSIENT_FS_ERRNOS | {getattr(errno, "ENOENT", 2)}
_WRITE_ATTEMPTS = 6
_READ_ATTEMPTS = 6
# Stale must be < timeout so a waiter can clear an abandoned FUSE lock before giving up.
_LOCK_STALE_SECONDS = 20.0
_LOCK_TIMEOUT_SECONDS = 45.0
_UPDATE_JSON_LOCK_ATTEMPTS = 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_transient_fs_error(exc: BaseException, *, for_write: bool = False) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if not isinstance(exc, OSError):
        return False
    errnos = _WRITE_TRANSIENT_FS_ERRNOS if for_write else _TRANSIENT_FS_ERRNOS
    return int(getattr(exc, "errno", -1) or -1) in errnos


def _backoff_delay(attempt: int) -> float:
    return 0.05 * (2**attempt) + random.uniform(0, 0.05)


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_text(path: Path, text: str) -> None:
    """Atomic text write resilient to NFS/GCS FUSE ESTALE races.

    Uses a unique temp name per attempt (pid + uuid) so concurrent writers never
    share the same ``.tmp`` inode, then ``os.replace`` onto the final path.
    Never truncates the destination in place (avoids GCS FUSE "legacy staged writes").
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    last_exc: OSError | None = None
    for attempt in range(_WRITE_ATTEMPTS):
        tmp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            return
        except OSError as exc:
            last_exc = exc
            _unlink_quiet(tmp)
            if not _is_transient_fs_error(exc, for_write=True) or attempt >= _WRITE_ATTEMPTS - 1:
                raise
            delay = _backoff_delay(attempt)
            log.warning(
                "Transient FS error writing %s (attempt %s/%s): %s; retrying in %.2fs",
                path,
                attempt + 1,
                _WRITE_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)
    if last_exc:
        raise last_exc


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic JSON write resilient to NFS/GCS FUSE ESTALE races."""
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    _write_text(path, text)


@contextmanager
def _exclusive_file_lock(
    lock_path: Path,
    *,
    timeout: float = _LOCK_TIMEOUT_SECONDS,
    stale_after: float = _LOCK_STALE_SECONDS,
) -> Iterator[None]:
    """Cross-process lock via O_EXCL create (works better on FUSE than fcntl)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, f"{os.getpid()}:{time.time():.3f}\n".encode())
                yield
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass
                _unlink_quiet(lock_path)
            return
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > stale_after:
                    log.warning("Removing stale lock %s (age=%.1fs)", lock_path, age)
                    _unlink_quiet(lock_path)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for lock {lock_path}") from None
            time.sleep(0.05 + random.uniform(0, 0.05))


def _update_json(
    path: Path,
    mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    create_if_missing: bool = False,
) -> dict[str, Any] | None:
    """Locked read-modify-write for infrequently updated shared status files.

    Prefer per-locale shards for hot fan-out progress paths — do not use this for
    high-frequency multi-writer updates of ``prompt_probe_fanout.json``.
    Retries briefly on lock TimeoutError (abandoned/contended FUSE locks).
    """
    lock_path = path.with_name(path.name + ".lock")
    last_timeout: TimeoutError | None = None
    for attempt in range(_UPDATE_JSON_LOCK_ATTEMPTS):
        try:
            with _exclusive_file_lock(lock_path):
                data = _read_json(path)
                if data is None:
                    if not create_if_missing:
                        return None
                    data = {}
                updated = mutator(data)
                if updated is None:
                    return data
                _write_json(path, updated)
                return updated
        except TimeoutError as exc:
            last_timeout = exc
            if attempt >= _UPDATE_JSON_LOCK_ATTEMPTS - 1:
                break
            delay = _backoff_delay(attempt)
            log.warning(
                "Lock timeout updating %s (attempt %s/%s); retrying in %.2fs",
                path,
                attempt + 1,
                _UPDATE_JSON_LOCK_ATTEMPTS,
                delay,
            )
            time.sleep(delay)
    if last_timeout:
        raise last_timeout
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read JSON with brief retries on GCS FUSE stale-handle / mid-replace races."""
    last_exc: OSError | None = None
    for attempt in range(_READ_ATTEMPTS):
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except FileNotFoundError:
            return None
        except OSError as exc:
            last_exc = exc
            if not _is_transient_fs_error(exc) or attempt >= _READ_ATTEMPTS - 1:
                return None
            delay = _backoff_delay(attempt)
            log.warning(
                "Transient FS error reading %s (attempt %s/%s): %s; retrying in %.2fs",
                path,
                attempt + 1,
                _READ_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)
            continue
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    if last_exc:
        log.warning("Giving up reading %s after transient FS errors: %s", path, last_exc)
    return None


def _list_fanout_shard_paths(audit_dir: Path) -> list[Path]:
    shards_dir = audit_dir / FANOUT_SHARDS_DIR
    try:
        if not shards_dir.is_dir():
            return []
        return sorted(p for p in shards_dir.glob("*.json") if p.is_file())
    except OSError as exc:
        if _is_transient_fs_error(exc):
            log.warning("Transient FS error listing fan-out shards under %s: %s", shards_dir, exc)
            return []
        raise


def prompt_job_name() -> str:
    return (os.getenv("PROMPT_PROBE_JOB_NAME") or "geo-audit-prompt-probes").strip()


def prompt_job_region() -> str:
    return (
        os.getenv("PROMPT_PROBE_JOB_REGION")
        or os.getenv("CLOUD_RUN_REGION")
        or "europe-west1"
    ).strip()


def prompt_job_project() -> str:
    return (
        os.getenv("PROMPT_PROBE_JOB_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or "emea-ds-sandbox"
    ).strip()


def locale_key_filename(locale_key: str) -> str:
    """Filesystem-safe form of a locale key (e.g. ``BE:en`` → ``BE_en``)."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (locale_key or "").strip())
    return safe or "locale"


def locale_probe_artifact_path(audit_dir: Path, locale_key: str) -> Path:
    return audit_dir / f"{LOCALE_PROBE_FILE_PREFIX}{locale_key_filename(locale_key)}.json"


def fanout_status_path(audit_dir: Path) -> Path:
    return audit_dir / FANOUT_STATUS_FILE


def fanout_locale_shard_path(audit_dir: Path, locale_key: str) -> Path:
    """Per-locale fan-out status shard (single-writer; GCS-FUSE-safe)."""
    return audit_dir / FANOUT_SHARDS_DIR / f"{locale_key_filename(locale_key)}.json"


def load_fanout_status(audit_dir: Path) -> dict[str, Any] | None:
    """Load fan-out status, overlaying per-locale shards onto the base file.

    Locale probe jobs write only their shard. The base ``prompt_probe_fanout.json``
    holds batch metadata + finalize claim state and is updated infrequently.
    """
    base = _read_json(fanout_status_path(audit_dir))
    if base is None:
        return None
    locales: dict[str, Any] = {}
    raw_locales = base.get("locales")
    if isinstance(raw_locales, dict):
        for key, entry in raw_locales.items():
            if isinstance(entry, dict):
                locales[str(key)] = dict(entry)

    filename_to_key = {locale_key_filename(k): k for k in locales}
    for shard_path in _list_fanout_shard_paths(audit_dir):
        shard = _read_json(shard_path)
        if not shard:
            continue
        locale_key = str(shard.get("locale_key") or "").strip()
        if not locale_key:
            locale_key = filename_to_key.get(shard_path.stem) or shard_path.stem
        entry = {k: v for k, v in shard.items() if k != "locale_key"}
        merged = dict(locales.get(locale_key) or {})
        merged.update(entry)
        locales[locale_key] = merged
        filename_to_key[locale_key_filename(locale_key)] = locale_key

    out = dict(base)
    out["locales"] = locales
    return out


def _write_fanout_locale_shard(
    audit_dir: Path,
    locale_key: str,
    entry: dict[str, Any],
) -> None:
    payload = {"locale_key": locale_key, **entry}
    _write_json(fanout_locale_shard_path(audit_dir, locale_key), payload)


def _pending_path(audit_dir: Path, mode: PromptJobMode) -> Path:
    return audit_dir / (AIO_PENDING_FILE if mode == "aio" else PROMPT_PENDING_FILE)


def _active_request(pending: Path) -> dict[str, Any] | None:
    payload = _read_json(pending)
    if not payload:
        return None
    if str(payload.get("status") or "") in {"queued", "starting", "running"}:
        return payload
    return None


def resolve_prompt_locales_for_audit(audit_dir: Path) -> list[dict[str, Any]]:
    """Return configured prompt locales (at least one default) for fan-out planning."""
    from api.prompt_performance import _load_audit_onboarding
    from prompt_locales import locales_from_onboarding

    onboarding = _load_audit_onboarding(audit_dir) or {}
    locales = locales_from_onboarding(onboarding)
    if locales:
        return locales
    return [
        {
            "country": str(onboarding.get("geo_market_country") or ""),
            "country_code": str(onboarding.get("geo_market_country_code") or ""),
            "language": "en",
            "language_name": "English",
            "key": "XX:en",
            "label": "English",
        }
    ]


def plan_locale_job_payloads(
    audit_dir: Path,
    *,
    mode: PromptJobMode,
    report_mode: bool = True,
    completion: dict[str, Any] | None = None,
    locale_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build one job payload per locale (used by enqueue + tests). Does not launch."""
    locales = resolve_prompt_locales_for_audit(audit_dir)
    if locale_keys is not None:
        wanted = {str(k) for k in locale_keys}
        locales = [loc for loc in locales if str(loc.get("key") or "") in wanted]
    audit_id = geo.audit_dir_api_rel(audit_dir)
    total = len(locales) or 1
    payloads: list[dict[str, Any]] = []
    for index, locale in enumerate(locales, start=1):
        key = str(locale.get("key") or f"locale_{index}")
        payloads.append(
            {
                "audit_id": audit_id,
                "mode": mode,
                "report_mode": bool(report_mode),
                "completion": completion or {},
                "locale_key": key,
                "locale": locale,
                "locale_index": index,
                "locale_total": total,
            }
        )
    return payloads


def _fanout_already_running_response(audit_id: str, active: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(active.get("status") or "queued"),
        "audit_id": audit_id,
        "request_id": str(active.get("request_id") or active.get("batch_id") or ""),
        "execution": str(active.get("execution") or ""),
        "already_running": True,
        "fanout": bool(active.get("fanout")),
        "locale_count": int(active.get("locale_count") or len(active.get("locales") or {})),
        "locales": active.get("locales") or {},
    }


def _locales_needing_rerun(fanout: dict[str, Any] | None) -> list[str]:
    """Return locale keys that failed (or never completed) and are safe to re-enqueue."""
    if not fanout or not isinstance(fanout.get("locales"), dict):
        return []
    out: list[str] = []
    for key, entry in fanout["locales"].items():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "")
        # Active in-flight statuses block a bulk failed re-run.
        if status in {"starting", "running", "finalizing"}:
            return []
        if status in {"failed", "launch_failed", "queued"}:
            # "queued" without an active pending job is treated as stale/failed
            # so partial fan-out deaths can be resumed.
            out.append(str(key))
    return out


def enqueue_prompt_job(
    audit_dir: Path,
    *,
    mode: PromptJobMode,
    report_mode: bool = True,
    all_prompts: bool = False,
    max_prompts: int = 25,
    completion: dict[str, Any] | None = None,
    failed_only: bool = False,
    locale_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Persist request manifest(s), launch Job execution(s), and return immediately."""
    audit_dir = audit_dir.resolve()
    audit_id = geo.audit_dir_api_rel(audit_dir)
    pending = _pending_path(audit_dir, mode)
    active = _active_request(pending)
    if active:
        return _fanout_already_running_response(audit_id, active)

    if mode in _LOCALE_FANOUT_MODES:
        fanout = load_fanout_status(audit_dir)
        explicit_keys = [str(k) for k in (locale_keys or []) if str(k).strip()]
        if explicit_keys:
            return _enqueue_locale_fanout(
                audit_dir,
                mode=mode,
                report_mode=report_mode,
                completion=completion,
                locale_keys=explicit_keys,
                resume_batch_id=str((fanout or {}).get("batch_id") or "") or None,
                prior_fanout=fanout if isinstance(fanout, dict) else None,
            )
        failed_keys = _locales_needing_rerun(fanout)
        if failed_only:
            if not failed_keys:
                raise ValueError("No failed markets to re-run")
            return _enqueue_locale_fanout(
                audit_dir,
                mode=mode,
                report_mode=report_mode,
                completion=completion,
                locale_keys=failed_keys,
                resume_batch_id=str((fanout or {}).get("batch_id") or "") or None,
                prior_fanout=fanout if isinstance(fanout, dict) else None,
            )
        if failed_keys and fanout and str(fanout.get("mode") or "") == mode:
            return _enqueue_locale_fanout(
                audit_dir,
                mode=mode,
                report_mode=report_mode,
                completion=completion,
                locale_keys=failed_keys,
                resume_batch_id=str(fanout.get("batch_id") or "") or None,
                prior_fanout=fanout,
            )
        return _enqueue_locale_fanout(
            audit_dir,
            mode=mode,
            report_mode=report_mode,
            completion=completion,
        )

    return _enqueue_single_prompt_job(
        audit_dir,
        mode=mode,
        report_mode=report_mode,
        all_prompts=all_prompts,
        max_prompts=max_prompts,
        completion=completion,
    )


def _enqueue_single_prompt_job(
    audit_dir: Path,
    *,
    mode: PromptJobMode,
    report_mode: bool = True,
    all_prompts: bool = False,
    max_prompts: int = 25,
    completion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Legacy single-execution path for history / aio modes."""
    audit_id = geo.audit_dir_api_rel(audit_dir)
    pending = _pending_path(audit_dir, mode)
    request_id = uuid.uuid4().hex
    manifest_path = audit_dir / PROMPT_JOB_REQUESTS_DIR / f"{request_id}.json"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "request_id": request_id,
        "audit_id": audit_id,
        "mode": mode,
        "report_mode": bool(report_mode),
        "all_prompts": bool(all_prompts),
        "max_prompts": max(1, min(int(max_prompts or 25), 80)),
        "completion": completion or {},
        "status": "queued",
        "created_at": _utc_now(),
    }
    _write_json(manifest_path, manifest)
    _write_json(
        pending,
        {
            "status": "starting",
            "request_id": request_id,
            "mode": mode,
            "created_at": manifest["created_at"],
        },
    )

    try:
        execution = _execute_prompt_job(audit_id=audit_id, request_id=request_id)
    except Exception as exc:
        manifest.update({"status": "launch_failed", "error": str(exc), "updated_at": _utc_now()})
        _write_json(manifest_path, manifest)
        pending.unlink(missing_ok=True)
        raise

    manifest.update({"status": "started", "execution": execution, "updated_at": _utc_now()})
    _write_json(manifest_path, manifest)
    current = _read_json(pending) or {}
    if str(current.get("request_id") or "") == request_id:
        current.update({"status": current.get("status") or "queued", "execution": execution})
        _write_json(pending, current)
    return {
        "status": "queued",
        "audit_id": audit_id,
        "request_id": request_id,
        "execution": execution,
        "already_running": False,
        "fanout": False,
        "locale_count": 1,
    }


def _enqueue_locale_fanout(
    audit_dir: Path,
    *,
    mode: PromptJobMode,
    report_mode: bool = True,
    completion: dict[str, Any] | None = None,
    locale_keys: list[str] | None = None,
    resume_batch_id: str | None = None,
    prior_fanout: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enqueue one Cloud Run Job execution per locale."""
    audit_id = geo.audit_dir_api_rel(audit_dir)
    pending = _pending_path(audit_dir, mode)
    payloads = plan_locale_job_payloads(
        audit_dir,
        mode=mode,
        report_mode=report_mode,
        completion=completion,
        locale_keys=locale_keys,
    )
    if not payloads:
        raise ValueError("No prompt locales to enqueue")

    # Recompute indices against full locale set so progress UI stays consistent.
    all_locales = resolve_prompt_locales_for_audit(audit_dir)
    key_to_index = {
        str(loc.get("key") or f"locale_{i}"): i for i, loc in enumerate(all_locales, start=1)
    }
    locale_total = len(all_locales) or len(payloads)

    batch_id = resume_batch_id or uuid.uuid4().hex
    created_at = _utc_now()
    locale_entries: dict[str, Any] = {}
    if prior_fanout and isinstance(prior_fanout.get("locales"), dict):
        # Preserve successful locale entries when re-running failures.
        for key, entry in prior_fanout["locales"].items():
            if isinstance(entry, dict) and str(entry.get("status") or "") == "completed":
                locale_entries[str(key)] = dict(entry)

    _write_json(
        pending,
        {
            "status": "starting",
            "request_id": batch_id,
            "batch_id": batch_id,
            "mode": mode,
            "fanout": True,
            "locale_count": locale_total,
            "created_at": created_at,
        },
    )

    launched: list[dict[str, Any]] = []
    launch_errors: list[str] = []

    for payload in payloads:
        locale_key = str(payload["locale_key"])
        request_id = uuid.uuid4().hex
        locale_index = int(key_to_index.get(locale_key) or payload["locale_index"])
        manifest: dict[str, Any] = {
            "schema_version": 2,
            "request_id": request_id,
            "batch_id": batch_id,
            "audit_id": audit_id,
            "mode": mode,
            "report_mode": bool(report_mode),
            "completion": completion or {},
            "locale_key": locale_key,
            "locale": payload.get("locale") or {},
            "locale_index": locale_index,
            "locale_total": locale_total,
            "status": "queued",
            "created_at": created_at,
        }
        manifest_path = audit_dir / PROMPT_JOB_REQUESTS_DIR / f"{request_id}.json"
        _write_json(manifest_path, manifest)
        try:
            execution = _execute_prompt_job(audit_id=audit_id, request_id=request_id)
        except Exception as exc:
            log.exception("Failed to launch locale probe job %s for %s", locale_key, audit_id)
            manifest.update(
                {"status": "launch_failed", "error": str(exc), "updated_at": _utc_now()}
            )
            _write_json(manifest_path, manifest)
            locale_entries[locale_key] = {
                "request_id": request_id,
                "status": "launch_failed",
                "error": str(exc),
                "locale_index": locale_index,
                "locale_label": str((payload.get("locale") or {}).get("label") or locale_key),
            }
            launch_errors.append(f"{locale_key}: {exc}")
            continue

        manifest.update({"status": "started", "execution": execution, "updated_at": _utc_now()})
        _write_json(manifest_path, manifest)
        entry = {
            "request_id": request_id,
            "status": "queued",
            "execution": execution,
            "locale_index": locale_index,
            "locale_label": str((payload.get("locale") or {}).get("label") or locale_key),
            "completed_calls": 0,
            "planned_calls": 0,
        }
        locale_entries[locale_key] = entry
        launched.append({"locale_key": locale_key, "request_id": request_id, "execution": execution})

    if not launched and launch_errors:
        pending.unlink(missing_ok=True)
        raise RuntimeError(
            "Failed to launch locale probe jobs: " + "; ".join(launch_errors[:3])
        )

    fanout_payload = {
        "schema_version": 2,
        "batch_id": batch_id,
        "audit_id": audit_id,
        "mode": mode,
        "report_mode": bool(report_mode),
        "completion": completion or {},
        "locale_total": locale_total,
        "locales": locale_entries,
        "finalize_status": "pending",
        "updated_at": _utc_now(),
        "created_at": created_at,
    }
    # Clear prior shards for locales we are (re)launching so stale progress
    # cannot mask a fresh run; preserve shards for completed locales kept above.
    shards_dir = audit_dir / FANOUT_SHARDS_DIR
    try:
        if shards_dir.is_dir():
            keep_filenames = {
                locale_key_filename(k)
                for k, entry in locale_entries.items()
                if str((entry or {}).get("status") or "") == "completed"
            }
            for old in shards_dir.glob("*.json"):
                if old.stem not in keep_filenames:
                    _unlink_quiet(old)
    except OSError as exc:
        log.warning("Could not prune fan-out shards under %s: %s", shards_dir, exc)

    _write_json(fanout_status_path(audit_dir), fanout_payload)
    for locale_key, entry in locale_entries.items():
        if str(entry.get("status") or "") == "completed":
            continue
        _write_fanout_locale_shard(audit_dir, locale_key, dict(entry))

    primary_execution = launched[0]["execution"] if launched else ""
    _write_json(
        pending,
        {
            # Jobs are already accepted — mark running so locale tasks need not
            # contend on a shared pending RMW at startup.
            "status": "running",
            "request_id": batch_id,
            "batch_id": batch_id,
            "mode": mode,
            "fanout": True,
            "locale_count": locale_total,
            "locales": {
                k: {
                    "request_id": v.get("request_id"),
                    "status": v.get("status"),
                    "execution": v.get("execution"),
                }
                for k, v in locale_entries.items()
            },
            "execution": primary_execution,
            "created_at": created_at,
            "updated_at": _utc_now(),
        },
    )

    log.info(
        "Enqueued %s locale probe job(s) for %s (batch %s)",
        len(launched),
        audit_id,
        batch_id,
    )
    return {
        "status": "queued",
        "audit_id": audit_id,
        "request_id": batch_id,
        "batch_id": batch_id,
        "execution": primary_execution,
        "already_running": False,
        "fanout": True,
        "locale_count": locale_total,
        "launched": launched,
        "launch_errors": launch_errors,
    }


def update_fanout_locale_progress(
    audit_dir: Path,
    *,
    locale_key: str,
    status: str | None = None,
    completed_calls: int | None = None,
    planned_calls: int | None = None,
    error: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any] | None:
    """Update one locale's fan-out progress via a single-writer shard file.

    Concurrent locale jobs must never RMW the shared ``prompt_probe_fanout.json``
    for progress ticks — that causes GCS FUSE stale-file-handle errors. Each
    locale writes only ``prompt_probe_fanout_shards/<locale>.json``.
    """
    shard_path = fanout_locale_shard_path(audit_dir, locale_key)
    entry = _read_json(shard_path) or {}
    entry.pop("locale_key", None)

    if not entry:
        # Seed from base snapshot when shard is missing (resume / older batches).
        base = _read_json(fanout_status_path(audit_dir)) or {}
        base_locales = base.get("locales") if isinstance(base.get("locales"), dict) else {}
        seed = base_locales.get(locale_key)
        if isinstance(seed, dict):
            entry = dict(seed)

    if request_id and entry.get("request_id") and str(entry["request_id"]) != str(request_id):
        # Stale job from a previous batch — ignore.
        return load_fanout_status(audit_dir)

    if status:
        entry["status"] = status
    if completed_calls is not None:
        entry["completed_calls"] = max(0, int(completed_calls))
    if planned_calls is not None:
        entry["planned_calls"] = max(0, int(planned_calls))
    if error is not None:
        entry["error"] = error
    if request_id and not entry.get("request_id"):
        entry["request_id"] = request_id
    entry["updated_at"] = _utc_now()

    try:
        _write_fanout_locale_shard(audit_dir, locale_key, entry)
    except OSError as exc:
        if _is_transient_fs_error(exc, for_write=True):
            log.warning(
                "Skipping fan-out shard update for %s after transient FS error: %s",
                locale_key,
                exc,
            )
            return load_fanout_status(audit_dir)
        raise
    return load_fanout_status(audit_dir)


def touch_fanout_pending_running(audit_dir: Path, *, mode: str, batch_id: str) -> None:
    """Best-effort mark of shared pending as running (fan-out jobs).

    Enqueue already sets pending to ``running`` after launch. Locale tasks call
    this once at start; failures are ignored so concurrent FUSE races cannot
    fail the probe itself.
    """
    pending = audit_dir / (AIO_PENDING_FILE if mode == "aio" else PROMPT_PENDING_FILE)
    current = _read_json(pending)
    if not current:
        return
    current_batch = str(current.get("batch_id") or current.get("request_id") or "")
    if batch_id and current_batch and current_batch != batch_id:
        return
    if str(current.get("status") or "") == "running" and current.get("fanout"):
        return

    def mutator(data: dict[str, Any]) -> dict[str, Any] | None:
        cb = str(data.get("batch_id") or data.get("request_id") or "")
        if batch_id and cb and cb != batch_id:
            return None
        data["status"] = "running"
        data["fanout"] = True
        data["batch_id"] = batch_id or data.get("batch_id") or ""
        data["mode"] = mode
        data["updated_at"] = _utc_now()
        return data

    try:
        _update_json(pending, mutator)
    except TimeoutError:
        log.warning("Timed out touching pending running status under %s", audit_dir)
    except OSError as exc:
        if _is_transient_fs_error(exc, for_write=True):
            log.warning("Skipping pending touch after transient FS error: %s", exc)
        else:
            raise


def claim_fanout_finalize(
    audit_dir: Path,
    *,
    batch_id: str,
    claim: str,
) -> dict[str, Any] | None:
    """Try to claim merge/finalize under lock. Returns merged fanout if this claim won."""

    def mutator(data: dict[str, Any]) -> dict[str, Any] | None:
        if str(data.get("batch_id") or "") != batch_id:
            return None
        status = str(data.get("finalize_status") or "")
        if status in {"finalizing", "finalized"}:
            return None
        data["finalize_status"] = "finalizing"
        data["finalize_claim"] = claim
        data["updated_at"] = _utc_now()
        return data

    updated = _update_json(fanout_status_path(audit_dir), mutator)
    if not updated:
        return None
    if str(updated.get("finalize_claim") or "") != claim:
        return None
    # Return shard-merged view so finalize sees up-to-date locale statuses.
    return load_fanout_status(audit_dir) or updated


_FANOUT_IN_FLIGHT = frozenset({"queued", "starting", "running", "finalizing"})
_FANOUT_LOCALE_TERMINAL = frozenset({"completed", "failed", "launch_failed"})
_FANOUT_FINALIZE_DONE = frozenset(
    {"finalized", "blocked_partial_failure", "finalize_failed"}
)


def fanout_batch_is_idle(fanout: dict[str, Any] | None) -> bool:
    """True when shard-merged fan-out has no in-flight locale work left.

    Once every locale is terminal, the batch is idle even if ``finalize_status``
    is still ``pending`` / ``finalizing`` — hung merge must not keep the UI
    banner forever. Finalize-settled batches are idle regardless of locales.
    """
    if not fanout or not isinstance(fanout, dict):
        return False
    finalize = str(fanout.get("finalize_status") or "")
    if finalize in _FANOUT_FINALIZE_DONE:
        return True
    locales = fanout.get("locales") if isinstance(fanout.get("locales"), dict) else {}
    if not locales:
        # Empty locale map with no finalize terminal state is still starting / unknown.
        return False
    statuses = [
        str((entry or {}).get("status") or "")
        for entry in locales.values()
        if isinstance(entry, dict)
    ]
    if not statuses:
        return False
    # Locale work finished → idle (finalize may still be catching up / stuck).
    if all(st in _FANOUT_LOCALE_TERMINAL for st in statuses):
        return True
    if any(st in _FANOUT_IN_FLIGHT for st in statuses):
        return False
    return False


def reconcile_orphaned_fanout_locales(
    audit_dir: Path,
    fanout: dict[str, Any],
) -> dict[str, Any]:
    """Treat in-flight locales with a probe artifact as completed (orphan recovery).

    Locale jobs write the artifact before updating the fan-out shard to
    ``completed``. If the process dies between those steps, shards stay
    ``running`` forever and the Prompts banner never clears.
    """
    locales = fanout.get("locales") if isinstance(fanout.get("locales"), dict) else {}
    if not locales:
        return fanout
    updated = dict(fanout)
    new_locales: dict[str, Any] = {}
    changed = False
    for key, entry in locales.items():
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        st = str(item.get("status") or "")
        if st in _FANOUT_IN_FLIGHT and locale_probe_artifact_path(audit_dir, str(key)).is_file():
            item["status"] = "completed"
            item["orphan_reconciled"] = True
            changed = True
            try:
                _write_fanout_locale_shard(audit_dir, str(key), item)
            except OSError:
                pass
        new_locales[str(key)] = item
    if not changed:
        return fanout
    updated["locales"] = new_locales
    return updated


def aggregate_fanout_progress(fanout: dict[str, Any]) -> dict[str, Any]:
    """Build a UI-compatible probe_progress summary from fan-out status.

    Markets run in parallel, so ``planned_calls`` / ``completed_calls`` / ETA use
    wall-clock (max across locales), not the sum of all markets.
    """
    from api.probe_eta import estimate_probe_run_seconds, wall_clock_platform_calls

    locales = fanout.get("locales") if isinstance(fanout.get("locales"), dict) else {}
    locale_total = max(1, int(fanout.get("locale_total") or len(locales) or 1))
    locales_done = 0
    locales_failed = 0
    active_label = ""
    active_index = 1
    active_locale_completed = 0
    active_locale_planned = 0
    locale_statuses: list[str] = []
    per_locale_calls: list[tuple[int, int]] = []
    for key, entry in locales.items():
        if not isinstance(entry, dict):
            continue
        c = max(0, int(entry.get("completed_calls") or 0))
        p = max(0, int(entry.get("planned_calls") or 0))
        st = str(entry.get("status") or "")
        # Terminal locales contribute zero remaining work even if call counters
        # stopped short (skipped platforms / early exit).
        if st in _FANOUT_LOCALE_TERMINAL and p > 0:
            c = p
        per_locale_calls.append((c, p))
        locale_statuses.append(st)
        if st == "completed":
            locales_done += 1
        elif st in {"failed", "launch_failed"}:
            locales_failed += 1
        elif st in {"queued", "starting", "running"} and not active_label:
            active_label = str(entry.get("locale_label") or key)
            active_index = int(entry.get("locale_index") or 1)
            active_locale_completed = c
            active_locale_planned = p
    if not active_label and locales:
        # Prefer last updated running-ish or first key
        first_key = next(iter(locales))
        first = locales[first_key] if isinstance(locales[first_key], dict) else {}
        active_label = str(first.get("locale_label") or first_key)
        active_index = int(first.get("locale_index") or 1)
        active_locale_completed = max(0, int(first.get("completed_calls") or 0))
        active_locale_planned = max(0, int(first.get("planned_calls") or 0))

    # If planned not yet known for some locales, fill with max known per-locale.
    known_planned = [p for _, p in per_locale_calls if p > 0]
    if known_planned:
        per = max(known_planned)
        filled: list[tuple[int, int]] = []
        for c, p in per_locale_calls:
            filled.append((c, p if p > 0 else per))
        missing = max(0, locale_total - len(per_locale_calls))
        for _ in range(missing):
            filled.append((0, per))
        per_locale_calls = filled

    planned_calls, completed_calls, remaining_calls = wall_clock_platform_calls(
        per_locale_calls
    )

    finalize = str(fanout.get("finalize_status") or "")
    in_flight = any(st in {"queued", "starting", "running"} for st in locale_statuses)
    locales_terminal = bool(locale_statuses) and all(
        st in _FANOUT_LOCALE_TERMINAL for st in locale_statuses
    )
    if in_flight and not locales_terminal:
        status = "running"
    elif finalize == "finalized" or (locales_terminal and locales_failed == 0):
        status = "complete"
    elif finalize in {"blocked_partial_failure", "finalize_failed"} or (
        locales_terminal and locales_failed > 0
    ):
        status = "error"
    elif not locale_statuses and finalize in _FANOUT_FINALIZE_DONE:
        status = "complete" if finalize == "finalized" else "error"
    elif finalize == "finalizing" and not locales_terminal:
        status = "running"
    else:
        status = "running"

    eta_seconds: int | None = None
    eta_total_seconds: int | None = None
    if status == "running" and planned_calls > 0:
        eta_total_seconds = estimate_probe_run_seconds(planned_calls)
        if remaining_calls > 0:
            eta_seconds = estimate_probe_run_seconds(remaining_calls)
        elif completed_calls <= 0:
            eta_seconds = eta_total_seconds
    elif planned_calls > 0 and status in {"complete", "error"}:
        # Idle: clamp counters so UI never shows leftover ETA.
        completed_calls = planned_calls
        remaining_calls = 0

    return {
        "status": status,
        "market_count": locale_total,
        "locale_index": active_index,
        "locale_label": active_label,
        "locales_done": locales_done,
        "completed_calls": completed_calls,
        "planned_calls": planned_calls,
        "locale_completed_calls": active_locale_completed,
        "locale_planned_calls": active_locale_planned,
        "eta_seconds": eta_seconds,
        "eta_total_seconds": eta_total_seconds,
        "fanout": True,
        "finalize_status": finalize or None,
    }


def _execute_prompt_job(*, audit_id: str, request_id: str) -> str:
    from google.cloud import run_v2

    project = prompt_job_project()
    region = prompt_job_region()
    name = prompt_job_name()
    job_path = f"projects/{project}/locations/{region}/jobs/{name}"
    overrides = {
        "container_overrides": [
            {
                "env": [
                    {"name": "PROMPT_JOB_AUDIT_ID", "value": audit_id},
                    {"name": "PROMPT_JOB_REQUEST_ID", "value": request_id},
                ]
            }
        ],
        "task_count": 1,
    }
    operation = run_v2.JobsClient().run_job(
        request=run_v2.RunJobRequest(name=job_path, overrides=overrides)
    )
    accepted_name = _accepted_operation_name(operation, job_path, request_id)
    log.info("Accepted prompt probe operation %s for %s", accepted_name, audit_id)
    return accepted_name


def _accepted_operation_name(operation: Any, job_path: str, request_id: str) -> str:
    """Return metadata or LRO name without depending on version-specific metadata classes."""
    metadata_name = str(getattr(getattr(operation, "metadata", None), "name", "") or "")
    raw_operation = getattr(operation, "operation", None)
    operation_name = str(getattr(raw_operation, "name", "") or "")
    return metadata_name or operation_name or f"{job_path}/operations/accepted-{request_id}"

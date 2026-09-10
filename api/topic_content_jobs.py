"""Durable per-topic content-outline jobs (Cloud Run Job or local thread)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api import geo_services as geo

log = logging.getLogger(__name__)

REQUESTS_DIR = "topic_content_job_requests"
PENDING_FILE = "topic_content_job_states.json"
STATES_DIR = "topic_content_job_states"
CLAIMS_DIR = "topic_content_job_claims"
ACTIVE_STATES = {"queued", "starting", "running", "retrying"}


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def pending_path(audit_dir: Path) -> Path:
    """Legacy aggregate state path retained for backwards-compatible reads."""
    return audit_dir / PENDING_FILE


def topic_state_path(audit_dir: Path, topic: str) -> Path:
    digest = hashlib.sha256(topic.casefold().encode("utf-8")).hexdigest()
    return audit_dir / STATES_DIR / f"{digest}.json"


def request_path(audit_dir: Path, request_id: str) -> Path:
    return audit_dir / REQUESTS_DIR / f"{request_id}.json"


def context_path(audit_dir: Path, request_id: str) -> Path:
    return audit_dir / REQUESTS_DIR / f"{request_id}.context.json"


def topic_claim_path(audit_dir: Path, topic: str) -> Path:
    digest = hashlib.sha256(topic.casefold().encode("utf-8")).hexdigest()
    return audit_dir / CLAIMS_DIR / f"{digest}.json"


def _claim_payload(request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "created_at": _now(),
        "expires_at_epoch": time.time() + _active_ttl_seconds(),
    }


def _gcs_claim_blob(path: Path):
    bucket_name = (os.getenv("GCS_BUCKET") or "").strip()
    root = Path(os.getenv("GEO_DATA_ROOT") or "/var/geo-data").resolve()
    if not bucket_name:
        return None
    try:
        object_name = path.resolve().relative_to(root).as_posix()
    except ValueError:
        return None
    from google.cloud import storage

    return storage.Client(project=topic_content_job_project()).bucket(bucket_name).blob(object_name)


def _try_claim_topic(audit_dir: Path, topic: str, request_id: str) -> bool:
    path = topic_claim_path(audit_dir, topic)
    payload = _claim_payload(request_id)
    blob = _gcs_claim_blob(path)
    if blob is not None:
        from google.api_core.exceptions import NotFound, PreconditionFailed

        for _attempt in range(2):
            try:
                blob.upload_from_string(
                    json.dumps(payload),
                    content_type="application/json",
                    if_generation_match=0,
                )
                return True
            except PreconditionFailed:
                try:
                    blob.reload()
                    generation = blob.generation
                    current = json.loads(
                        blob.download_as_text(if_generation_match=generation)
                    )
                    expired = float(current.get("expires_at_epoch") or 0) < time.time()
                    if not expired:
                        return False
                    blob.delete(if_generation_match=generation)
                except (NotFound, PreconditionFailed, ValueError, TypeError, json.JSONDecodeError):
                    continue
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        current = _read_json(path) or {}
        if float(current.get("expires_at_epoch") or 0) >= time.time():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return _try_claim_topic(audit_dir, topic, request_id)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return True


def _release_topic_claim(audit_dir: Path, topic: str, request_id: str) -> None:
    path = topic_claim_path(audit_dir, topic)
    blob = _gcs_claim_blob(path)
    if blob is not None:
        from google.api_core.exceptions import NotFound, PreconditionFailed

        try:
            current = json.loads(blob.download_as_text())
            blob.reload()
            if str(current.get("request_id") or "") == request_id:
                blob.delete(if_generation_match=blob.generation)
        except (NotFound, PreconditionFailed, ValueError, TypeError, json.JSONDecodeError):
            pass
        return
    current = _read_json(path) or {}
    if str(current.get("request_id") or "") == request_id:
        try:
            path.unlink()
        except OSError:
            pass


def _topic_states(audit_dir: Path) -> dict[str, Any]:
    states: dict[str, Any] = {}
    payload = _read_json(pending_path(audit_dir)) or {}
    topics = payload.get("topics")
    if isinstance(topics, dict):
        states.update(
            (str(topic), state)
            for topic, state in topics.items()
            if isinstance(state, dict)
        )
    state_dir = audit_dir / STATES_DIR
    if state_dir.is_dir():
        for path in state_dir.iterdir():
            if path.suffix != ".json":
                continue
            state = _read_json(path)
            topic = str((state or {}).get("topic") or "")
            if topic and state:
                states[topic] = state
    return states


def _set_topic_state(
    audit_dir: Path,
    topic: str,
    state: dict[str, Any],
    *,
    expected_request_id: str | None = None,
) -> bool:
    path = topic_state_path(audit_dir, topic)
    if expected_request_id:
        current = _read_json(path)
        if current and str(current.get("request_id") or "") != expected_request_id:
            return False
        if (
            current
            and str(current.get("status") or "") in {"done", "error"}
            and str(state.get("status") or "") in ACTIVE_STATES
        ):
            return False
    _write_json(path, state)
    return True


def _active_ttl_seconds() -> int:
    try:
        return max(300, int(os.getenv("TOPIC_CONTENT_ACTIVE_TTL_SECONDS") or "2400"))
    except ValueError:
        return 2400


def _normalized_state(state: dict[str, Any]) -> dict[str, Any]:
    if str(state.get("status") or "") not in ACTIVE_STATES:
        return state
    try:
        updated = datetime.fromisoformat(str(state.get("updated_at") or "").replace("Z", "+00:00"))
        age = (datetime.now(UTC) - updated.astimezone(UTC)).total_seconds()
    except (TypeError, ValueError):
        age = _active_ttl_seconds() + 1
    if age <= _active_ttl_seconds():
        return state
    return {
        **state,
        "status": "error",
        "error": "Generation timed out. Retry this topic.",
    }


def get_topic_content_job_states(audit_dir: Path) -> dict[str, Any]:
    return {
        topic: _normalized_state(state)
        for topic, state in _topic_states(audit_dir.resolve()).items()
    }


def _request_is_current(audit_dir: Path, topic: str, request_id: str) -> bool:
    current = _read_json(topic_state_path(audit_dir, topic))
    return not current or str(current.get("request_id") or "") == request_id


def topic_content_job_name() -> str:
    return (os.getenv("TOPIC_CONTENT_JOB_NAME") or "").strip()


def topic_content_job_region() -> str:
    return (
        os.getenv("TOPIC_CONTENT_JOB_REGION")
        or os.getenv("CLOUD_RUN_REGION")
        or "europe-west1"
    ).strip()


def topic_content_job_project() -> str:
    project = (
        os.getenv("TOPIC_CONTENT_JOB_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or ""
    ).strip()
    if not project:
        raise RuntimeError("TOPIC_CONTENT_JOB_PROJECT is not configured")
    return project


def topic_content_jobs_enabled() -> bool:
    force_local = (os.getenv("TOPIC_CONTENT_FORCE_LOCAL") or "").strip().lower()
    return bool(topic_content_job_name()) and force_local not in {"1", "true", "yes"}


def _max_attempts() -> int:
    try:
        return max(1, min(5, int(os.getenv("TOPIC_CONTENT_MAX_ATTEMPTS") or "2")))
    except ValueError:
        return 2


def _update_manifest(audit_dir: Path, request_id: str, **updates: Any) -> None:
    path = request_path(audit_dir, request_id)
    manifest = _read_json(path) or {"request_id": request_id}
    current_status = str(manifest.get("status") or "")
    next_status = str(updates.get("status") or "")
    if current_status in {"done", "error"} and next_status in {
        "queued",
        "started",
        "running",
        "retrying",
    }:
        return
    manifest.update(updates)
    manifest["updated_at"] = _now()
    _write_json(path, manifest)


def run_topic_content_generation(
    audit_dir: Path,
    *,
    topic: str,
    request_id: str,
    structure: Any = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Generate one topic sample with persisted retry/error lifecycle."""
    from topic_content_generator import generate_topic_outline, refresh_evidence

    audit_dir = audit_dir.resolve()
    last_error: Exception | None = None
    for attempt in range(1, _max_attempts() + 1):
        status = "running" if attempt == 1 else "retrying"
        state = {
            "topic": topic,
            "request_id": request_id,
            "status": status,
            "attempt": attempt,
            "execution": str((_read_json(request_path(audit_dir, request_id)) or {}).get("execution") or ""),
            "updated_at": _now(),
            "error": None,
        }
        if not _set_topic_state(
            audit_dir,
            topic,
            state,
            expected_request_id=request_id,
        ):
            _release_topic_claim(audit_dir, topic, request_id)
            raise RuntimeError("Topic generation was superseded by a newer request")
        _update_manifest(audit_dir, request_id, status=status, attempt=attempt)
        try:
            if refresh:
                refresh_evidence(audit_dir)
            sample = generate_topic_outline(
                audit_dir,
                topic,
                structure=structure,
                commit_guard=lambda: _request_is_current(audit_dir, topic, request_id),
            )
            done = {**state, "status": "done", "updated_at": _now(), "error": None}
            _set_topic_state(
                audit_dir,
                topic,
                done,
                expected_request_id=request_id,
            )
            _update_manifest(audit_dir, request_id, status="done", outcome={"topic": topic})
            _release_topic_claim(audit_dir, topic, request_id)
            return sample
        except Exception as exc:
            last_error = exc
            log.exception(
                "Topic content attempt %s/%s failed for %s / %s",
                attempt,
                _max_attempts(),
                geo.audit_dir_api_rel(audit_dir),
                topic,
            )
            _update_manifest(audit_dir, request_id, status="retrying", error=str(exc))
            if attempt < _max_attempts():
                time.sleep(min(2 ** (attempt - 1), 4))

    message = str(last_error or "Topic content generation failed")
    failed = {
        "topic": topic,
        "request_id": request_id,
        "status": "error",
        "attempt": _max_attempts(),
        "execution": str((_read_json(request_path(audit_dir, request_id)) or {}).get("execution") or ""),
        "updated_at": _now(),
        "error": message,
    }
    _set_topic_state(
        audit_dir,
        topic,
        failed,
        expected_request_id=request_id,
    )
    _update_manifest(audit_dir, request_id, status="error", error=message)
    _release_topic_claim(audit_dir, topic, request_id)
    raise RuntimeError(message) from last_error


def enqueue_topic_content_job(
    audit_dir: Path,
    *,
    topic: str,
    structure: Any = None,
    refresh: bool = False,
) -> dict[str, Any]:
    audit_dir = audit_dir.resolve()
    audit_id = geo.audit_dir_api_rel(audit_dir)
    states = get_topic_content_job_states(audit_dir)
    active = states.get(topic) if isinstance(states.get(topic), dict) else None
    if active and str(active.get("status") or "") in ACTIVE_STATES:
        return {
            **active,
            "audit_id": audit_id,
            "already_running": True,
        }

    request_id = uuid.uuid4().hex
    if not _try_claim_topic(audit_dir, topic, request_id):
        current = get_topic_content_job_states(audit_dir).get(topic) or {}
        return {
            **current,
            "topic": topic,
            "status": current.get("status") or "queued",
            "request_id": current.get("request_id") or "",
            "audit_id": audit_id,
            "already_running": True,
        }
    manifest = {
        "request_id": request_id,
        "audit_id": audit_id,
        "topic": topic,
        "refresh": bool(refresh),
        "status": "queued",
        "attempt": 0,
        "execution": "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    _write_json(request_path(audit_dir, request_id), manifest)
    serialized_path = ""
    if structure is not None:
        serialized = context_path(audit_dir, request_id)
        _write_json(serialized, {"structure": structure})
        serialized_path = str(serialized)
        manifest["context_path"] = serialized_path
        _write_json(request_path(audit_dir, request_id), manifest)
    state = {
        "topic": topic,
        "request_id": request_id,
        "status": "queued",
        "attempt": 0,
        "execution": "",
        "created_at": _now(),
        "updated_at": _now(),
        "error": None,
    }
    _set_topic_state(audit_dir, topic, state)

    if topic_content_jobs_enabled():
        try:
            _execute_topic_content_job(
                audit_id=audit_id,
                topic=topic,
                request_id=request_id,
                context_file=serialized_path,
                refresh=refresh,
            )
            current = _read_json(topic_state_path(audit_dir, topic)) or state
            return {**current, "audit_id": audit_id, "already_running": False}
        except Exception as exc:
            log.exception("Cloud Run topic-content launch failed")
            _update_manifest(audit_dir, request_id, status="launch_failed", error=str(exc))
            current = _read_json(topic_state_path(audit_dir, topic)) or state
            if str(current.get("status") or "") in {"running", "retrying", "done"}:
                return {**current, "audit_id": audit_id, "already_running": False}
            failed = {
                **state,
                "status": "error",
                "updated_at": _now(),
                "error": f"Could not start generation job: {exc}",
            }
            _set_topic_state(
                audit_dir,
                topic,
                failed,
                expected_request_id=request_id,
            )
            _release_topic_claim(audit_dir, topic, request_id)
            raise RuntimeError(f"Could not start topic content job: {exc}") from exc

    def worker() -> None:
        try:
            run_topic_content_generation(
                audit_dir,
                topic=topic,
                request_id=request_id,
                structure=structure,
                refresh=refresh,
            )
        except Exception:
            log.exception("Local topic-content job failed for %s / %s", audit_id, topic)

    state.update({"execution": "local-thread", "updated_at": _now()})
    _set_topic_state(
        audit_dir,
        topic,
        state,
        expected_request_id=request_id,
    )
    _update_manifest(audit_dir, request_id, status="started", execution="local-thread")
    threading.Thread(
        target=worker,
        name=f"topic-content-{request_id[:8]}",
        daemon=True,
    ).start()
    return {**state, "audit_id": audit_id, "already_running": False}


def enqueue_lowest_topic_content_job(audit_dir: Path) -> dict[str, Any] | None:
    """Ensure the audit's weakest measured topic has its single default sample."""
    from topic_content_generator import ensure_evidence

    audit_dir = audit_dir.resolve()
    artifact = ensure_evidence(audit_dir)
    topics = artifact.get("topics") if isinstance(artifact.get("topics"), dict) else {}
    candidates = [
        entry
        for entry in topics.values()
        if isinstance(entry, dict) and isinstance(entry.get("evidence"), dict)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda entry: (
            float(entry["evidence"].get("visibility") or 0),
            str(entry["evidence"].get("topic") or "").casefold(),
        )
    )
    lowest = candidates[0]
    topic = str(lowest["evidence"].get("topic") or "")
    if not topic:
        return None
    if lowest.get("sample") is not None:
        return {"topic": topic, "status": "done", "cached": True}
    return enqueue_topic_content_job(audit_dir, topic=topic)


def _execute_topic_content_job(
    *,
    audit_id: str,
    topic: str,
    request_id: str,
    context_file: str = "",
    refresh: bool = False,
) -> str:
    from google.cloud import run_v2

    project = topic_content_job_project()
    region = topic_content_job_region()
    name = topic_content_job_name()
    job_path = f"projects/{project}/locations/{region}/jobs/{name}"
    env = [
        {"name": "TOPIC_CONTENT_AUDIT_ID", "value": audit_id},
        {"name": "TOPIC_CONTENT_TOPIC", "value": topic},
        {"name": "TOPIC_CONTENT_REQUEST_ID", "value": request_id},
        {"name": "TOPIC_CONTENT_REFRESH", "value": "1" if refresh else "0"},
    ]
    if context_file:
        env.append({"name": "TOPIC_CONTENT_CONTEXT_PATH", "value": context_file})
    operation = run_v2.JobsClient().run_job(
        request=run_v2.RunJobRequest(
            name=job_path,
            overrides={"container_overrides": [{"env": env}], "task_count": 1},
        )
    )
    metadata_name = str(getattr(getattr(operation, "metadata", None), "name", "") or "")
    raw_operation = getattr(operation, "operation", None)
    operation_name = str(getattr(raw_operation, "name", "") or "")
    return metadata_name or operation_name or f"{job_path}/operations/accepted-{request_id}"

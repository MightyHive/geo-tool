"""Launch durable Cloud Run Job executions for brand and competitor crawls."""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from api import geo_services as geo

log = logging.getLogger(__name__)

CrawlJobMode = Literal["full_audit", "competitors_only"]
CRAWL_JOB_REQUESTS_DIR = "crawl_job_requests"
FULL_AUDIT_PENDING_FILE = "audit_crawl_pending.json"
COMPETITOR_CRAWL_PENDING_FILE = "competitor_crawl_pending.json"
GA4_ADC_FILENAME = "ga4_adc.json"
# Cloud Run currently enforces a 2h task timeout. Allow a short control-plane
# buffer, then stop trusting an active marker even if a killed container never
# reached its ``finally`` cleanup.
DEFAULT_CRAWL_JOB_PENDING_MAX_AGE_SECONDS = (2 * 60 * 60) + (15 * 60)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def crawl_job_name() -> str:
    return (os.getenv("AUDIT_CRAWL_JOB_NAME") or "geo-audit-site-crawls").strip()


def crawl_job_region() -> str:
    return (
        os.getenv("AUDIT_CRAWL_JOB_REGION")
        or os.getenv("CLOUD_RUN_REGION")
        or "europe-west1"
    ).strip()


def crawl_job_project() -> str:
    project = (
        os.getenv("AUDIT_CRAWL_JOB_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or ""
    ).strip()
    if not project:
        raise RuntimeError("AUDIT_CRAWL_JOB_PROJECT is not configured")
    return project


def crawl_jobs_enabled() -> bool:
    """Use Cloud Run Job when AUDIT_CRAWL_JOB_NAME is set; otherwise local threads."""
    force_local = (os.getenv("AUDIT_CRAWL_FORCE_LOCAL") or "").strip().lower()
    if force_local in {"1", "true", "yes"}:
        return False
    return bool((os.getenv("AUDIT_CRAWL_JOB_NAME") or "").strip())


def _pending_path(audit_dir: Path, mode: CrawlJobMode) -> Path:
    return audit_dir / (
        COMPETITOR_CRAWL_PENDING_FILE if mode == "competitors_only" else FULL_AUDIT_PENDING_FILE
    )


def _active_request(pending: Path) -> dict[str, Any] | None:
    payload = _read_json(pending)
    if not payload:
        return None
    if str(payload.get("status") or "") in {"queued", "starting", "running"}:
        return payload
    return None


def crawl_job_pending_max_age_seconds() -> int:
    raw = (os.getenv("AUDIT_CRAWL_PENDING_MAX_AGE_SECONDS") or "").strip()
    if raw:
        try:
            return max(60, int(raw))
        except ValueError:
            log.warning(
                "Ignoring invalid AUDIT_CRAWL_PENDING_MAX_AGE_SECONDS=%r",
                raw,
            )
    return DEFAULT_CRAWL_JOB_PENDING_MAX_AGE_SECONDS


def reconcile_crawl_job_pending(
    audit_dir: Path,
    *,
    mode: CrawlJobMode,
) -> dict[str, Any] | None:
    """Clear terminal or over-age crawl markers left by killed Cloud Run tasks."""
    pending_path = _pending_path(audit_dir, mode)
    pending = _read_json(pending_path)
    if not pending:
        return None
    if str(pending.get("status") or "") not in {"queued", "starting", "running"}:
        return pending

    request_id = str(pending.get("request_id") or "").strip()
    manifest_path = (
        audit_dir / CRAWL_JOB_REQUESTS_DIR / f"{request_id}.json"
        if request_id
        else None
    )
    manifest = _read_json(manifest_path) if manifest_path is not None else None
    manifest_status = str((manifest or {}).get("status") or "").strip().lower()

    if manifest_status == "completed":
        pending_path.unlink(missing_ok=True)
        return {**pending, "status": "completed", "reconciled": True}

    failure_statuses = {"failed", "launch_failed", "cancelled", "error"}
    failure = manifest_status in failure_statuses
    age = geo.running_status_age_seconds(pending)
    over_age = age is not None and age >= crawl_job_pending_max_age_seconds()
    if not failure and not over_age:
        return pending

    if failure:
        error = str((manifest or {}).get("error") or "Crawl job failed.")
    else:
        error = (
            "Crawl job exceeded its maximum runtime or the worker stopped "
            "before publishing a terminal status."
        )
        if manifest is not None and manifest_path is not None:
            manifest.update(
                {
                    "status": "failed",
                    "error": error,
                    "completed_at": _utc_now(),
                    "reconciled": True,
                }
            )
            _write_json(manifest_path, manifest)

    log.warning(
        "Clearing failed crawl marker for %s (mode=%s, request=%s, age=%s)",
        geo.audit_dir_api_rel(audit_dir),
        mode,
        request_id or "unknown",
        f"{int(age)}s" if age is not None else "unknown",
    )
    pending_path.unlink(missing_ok=True)
    return {
        **pending,
        "status": "failed",
        "error": error,
        "completed_at": _utc_now(),
        "reconciled": True,
    }


def persist_ga4_adc(audit_dir: Path, source_path: str | Path | None) -> str | None:
    """Copy temp GA4 ADC JSON into the audit dir so the Job can read it from GCS."""
    if not source_path:
        return None
    src = Path(source_path)
    if not src.is_file():
        return None
    dest_dir = audit_dir / ".runtime"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / GA4_ADC_FILENAME
    shutil.copy2(src, dest)
    try:
        dest.chmod(0o600)
    except OSError:
        pass
    return str(dest.relative_to(audit_dir))


def enqueue_crawl_job(
    audit_dir: Path,
    *,
    mode: CrawlJobMode,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a request manifest, launch the Job, and return immediately."""
    audit_dir = audit_dir.resolve()
    audit_id = geo.audit_dir_api_rel(audit_dir)
    pending = _pending_path(audit_dir, mode)
    active = _active_request(pending)
    if active:
        return {
            "status": str(active.get("status") or "queued"),
            "audit_id": audit_id,
            "request_id": str(active.get("request_id") or ""),
            "execution": str(active.get("execution") or ""),
            "already_running": True,
            "mode": mode,
        }

    request_id = uuid.uuid4().hex
    manifest_path = audit_dir / CRAWL_JOB_REQUESTS_DIR / f"{request_id}.json"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "request_id": request_id,
        "audit_id": audit_id,
        "mode": mode,
        "payload": payload or {},
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
        execution = _execute_crawl_job(audit_id=audit_id, request_id=request_id)
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
        "mode": mode,
    }


def _execute_crawl_job(*, audit_id: str, request_id: str) -> str:
    from google.cloud import run_v2

    project = crawl_job_project()
    region = crawl_job_region()
    name = crawl_job_name()
    job_path = f"projects/{project}/locations/{region}/jobs/{name}"
    overrides = {
        "container_overrides": [
            {
                "env": [
                    {"name": "CRAWL_JOB_AUDIT_ID", "value": audit_id},
                    {"name": "CRAWL_JOB_REQUEST_ID", "value": request_id},
                ]
            }
        ],
        "task_count": 1,
    }
    operation = run_v2.JobsClient().run_job(
        request=run_v2.RunJobRequest(name=job_path, overrides=overrides)
    )
    accepted_name = _accepted_operation_name(operation, job_path, request_id)
    log.info("Accepted crawl job operation %s for %s", accepted_name, audit_id)
    return accepted_name


def _accepted_operation_name(operation: Any, job_path: str, request_id: str) -> str:
    metadata_name = str(getattr(getattr(operation, "metadata", None), "name", "") or "")
    raw_operation = getattr(operation, "operation", None)
    operation_name = str(getattr(raw_operation, "name", "") or "")
    return metadata_name or operation_name or f"{job_path}/operations/accepted-{request_id}"

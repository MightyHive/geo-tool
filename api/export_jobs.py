"""Enqueue durable PDF export jobs (Cloud Run Job or local thread fallback)."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api import geo_services as geo

log = logging.getLogger(__name__)

EXPORT_JOB_REQUESTS_DIR = "export_job_requests"
PDF_EXPORT_PENDING_FILE = "pdf_export_pending.json"
PDF_EXPORTS_DIR = "exports"


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


def pdf_export_job_name() -> str:
    return (os.getenv("PDF_EXPORT_JOB_NAME") or "").strip()


def pdf_export_job_region() -> str:
    return (
        os.getenv("PDF_EXPORT_JOB_REGION")
        or os.getenv("CLOUD_RUN_REGION")
        or "europe-west1"
    ).strip()


def pdf_export_job_project() -> str:
    return (
        os.getenv("PDF_EXPORT_JOB_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or "emea-ds-sandbox"
    ).strip()


def pdf_export_jobs_enabled() -> bool:
    force_local = (os.getenv("PDF_EXPORT_FORCE_LOCAL") or "").strip().lower()
    if force_local in {"1", "true", "yes"}:
        return False
    return bool(pdf_export_job_name())


def _section_key(section: str | None) -> str:
    sec = (section or "").strip()
    return sec or "full"


def pending_path(audit_dir: Path, section: str | None = None) -> Path:
    key = _section_key(section)
    if key == "full":
        return audit_dir / PDF_EXPORT_PENDING_FILE
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in key)
    return audit_dir / f"pdf_export_pending_{safe}.json"


def artifact_path(audit_dir: Path, section: str | None = None) -> Path:
    key = _section_key(section)
    exports = audit_dir / PDF_EXPORTS_DIR
    if key == "full":
        return exports / "full-report.pdf"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in key)
    return exports / f"section-{safe}.pdf"


def _active_request(pending: Path) -> dict[str, Any] | None:
    payload = _read_json(pending)
    if not payload:
        return None
    if str(payload.get("status") or "") in {"queued", "starting", "running"}:
        return payload
    return None


def get_pdf_export_status(audit_dir: Path, *, section: str | None = None) -> dict[str, Any]:
    audit_dir = audit_dir.resolve()
    audit_id = geo.audit_dir_api_rel(audit_dir)
    pending = pending_path(audit_dir, section)
    artifact = artifact_path(audit_dir, section)
    payload = _read_json(pending) or {}
    status = str(payload.get("status") or "")
    ready = artifact.is_file() and status in {"", "done", "ready"}
    if artifact.is_file() and status not in {"queued", "starting", "running", "error", "launch_failed"}:
        ready = True
        status = status or "ready"
    return {
        "audit_id": audit_id,
        "section": _section_key(section),
        "status": status or ("ready" if artifact.is_file() else "idle"),
        "ready": ready,
        "request_id": str(payload.get("request_id") or ""),
        "execution": str(payload.get("execution") or ""),
        "error": str(payload.get("error") or "") or None,
        "updated_at": payload.get("updated_at") or payload.get("created_at"),
        "artifact": str(artifact.relative_to(audit_dir)) if artifact.is_file() else None,
        "bytes": artifact.stat().st_size if artifact.is_file() else None,
    }


def run_pdf_export(audit_dir: Path, *, section: str | None = None, request_id: str = "") -> Path:
    """Generate the PDF artifact and mark pending status done."""
    from api.pdf_service import generate_audit_pdf, generate_section_pdf

    audit_dir = audit_dir.resolve()
    pending = pending_path(audit_dir, section)
    artifact = artifact_path(audit_dir, section)
    current = _read_json(pending) or {}
    if request_id and str(current.get("request_id") or "") not in {"", request_id}:
        log.info("Skipping stale PDF export request %s (active %s)", request_id, current.get("request_id"))
        return artifact

    current.update({"status": "running", "updated_at": _utc_now()})
    if request_id:
        current["request_id"] = request_id
    _write_json(pending, current)

    try:
        if section and section.strip():
            pdf_bytes = generate_section_pdf(audit_dir, section.strip())
        else:
            pdf_bytes = generate_audit_pdf(audit_dir)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        tmp = artifact.with_suffix(artifact.suffix + ".tmp")
        tmp.write_bytes(pdf_bytes)
        tmp.replace(artifact)
        current.update(
            {
                "status": "ready",
                "updated_at": _utc_now(),
                "artifact": str(artifact.relative_to(audit_dir)),
                "bytes": len(pdf_bytes),
                "error": None,
            }
        )
        _write_json(pending, current)
        return artifact
    except Exception as exc:
        current.update({"status": "error", "error": str(exc), "updated_at": _utc_now()})
        _write_json(pending, current)
        raise


def enqueue_pdf_export(
    audit_dir: Path,
    *,
    section: str | None = None,
) -> dict[str, Any]:
    """Queue PDF generation and return immediately."""
    audit_dir = audit_dir.resolve()
    audit_id = geo.audit_dir_api_rel(audit_dir)
    sec = _section_key(section)
    pending = pending_path(audit_dir, section)
    active = _active_request(pending)
    if active:
        return {
            "status": str(active.get("status") or "queued"),
            "audit_id": audit_id,
            "section": sec,
            "request_id": str(active.get("request_id") or ""),
            "execution": str(active.get("execution") or ""),
            "already_running": True,
        }

    request_id = uuid.uuid4().hex
    manifest_path = audit_dir / EXPORT_JOB_REQUESTS_DIR / f"{request_id}.json"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "request_id": request_id,
        "audit_id": audit_id,
        "kind": "pdf",
        "section": sec if sec != "full" else "",
        "status": "queued",
        "created_at": _utc_now(),
    }
    _write_json(manifest_path, manifest)
    _write_json(
        pending,
        {
            "status": "starting",
            "request_id": request_id,
            "section": sec,
            "created_at": manifest["created_at"],
        },
    )

    if pdf_export_jobs_enabled():
        try:
            execution = _execute_pdf_export_job(audit_id=audit_id, request_id=request_id)
        except Exception as exc:
            manifest.update({"status": "launch_failed", "error": str(exc), "updated_at": _utc_now()})
            _write_json(manifest_path, manifest)
            pending.unlink(missing_ok=True)
            raise
        manifest.update({"status": "started", "execution": execution, "updated_at": _utc_now()})
        _write_json(manifest_path, manifest)
        current = _read_json(pending) or {}
        if str(current.get("request_id") or "") == request_id:
            current.update({"status": "queued", "execution": execution})
            _write_json(pending, current)
        return {
            "status": "queued",
            "audit_id": audit_id,
            "section": sec,
            "request_id": request_id,
            "execution": execution,
            "already_running": False,
        }

    # Local / no job configured: run in a background thread on the service.
    def _worker() -> None:
        try:
            run_pdf_export(audit_dir, section=section if sec != "full" else None, request_id=request_id)
        except Exception:
            log.exception("Local PDF export failed for %s section=%s", audit_id, sec)

    threading.Thread(target=_worker, name=f"pdf-export-{request_id[:8]}", daemon=True).start()
    return {
        "status": "queued",
        "audit_id": audit_id,
        "section": sec,
        "request_id": request_id,
        "execution": "local-thread",
        "already_running": False,
    }


def _execute_pdf_export_job(*, audit_id: str, request_id: str) -> str:
    from google.cloud import run_v2

    project = pdf_export_job_project()
    region = pdf_export_job_region()
    name = pdf_export_job_name()
    job_path = f"projects/{project}/locations/{region}/jobs/{name}"
    overrides = {
        "container_overrides": [
            {
                "env": [
                    {"name": "PDF_EXPORT_AUDIT_ID", "value": audit_id},
                    {"name": "PDF_EXPORT_REQUEST_ID", "value": request_id},
                ]
            }
        ],
        "task_count": 1,
    }
    operation = run_v2.JobsClient().run_job(
        request=run_v2.RunJobRequest(name=job_path, overrides=overrides)
    )
    metadata_name = str(getattr(getattr(operation, "metadata", None), "name", "") or "")
    raw_operation = getattr(operation, "operation", None)
    operation_name = str(getattr(raw_operation, "name", "") or "")
    return metadata_name or operation_name or f"{job_path}/operations/accepted-{request_id}"

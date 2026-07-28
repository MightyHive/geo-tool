"""Cloud Run Job entrypoint for durable brand and competitor site crawls."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from api import geo_services as geo
from api.crawl_jobs import (
    COMPETITOR_CRAWL_PENDING_FILE,
    CRAWL_JOB_REQUESTS_DIR,
    FULL_AUDIT_PENDING_FILE,
    _read_json,
    _utc_now,
    _write_json,
)

log = logging.getLogger(__name__)


class _CloudRunJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_CloudRunJsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def _ga4_cred_path(audit_dir: Path, payload: dict[str, Any]) -> str | None:
    rel = str(payload.get("ga4_adc_relpath") or "").strip()
    if not rel:
        return None
    path = (audit_dir / rel).resolve()
    try:
        path.relative_to(audit_dir.resolve())
    except ValueError:
        log.warning("Ignoring ga4_adc_relpath outside audit dir: %s", rel)
        return None
    return str(path) if path.is_file() else None


def _cleanup_ga4_adc(audit_dir: Path, payload: dict[str, Any]) -> None:
    rel = str(payload.get("ga4_adc_relpath") or "").strip()
    if not rel:
        return
    path = audit_dir / rel
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _run_full_audit(audit_dir: Path, payload: dict[str, Any]) -> None:
    from api.audit_runner import _run_audit_job

    ga4_cred_path = _ga4_cred_path(audit_dir, payload)
    try:
        _run_audit_job(
            audit_dir=audit_dir,
            primary=str(payload.get("primary") or ""),
            competitors=[
                str(item).strip()
                for item in (payload.get("competitors") or [])
                if str(item).strip()
            ],
            body_dict=dict(payload.get("body_dict") or {}),
            ga4_prop=str(payload.get("ga4_prop") or "") or None,
            ga4_ch=str(payload.get("ga4_ch") or "") or None,
            ga4_cred_path=ga4_cred_path,
            owner_email=str(payload.get("owner_email") or "") or None,
            notification_email=str(payload.get("notification_email") or "") or None,
            stream_progress=True,
        )
    finally:
        _cleanup_ga4_adc(audit_dir, payload)


def _run_competitors_only(audit_dir: Path) -> None:
    from api.audit_runner import run_competitor_crawl_job

    run_competitor_crawl_job(audit_dir)


def run() -> None:
    audit_id = (os.getenv("CRAWL_JOB_AUDIT_ID") or "").strip()
    request_id = (os.getenv("CRAWL_JOB_REQUEST_ID") or "").strip()
    if not audit_id or not request_id:
        raise ValueError("CRAWL_JOB_AUDIT_ID and CRAWL_JOB_REQUEST_ID are required")

    audit_dir = geo.resolve_audit_dir(audit_id)
    manifest_path = audit_dir / CRAWL_JOB_REQUESTS_DIR / f"{request_id}.json"
    manifest = _read_json(manifest_path)
    if not manifest:
        raise FileNotFoundError(f"Crawl job manifest not found: {manifest_path}")
    mode = str(manifest.get("mode") or "")
    pending = audit_dir / (
        COMPETITOR_CRAWL_PENDING_FILE if mode == "competitors_only" else FULL_AUDIT_PENDING_FILE
    )
    manifest.pop("error", None)
    manifest.update({"status": "running", "started_at": _utc_now()})
    _write_json(manifest_path, manifest)
    _write_json(
        pending,
        {
            "status": "running",
            "request_id": request_id,
            "mode": mode,
            "execution": str(manifest.get("execution") or ""),
            "started_at": manifest["started_at"],
        },
    )

    try:
        payload = dict(manifest.get("payload") or {})
        if mode == "full_audit":
            _run_full_audit(audit_dir, payload)
        elif mode == "competitors_only":
            _run_competitors_only(audit_dir)
        else:
            raise ValueError(f"Unsupported crawl job mode: {mode}")
        manifest.update({"status": "completed", "completed_at": _utc_now()})
        _write_json(manifest_path, manifest)
    except Exception as exc:
        manifest.update({"status": "failed", "error": str(exc), "completed_at": _utc_now()})
        _write_json(manifest_path, manifest)
        raise
    finally:
        current = _read_json(pending) or {}
        if str(current.get("request_id") or "") == request_id:
            pending.unlink(missing_ok=True)


if __name__ == "__main__":
    _configure_logging()
    run()

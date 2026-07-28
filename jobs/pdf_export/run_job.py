"""Cloud Run Job entrypoint for durable PDF export generation."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from api import geo_services as geo
from api.export_jobs import (
    EXPORT_JOB_REQUESTS_DIR,
    _read_json,
    _utc_now,
    _write_json,
    run_pdf_export,
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


def main() -> int:
    _configure_logging()
    audit_id = (os.getenv("PDF_EXPORT_AUDIT_ID") or "").strip()
    request_id = (os.getenv("PDF_EXPORT_REQUEST_ID") or "").strip()
    if not audit_id or not request_id:
        log.error("PDF_EXPORT_AUDIT_ID and PDF_EXPORT_REQUEST_ID are required")
        return 2

    audit_dir = geo.resolve_audit_dir(audit_id)
    manifest_path = audit_dir / EXPORT_JOB_REQUESTS_DIR / f"{request_id}.json"
    manifest = _read_json(manifest_path) or {}
    section = str(manifest.get("section") or "").strip() or None

    log.info("Starting PDF export audit=%s request=%s section=%s", audit_id, request_id, section or "full")
    try:
        path = run_pdf_export(audit_dir, section=section, request_id=request_id)
        if manifest_path.is_file():
            manifest.update({"status": "done", "updated_at": _utc_now(), "artifact": str(path.name)})
            _write_json(manifest_path, manifest)
        log.info("PDF export complete: %s", path)
        return 0
    except Exception:
        log.exception("PDF export failed")
        if manifest_path.is_file():
            manifest = _read_json(manifest_path) or manifest
            manifest.update({"status": "error", "updated_at": _utc_now()})
            _write_json(manifest_path, manifest)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

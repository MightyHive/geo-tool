"""Cloud Run Job entrypoint for Gemini content-quality overlay.

Persists ``content_quality_gemini.json`` so the API/UI can merge qualitative
E-E-A-T / answerability scores without recomputing on page load.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from api import geo_services as geo
from api.content_quality_jobs import (
    CONTENT_QUALITY_JOB_REQUESTS_DIR,
    _read_json,
    _utc_now,
    _write_json,
    run_content_quality_analysis,
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
    audit_id = (os.getenv("CONTENT_QUALITY_AUDIT_ID") or "").strip()
    request_id = (os.getenv("CONTENT_QUALITY_REQUEST_ID") or "").strip()
    if not audit_id or not request_id:
        log.error("CONTENT_QUALITY_AUDIT_ID and CONTENT_QUALITY_REQUEST_ID are required")
        return 2

    brand_name = (os.getenv("CONTENT_QUALITY_BRAND_NAME") or "").strip()
    site_url = (os.getenv("CONTENT_QUALITY_SITE_URL") or "").strip()
    cap_raw = (os.getenv("CONTENT_QUALITY_SAMPLE_CAP_OVERRIDE") or "").strip()
    sample_cap: int | None = None
    if cap_raw:
        try:
            sample_cap = int(cap_raw)
        except ValueError:
            sample_cap = None

    audit_dir = geo.resolve_audit_dir(audit_id)
    manifest_path = audit_dir / CONTENT_QUALITY_JOB_REQUESTS_DIR / f"{request_id}.json"
    manifest = _read_json(manifest_path) or {}

    log.info(
        "Starting content-quality Gemini audit=%s request=%s brand=%s",
        audit_id,
        request_id,
        brand_name or "(from context)",
    )
    try:
        outcome = run_content_quality_analysis(
            audit_dir,
            request_id=request_id,
            brand_name=brand_name,
            site_url=site_url,
            sample_cap=sample_cap,
        )
        cq_status = str(outcome.get("content_quality") or "")
        # Soft-skip empty crawls (no_pages) so competitor pipeline pending clears.
        ok = cq_status in {"done", "skipped"}
        if manifest_path.is_file():
            manifest.update(
                {
                    "status": "done" if ok else "error",
                    "updated_at": _utc_now(),
                    "outcome": outcome,
                }
            )
            _write_json(manifest_path, manifest)
        log.info("Content-quality Gemini complete: %s", outcome)
        return 0 if ok else 1
    except Exception:
        log.exception("Content-quality Gemini job failed")
        if manifest_path.is_file():
            manifest = _read_json(manifest_path) or manifest
            manifest.update({"status": "error", "updated_at": _utc_now()})
            _write_json(manifest_path, manifest)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

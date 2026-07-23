"""Cloud Run Job entrypoint for Gemini qualitative prompt sentiment.

Persists ``prompt_performance_sentiment.json`` (overall + by_category + by_prompt)
so the API/UI can read labels without recomputing on page load.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from api import geo_services as geo
from api.sentiment_jobs import (
    SENTIMENT_JOB_REQUESTS_DIR,
    _read_json,
    _utc_now,
    _write_json,
    run_prompt_sentiment_analysis,
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
    audit_id = (os.getenv("PROMPT_SENTIMENT_AUDIT_ID") or "").strip()
    request_id = (os.getenv("PROMPT_SENTIMENT_REQUEST_ID") or "").strip()
    if not audit_id or not request_id:
        log.error("PROMPT_SENTIMENT_AUDIT_ID and PROMPT_SENTIMENT_REQUEST_ID are required")
        return 2

    audit_dir = geo.resolve_audit_dir(audit_id)
    manifest_path = audit_dir / SENTIMENT_JOB_REQUESTS_DIR / f"{request_id}.json"
    manifest = _read_json(manifest_path) or {}

    log.info("Starting prompt sentiment audit=%s request=%s", audit_id, request_id)
    try:
        outcome = run_prompt_sentiment_analysis(audit_dir, request_id=request_id)
        if manifest_path.is_file():
            manifest.update(
                {
                    "status": "done" if outcome.get("sentiment") == "done" else "error",
                    "updated_at": _utc_now(),
                    "outcome": outcome,
                }
            )
            _write_json(manifest_path, manifest)
        if outcome.get("sentiment") == "done":
            log.info("Prompt sentiment complete: %s", outcome)
            return 0
        # Persist path already updated pending/manifest above; keep exit non-zero for Cloud Run.
        log.error(
            "Prompt sentiment failed for audit=%s request=%s outcome=%s",
            audit_id,
            request_id,
            outcome,
        )
        return 1
    except Exception:
        log.exception("Prompt sentiment job failed for audit=%s request=%s", audit_id, request_id)
        if manifest_path.is_file():
            manifest = _read_json(manifest_path) or manifest
            manifest.update({"status": "error", "updated_at": _utc_now()})
            _write_json(manifest_path, manifest)
        pending = audit_dir / "prompt_sentiment_pending.json"
        if pending.is_file():
            cur = _read_json(pending) or {}
            if str(cur.get("request_id") or "") == request_id:
                cur.update(
                    {
                        "status": "error",
                        "updated_at": _utc_now(),
                        "error": "Prompt sentiment job crashed; see Cloud Run logs",
                    }
                )
                _write_json(pending, cur)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

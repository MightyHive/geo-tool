"""Cloud Run Job entrypoint for one topic content outline."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from api import geo_services as geo
from api.topic_content_jobs import (
    _read_json,
    _update_manifest,
    context_path,
    run_topic_content_generation,
)

log = logging.getLogger(__name__)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            value["exception"] = self.formatException(record.exc_info)
        return json.dumps(value, ensure_ascii=False)


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def main() -> int:
    _configure_logging()
    audit_id = (os.getenv("TOPIC_CONTENT_AUDIT_ID") or "").strip()
    topic = (os.getenv("TOPIC_CONTENT_TOPIC") or "").strip()
    request_id = (os.getenv("TOPIC_CONTENT_REQUEST_ID") or "").strip()
    if not audit_id or not topic or not request_id:
        log.error(
            "TOPIC_CONTENT_AUDIT_ID, TOPIC_CONTENT_TOPIC, and "
            "TOPIC_CONTENT_REQUEST_ID are required"
        )
        return 2

    audit_dir = geo.resolve_audit_dir(audit_id)
    supplied_context = (os.getenv("TOPIC_CONTENT_CONTEXT_PATH") or "").strip()
    serialized = Path(supplied_context) if supplied_context else context_path(audit_dir, request_id)
    context = _read_json(serialized) if serialized.is_file() else None
    structure = context.get("structure") if isinstance(context, dict) else None
    refresh = _truthy(os.getenv("TOPIC_CONTENT_REFRESH") or "")

    log.info("Starting topic content audit=%s topic=%s request=%s", audit_id, topic, request_id)
    try:
        run_topic_content_generation(
            audit_dir,
            topic=topic,
            request_id=request_id,
            structure=structure,
            refresh=refresh,
        )
        log.info("Topic content complete audit=%s topic=%s", audit_id, topic)
        return 0
    except Exception as exc:
        log.exception("Topic content failed audit=%s topic=%s", audit_id, topic)
        _update_manifest(audit_dir, request_id, status="error", error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Enqueue durable Gemini prompt-sentiment jobs (Cloud Run Job or local thread).

Runs after probe finalize so page loads read ``prompt_performance_sentiment.json``
instead of recomputing qualitative labels on every request.
"""

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

SENTIMENT_JOB_REQUESTS_DIR = "sentiment_job_requests"
SENTIMENT_PENDING_FILE = "prompt_sentiment_pending.json"
LIVE_PROBE_FILE = "prompt_performance_live_probe.json"


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


def sentiment_job_name() -> str:
    return (os.getenv("PROMPT_SENTIMENT_JOB_NAME") or "").strip()


def sentiment_job_region() -> str:
    return (
        os.getenv("PROMPT_SENTIMENT_JOB_REGION")
        or os.getenv("CLOUD_RUN_REGION")
        or "europe-west1"
    ).strip()


def sentiment_job_project() -> str:
    return (
        os.getenv("PROMPT_SENTIMENT_JOB_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or "emea-ds-sandbox"
    ).strip()


def sentiment_jobs_enabled() -> bool:
    force_local = (os.getenv("PROMPT_SENTIMENT_FORCE_LOCAL") or "").strip().lower()
    if force_local in {"1", "true", "yes"}:
        return False
    return bool(sentiment_job_name())


def pending_path(audit_dir: Path) -> Path:
    return audit_dir / SENTIMENT_PENDING_FILE


def _active_request(pending: Path) -> dict[str, Any] | None:
    payload = _read_json(pending)
    if not payload:
        return None
    if str(payload.get("status") or "") in {"queued", "starting", "running"}:
        return payload
    return None


def get_sentiment_job_status(audit_dir: Path) -> dict[str, Any]:
    audit_dir = audit_dir.resolve()
    audit_id = geo.audit_dir_api_rel(audit_dir)
    pending = pending_path(audit_dir)
    payload = _read_json(pending) or {}
    status = str(payload.get("status") or "")
    return {
        "audit_id": audit_id,
        "status": status or "idle",
        "request_id": str(payload.get("request_id") or ""),
        "execution": str(payload.get("execution") or ""),
        "error": str(payload.get("error") or "") or None,
        "updated_at": payload.get("updated_at") or payload.get("created_at"),
    }


def run_prompt_sentiment_analysis(audit_dir: Path, *, request_id: str | None = None) -> dict[str, str]:
    """Generate Gemini qualitative sentiment (overall + by_category + by_prompt) and persist."""
    from insights_llm import load_or_generate_prompt_sentiment

    audit_dir = audit_dir.resolve()
    audit_id = geo.audit_dir_api_rel(audit_dir)
    pending = pending_path(audit_dir)
    if request_id:
        cur = _read_json(pending) or {}
        if str(cur.get("request_id") or "") == request_id:
            cur.update({"status": "running", "updated_at": _utc_now()})
            _write_json(pending, cur)

    probe_path = audit_dir / LIVE_PROBE_FILE
    saved = _read_json(probe_path) or {}
    live = saved.get("live_probe") if isinstance(saved.get("live_probe"), dict) else None
    if not isinstance(live, dict) or not (live.get("per_prompt") or []):
        locales = saved.get("locale_probes") if isinstance(saved.get("locale_probes"), dict) else {}
        default_key = str(saved.get("default_locale_key") or "")
        entry = locales.get(default_key) if default_key else None
        if not isinstance(entry, dict):
            entry = next((e for e in locales.values() if isinstance(e, dict)), None)
        if isinstance(entry, dict) and isinstance(entry.get("live_probe"), dict):
            live = entry["live_probe"]
    if not isinstance(live, dict) or not (live.get("per_prompt") or []):
        outcome = {"sentiment": "skipped", "reason": "no_probe"}
        if request_id and pending.is_file():
            cur = _read_json(pending) or {}
            if str(cur.get("request_id") or "") == request_id:
                cur.update({"status": "done", "updated_at": _utc_now(), "outcome": outcome})
                _write_json(pending, cur)
        return outcome

    onboarding_path = audit_dir / "onboarding_context.json"
    onboarding = _read_json(onboarding_path) or {}
    brand = str(onboarding.get("brand_name_used") or onboarding.get("brand_name") or "").strip()
    site = str(onboarding.get("website_url") or onboarding.get("site_url") or "").strip()
    if not brand:
        try:
            from api.prompt_performance import _brand_and_site, _load_audit_onboarding

            brand, site = _brand_and_site(_load_audit_onboarding(audit_dir))
        except Exception:
            pass

    pss_rows = onboarding.get("products_and_services_rows")
    if not isinstance(pss_rows, list):
        pss_rows = None

    sent, err = load_or_generate_prompt_sentiment(
        audit_dir,
        live,
        brand_name=brand or "the brand",
        site_url=site,
        pss_rows=pss_rows,
        force=True,
    )
    if sent:
        outcome = {"sentiment": "done"}
        status = "done"
    else:
        outcome = {"sentiment": f"error: {err or 'unknown'}"}
        status = "error"

    if request_id and pending.is_file():
        cur = _read_json(pending) or {}
        if str(cur.get("request_id") or "") == request_id:
            cur.update(
                {
                    "status": status,
                    "updated_at": _utc_now(),
                    "outcome": outcome,
                    "error": None if status == "done" else (err or "Sentiment analysis failed"),
                }
            )
            _write_json(pending, cur)
    log.info("Prompt sentiment %s for %s: %s", status, audit_id, outcome)
    return outcome


def enqueue_sentiment_job(audit_dir: Path) -> dict[str, Any]:
    """Queue Gemini sentiment analysis after probes (Cloud Run Job or local thread)."""
    audit_dir = audit_dir.resolve()
    audit_id = geo.audit_dir_api_rel(audit_dir)
    pending = pending_path(audit_dir)
    active = _active_request(pending)
    if active:
        return {
            "status": str(active.get("status") or "queued"),
            "audit_id": audit_id,
            "request_id": str(active.get("request_id") or ""),
            "execution": str(active.get("execution") or ""),
            "already_running": True,
        }

    request_id = uuid.uuid4().hex
    requests_dir = audit_dir / SENTIMENT_JOB_REQUESTS_DIR
    requests_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = requests_dir / f"{request_id}.json"
    manifest = {
        "request_id": request_id,
        "audit_id": audit_id,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "status": "queued",
    }
    _write_json(manifest_path, manifest)
    _write_json(
        pending,
        {
            "request_id": request_id,
            "status": "queued",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "execution": "",
        },
    )

    if sentiment_jobs_enabled():
        try:
            execution = _execute_sentiment_job(audit_id=audit_id, request_id=request_id)
            manifest.update({"status": "started", "execution": execution, "updated_at": _utc_now()})
            _write_json(manifest_path, manifest)
            current = _read_json(pending) or {}
            if str(current.get("request_id") or "") == request_id:
                current.update({"status": "queued", "execution": execution, "updated_at": _utc_now()})
                _write_json(pending, current)
            return {
                "status": "queued",
                "audit_id": audit_id,
                "request_id": request_id,
                "execution": execution,
                "already_running": False,
            }
        except Exception as exc:
            log.exception("Failed to launch sentiment Cloud Run Job for %s", audit_id)
            manifest.update({"status": "launch_failed", "error": str(exc), "updated_at": _utc_now()})
            _write_json(manifest_path, manifest)
            _write_json(
                pending,
                {
                    "request_id": request_id,
                    "status": "launch_failed",
                    "error": str(exc),
                    "updated_at": _utc_now(),
                },
            )
            # Fall through to local thread so sentiment still completes.

    def _worker() -> None:
        try:
            run_prompt_sentiment_analysis(audit_dir, request_id=request_id)
            if manifest_path.is_file():
                m = _read_json(manifest_path) or manifest
                m.update({"status": "done", "updated_at": _utc_now()})
                _write_json(manifest_path, m)
        except Exception:
            log.exception("Local prompt sentiment failed for %s", audit_id)
            if manifest_path.is_file():
                m = _read_json(manifest_path) or manifest
                m.update({"status": "error", "updated_at": _utc_now()})
                _write_json(manifest_path, m)

    threading.Thread(target=_worker, name=f"prompt-sentiment-{request_id[:8]}", daemon=True).start()
    return {
        "status": "queued",
        "audit_id": audit_id,
        "request_id": request_id,
        "execution": "local-thread",
        "already_running": False,
    }


def _execute_sentiment_job(*, audit_id: str, request_id: str) -> str:
    from google.cloud import run_v2

    project = sentiment_job_project()
    region = sentiment_job_region()
    name = sentiment_job_name()
    job_path = f"projects/{project}/locations/{region}/jobs/{name}"
    overrides = {
        "container_overrides": [
            {
                "env": [
                    {"name": "PROMPT_SENTIMENT_AUDIT_ID", "value": audit_id},
                    {"name": "PROMPT_SENTIMENT_REQUEST_ID", "value": request_id},
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

"""Cloud Run Job entrypoint for durable AI prompt probe executions."""

from __future__ import annotations

import logging
import json
import os
import sys
from pathlib import Path
from typing import Any

from api import geo_services as geo
from api.prompt_jobs import (
    AIO_PENDING_FILE,
    PROMPT_JOB_REQUESTS_DIR,
    PROMPT_PENDING_FILE,
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


def _run_aio(audit_dir: Path, *, max_prompts: int) -> None:
    from api.prompt_performance import (
        AIO_PROBE_FILE,
        _build_context_response,
        _probe_prompts_for_api,
        _save_aio_probe,
    )
    from google_aio import run_aio_probes
    from video_enrichment import enrich_aio_probe

    context = _build_context_response(audit_dir)
    prompts = _probe_prompts_for_api(context)
    if not prompts:
        raise ValueError("No prompts on file for this audit")
    aio = run_aio_probes(
        prompts,
        brand_site_url=str(context.get("brand_site_url") or "").strip(),
        max_prompts=max_prompts,
        market_country=str((context.get("primary_market") or {}).get("country") or ""),
        market_country_code=str((context.get("primary_market") or {}).get("country_id") or ""),
    )
    try:
        enrich_aio_probe(aio)
    except Exception:
        log.exception("AIO citation enrichment failed")
    _save_aio_probe(audit_dir, aio)
    if not (audit_dir / AIO_PROBE_FILE).is_file():
        raise RuntimeError("AIO probe result was not persisted")


def _run_post_audit(
    audit_dir: Path,
    *,
    completion: dict[str, Any],
    report_mode: bool,
) -> None:
    from api.audit_progress import AuditProgressState, advance_to_step
    from api.audit_runner import _write_run_status, finalize_audit_run
    from api.prompt_performance import run_post_audit_prompt_insights

    state = advance_to_step(
        AuditProgressState(),
        "prompt_probes",
        "Running AI prompt probes for share of voice…",
    )

    def on_step(step_id: str, detail: str) -> None:
        nonlocal state
        state = advance_to_step(state, step_id, detail)
        _write_run_status(
            audit_dir,
            {
                "status": "running",
                "audit_dir": geo.audit_dir_api_rel(audit_dir),
                **state.to_payload(),
            },
        )

    outcome = run_post_audit_prompt_insights(
        audit_dir,
        report_mode=report_mode,
        on_step=on_step,
    )
    if str(outcome.get("probes") or "").startswith("error:"):
        raise RuntimeError(str(outcome["probes"]))
    finalize_audit_run(
        audit_dir=audit_dir,
        primary=str(completion.get("primary") or ""),
        competitors=[
            str(item).strip()
            for item in (completion.get("competitors") or [])
            if str(item).strip()
        ],
        owner_email=str(completion.get("owner_email") or "") or None,
        notification_email=str(completion.get("notification_email") or "") or None,
        brand_name=str(completion.get("brand_name") or ""),
        progress_state=state,
    )


def run() -> None:
    audit_id = (os.getenv("PROMPT_JOB_AUDIT_ID") or "").strip()
    request_id = (os.getenv("PROMPT_JOB_REQUEST_ID") or "").strip()
    if not audit_id or not request_id:
        raise ValueError("PROMPT_JOB_AUDIT_ID and PROMPT_JOB_REQUEST_ID are required")

    audit_dir = geo.resolve_audit_dir(audit_id)
    manifest_path = audit_dir / PROMPT_JOB_REQUESTS_DIR / f"{request_id}.json"
    manifest = _read_json(manifest_path)
    if not manifest:
        raise FileNotFoundError(f"Prompt job manifest not found: {manifest_path}")
    mode = str(manifest.get("mode") or "")
    pending = audit_dir / (AIO_PENDING_FILE if mode == "aio" else PROMPT_PENDING_FILE)
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
        if mode == "post_audit":
            _run_post_audit(
                audit_dir,
                completion=manifest.get("completion") or {},
                report_mode=bool(manifest.get("report_mode", True)),
            )
        elif mode == "live":
            from api.prompt_performance import run_post_audit_prompt_insights

            outcome = run_post_audit_prompt_insights(
                audit_dir,
                report_mode=bool(manifest.get("report_mode", True)),
            )
            if str(outcome.get("probes") or "").startswith("error:"):
                raise RuntimeError(str(outcome["probes"]))
        elif mode == "history":
            from api.probe_history import _do_rerun

            _do_rerun(
                audit_id,
                audit_dir,
                all_prompts=bool(manifest.get("all_prompts")),
                raise_errors=True,
            )
        elif mode == "aio":
            _run_aio(audit_dir, max_prompts=int(manifest.get("max_prompts") or 25))
        else:
            raise ValueError(f"Unsupported prompt job mode: {mode}")
        manifest.update({"status": "completed", "completed_at": _utc_now()})
        _write_json(manifest_path, manifest)
    except Exception as exc:
        manifest.update({"status": "failed", "error": str(exc), "completed_at": _utc_now()})
        _write_json(manifest_path, manifest)
        if mode == "post_audit":
            from api.audit_runner import _write_run_status

            _write_run_status(
                audit_dir,
                {
                    "status": "error",
                    "audit_dir": audit_id,
                    "detail": f"AI prompt job failed: {exc}",
                    "error": str(exc),
                },
            )
        raise
    finally:
        current = _read_json(pending) or {}
        if str(current.get("request_id") or "") == request_id:
            pending.unlink(missing_ok=True)


if __name__ == "__main__":
    _configure_logging()
    run()

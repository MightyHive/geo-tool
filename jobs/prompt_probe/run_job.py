"""Cloud Run Job entrypoint for durable AI prompt probe executions.

Supports locale fan-out: each execution probes one market+language locale.
When all locale executions in a batch succeed, results are merged and the
audit is finalized (post_audit mode).
"""

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
    touch_fanout_pending_running,
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


def _write_locale_run_status(
    audit_dir: Path,
    *,
    market_count: int,
    state: Any,
    probe_progress: dict[str, Any] | None = None,
) -> None:
    from api.audit_runner import _write_run_status

    payload: dict[str, Any] = {
        "status": "running",
        "audit_dir": geo.audit_dir_api_rel(audit_dir),
        "market_count": market_count,
        **state.to_payload(),
    }
    if probe_progress:
        payload["probe_progress"] = probe_progress
        mc = int(probe_progress.get("market_count") or 0)
        if mc > 0:
            payload["market_count"] = mc
    _write_run_status(audit_dir, payload)


def _run_locale_job(
    audit_dir: Path,
    *,
    manifest: dict[str, Any],
    request_id: str,
) -> None:
    """Probe a single locale, then try merge/finalize when the batch is complete."""
    from api.audit_progress import AuditProgressState, advance_to_step
    from api.prompt_performance import (
        maybe_complete_locale_fanout,
        run_live_probe_for_locale,
        summarize_probe_progress_event,
    )

    locale = manifest.get("locale") if isinstance(manifest.get("locale"), dict) else {}
    locale_key = str(manifest.get("locale_key") or locale.get("key") or "").strip()
    if not locale_key:
        raise ValueError("Locale probe manifest missing locale_key")
    if not locale:
        locale = {"key": locale_key}
    locale_index = max(1, int(manifest.get("locale_index") or 1))
    locale_total = max(1, int(manifest.get("locale_total") or 1))
    batch_id = str(manifest.get("batch_id") or "").strip()
    if not batch_id:
        raise ValueError("Locale probe manifest missing batch_id")
    report_mode = bool(manifest.get("report_mode", True))
    mode = str(manifest.get("mode") or "live")

    state = advance_to_step(
        AuditProgressState(),
        "prompt_probes",
        f"Running AI prompt probes for {locale.get('label') or locale_key} "
        f"(market {locale_index}/{locale_total})…",
    )
    if mode == "post_audit":
        _write_locale_run_status(audit_dir, market_count=locale_total, state=state)

    def on_progress(event: dict[str, Any]) -> None:
        nonlocal state
        from api.prompt_performance import read_latest_probe_progress

        # Prefer fan-out aggregate so parallel locale jobs don't inflate ETA/counts.
        summary = read_latest_probe_progress(audit_dir) or summarize_probe_progress_event(
            event
        )
        label = str(event.get("locale_label") or locale_key)
        status = str(event.get("status") or "")
        locale_completed = int(event.get("completed_calls") or 0)
        locale_planned = int(event.get("planned_calls") or 0)
        if status == "locale_started":
            detail = f"Starting prompts for {label} (market {locale_index}/{locale_total})…"
        else:
            detail = (
                f"{label}: {locale_completed}/{locale_planned} platform calls "
                f"(market {locale_index}/{locale_total})…"
            )
        state = advance_to_step(state, "prompt_probes", detail)
        if mode == "post_audit":
            _write_locale_run_status(
                audit_dir,
                market_count=locale_total,
                state=state,
                probe_progress=summary,
            )

    try:
        run_live_probe_for_locale(
            audit_dir,
            locale=locale,
            locale_index=locale_index,
            locale_total=locale_total,
            report_mode=report_mode,
            on_progress=on_progress,
            request_id=request_id,
        )
    except Exception as exc:
        # Ensure failed locales are recorded so other jobs can surface partial failure.
        try:
            maybe_complete_locale_fanout(
                audit_dir,
                batch_id=batch_id,
                completing_locale_key=locale_key,
                request_id=request_id,
                locale_error=str(exc),
            )
        except Exception:
            log.exception("Failed to record locale failure for %s", locale_key)
        raise

    completion = maybe_complete_locale_fanout(
        audit_dir,
        batch_id=batch_id,
        completing_locale_key=locale_key,
        request_id=request_id,
    )
    log.info(
        "Locale %s finished for batch %s: %s",
        locale_key,
        batch_id,
        completion.get("action"),
    )
    if completion.get("action") == "finalize_failed":
        raise RuntimeError(str(completion.get("error") or "Fan-out finalize failed"))


def _run_post_audit(
    audit_dir: Path,
    *,
    completion: dict[str, Any],
    report_mode: bool,
) -> None:
    """Legacy sequential path for manifests without locale_key (pre-fan-out)."""
    from api.audit_progress import AuditProgressState, advance_to_step
    from api.audit_runner import _write_run_status, finalize_audit_run
    from api.prompt_performance import _load_audit_onboarding, run_post_audit_prompt_insights
    from prompt_locales import locales_from_onboarding

    market_count = 1
    try:
        locales = locales_from_onboarding(_load_audit_onboarding(audit_dir) or {})
        if locales:
            market_count = len(locales)
    except Exception:
        pass

    state = advance_to_step(
        AuditProgressState(),
        "prompt_probes",
        "Running AI prompt probes for share of voice…",
    )
    _write_run_status(
        audit_dir,
        {
            "status": "running",
            "audit_dir": geo.audit_dir_api_rel(audit_dir),
            "market_count": market_count,
            **state.to_payload(),
        },
    )

    def on_step(
        step_id: str,
        detail: str,
        *,
        probe_progress: dict[str, Any] | None = None,
    ) -> None:
        nonlocal state
        state = advance_to_step(state, step_id, detail)
        payload: dict[str, Any] = {
            "status": "running",
            "audit_dir": geo.audit_dir_api_rel(audit_dir),
            "market_count": market_count,
            **state.to_payload(),
        }
        if probe_progress:
            payload["probe_progress"] = probe_progress
            mc = int(probe_progress.get("market_count") or 0)
            if mc > 0:
                payload["market_count"] = mc
        _write_run_status(audit_dir, payload)

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

    is_locale_job = bool(str(manifest.get("locale_key") or "").strip()) and mode in {
        "post_audit",
        "live",
    }

    # Fan-out batches share one pending file across concurrent locale jobs.
    # Use a locked merge (never a fixed .tmp + blind RMW) so sibling locales
    # don't trigger ESTALE / clobber each other's progress metadata.
    if not is_locale_job:
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
    else:
        touch_fanout_pending_running(
            audit_dir,
            mode=mode,
            batch_id=str(manifest.get("batch_id") or ""),
        )

    try:
        if is_locale_job:
            _run_locale_job(audit_dir, manifest=manifest, request_id=request_id)
        elif mode == "post_audit":
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
        if mode == "post_audit" and not is_locale_job:
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
        if not is_locale_job:
            current = _read_json(pending) or {}
            if str(current.get("request_id") or "") == request_id:
                pending.unlink(missing_ok=True)


if __name__ == "__main__":
    _configure_logging()
    run()

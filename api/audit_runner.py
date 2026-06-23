"""Background audit pipeline (wizard run step) with on-disk progress for polling."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api import geo_services as geo

log = logging.getLogger(__name__)

AUDIT_RUN_STATUS_FILE = "audit_run_status.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_run_status(audit_dir: Path, payload: dict[str, Any]) -> None:
    payload = {**payload, "updated_at": _utc_now()}
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / AUDIT_RUN_STATUS_FILE).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_run_status(audit_dir: Path) -> dict[str, Any] | None:
    path = audit_dir / AUDIT_RUN_STATUS_FILE
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(raw, dict):
                return raw
        except (OSError, json.JSONDecodeError):
            pass
    if (audit_dir / "report.html").is_file():
        try:
            summary = geo.load_audit_summary(audit_dir)
            return {
                "status": "done",
                "audit_dir": geo.audit_dir_api_rel(audit_dir),
                "overall_score": summary.get("overall_score"),
                "detail": "Audit complete",
                "percent": 100,
            }
        except Exception:
            return {"status": "done", "audit_dir": geo.audit_dir_api_rel(audit_dir)}
    # The audit directory was seeded by the wizard but the status file hasn't been
    # written yet (GCS FUSE write-back cache delay, or race between thread start and
    # the initial _write_run_status call). Return a "starting" stub so the client
    # keeps polling rather than receiving a 404 and aborting.
    if audit_dir.is_dir():
        return {
            "status": "running",
            "audit_dir": geo.audit_dir_api_rel(audit_dir),
            "percent": 0,
            "detail": "Audit starting…",
            "current_step": "crawl",
            "steps": [],
        }
    return None


def _run_audit_job(
    *,
    audit_dir: Path,
    primary: str,
    competitors: list[str],
    body_dict: dict[str, Any],
    ga4_prop: str | None,
    ga4_ch: str | None,
    ga4_cred_path: str | None,
    owner_email: str | None,
    stream_progress: bool,
) -> None:
    from api.audit_progress import (
        AuditProgressState,
        advance_to_step,
        apply_log_line,
        complete_all_steps,
    )
    from api.prompt_performance import run_post_audit_prompt_insights

    rel = geo.audit_dir_api_rel(audit_dir)

    def _progress_payload(state: Any | None) -> dict[str, Any]:
        if state is not None:
            return state.to_payload()
        return {
            "percent": 2,
            "detail": "Starting audit…",
            "current_step": "crawl",
            "steps": [
                {"id": sid, "label": lbl, "status": "pending" if sid != "crawl" else "active"}
                for sid, lbl in PIPELINE_STEPS
            ],
        }

    try:
        progress_state = AuditProgressState() if stream_progress else None
        _write_run_status(
            audit_dir,
            {
                "status": "running",
                "audit_dir": rel,
                "started_at": _utc_now(),
                **_progress_payload(progress_state),
            },
        )

        for line in geo.iter_pipeline_logs(
            primary,
            competitors,
            body_dict.get("out_base", "audit_output"),
            int(body_dict.get("max_urls", 40)),
            float(body_dict.get("delay", 0.2)),
            brand_name=str(body_dict.get("brand_name") or ""),
            industry=str(body_dict.get("industry") or ""),
            market_country=str(body_dict.get("wizard_market_country") or ""),
            market_country_code=str(body_dict.get("wizard_market_country_code") or ""),
            ga4_property_id=ga4_prop,
            ga4_ai_channels=ga4_ch,
            ga4_oauth_credentials_path=ga4_cred_path,
        ):
            if stream_progress and progress_state is not None:
                progress_state = apply_log_line(progress_state, line)
                _write_run_status(
                    audit_dir,
                    {
                        "status": "running",
                        "audit_dir": rel,
                        **_progress_payload(progress_state),
                    },
                )

        if progress_state is not None:
            progress_state = advance_to_step(
                progress_state,
                "prompt_probes",
                "Running AI prompt probes for share of voice…",
            )
            _write_run_status(
                audit_dir,
                {
                    "status": "running",
                    "audit_dir": rel,
                    **_progress_payload(progress_state),
                },
            )

        def _on_post_audit_step(step_id: str, detail: str) -> None:
            nonlocal progress_state
            if progress_state is not None:
                progress_state = advance_to_step(progress_state, step_id, detail)
                _write_run_status(
                    audit_dir,
                    {
                        "status": "running",
                        "audit_dir": rel,
                        **_progress_payload(progress_state),
                    },
                )

        run_post_audit_prompt_insights(
            audit_dir, report_mode=True, on_step=_on_post_audit_step if progress_state else None
        )

        if progress_state is not None:
            progress_state = complete_all_steps(progress_state, detail="Audit complete")

        summary = geo.load_audit_summary(audit_dir)
        overall = float(summary.get("overall_score") or 0)
        if overall <= 0:
            resolved = geo.resolve_overall_score_for_audit(audit_dir)
            if resolved is not None:
                overall = resolved
        geo.archive_add_run(
            primary_url=primary,
            audit_dir=audit_dir,
            overall=overall,
            competitors=competitors,
            owner_email=owner_email,
            brand_name=str(body_dict.get("brand_name") or "").strip() or None,
        )
        _write_run_status(
            audit_dir,
            {
                "status": "done",
                "audit_dir": rel,
                "overall_score": summary.get("overall_score"),
                "detail": "Audit complete",
                "percent": 100,
                **(_progress_payload(progress_state) if progress_state else {}),
            },
        )
    except Exception as exc:
        log.exception("Background audit failed for %s: %s", rel, exc)
        _write_run_status(
            audit_dir,
            {
                "status": "error",
                "audit_dir": rel,
                "error": str(exc),
                "detail": str(exc)[:500],
                "percent": 0,
            },
        )
    finally:
        if ga4_cred_path:
            try:
                Path(ga4_cred_path).unlink(missing_ok=True)
            except OSError:
                pass


def start_background_audit(
    request: Any,
    body: Any,
    *,
    owner_email: str | None,
) -> dict[str, Any]:
    """
    Seed audit folder, spawn pipeline in a daemon thread, return immediately.
    Client polls :func:`read_run_status` via GET ``/api/audits/{id}/run-status``.
    """
    from geo_app_env import current_app_env
    from geo_setup_llm import normalize_competitor_url

    from api.ga4 import resolve_ga4_for_audit_run

    primary = normalize_competitor_url(body.brand_website.strip())
    if not primary:
        raise ValueError("Invalid brand website URL")

    competitors = [c.strip() for c in body.competitors if c.strip()][:12]
    ga4_prop, ga4_ch, ga4_cred_temp = resolve_ga4_for_audit_run(
        request,
        ga4_property_id=body.ga4_property_id,
        ga4_ai_channels=body.ga4_ai_channels,
    )
    ga4_cred_path = str(ga4_cred_temp) if ga4_cred_temp is not None else None

    adir = geo.audit_dir_for_run(body.out_base, primary)
    geo.seed_audit_dir_from_wizard(
        adir,
        primary_url=primary,
        brand_name=body.brand_name.strip(),
        industry=body.industry.strip(),
        market_country=body.wizard_market_country.strip(),
        market_country_code=body.wizard_market_country_code.strip(),
        competitor_urls=competitors,
        products_rows=[p.model_dump() for p in body.wizard_products],
        competitors_detail=[c.model_dump() for c in body.wizard_competitors],
        ga4_property_id=ga4_prop or "",
        ga4_ai_channel_names=ga4_ch or "",
    )

    body_dict = body.model_dump()
    # Always stream progress to audit_run_status.json for wizard step 7 polling.
    stream_progress = True

    threading.Thread(
        target=_run_audit_job,
        kwargs={
            "audit_dir": adir,
            "primary": primary,
            "competitors": competitors,
            "body_dict": body_dict,
            "ga4_prop": ga4_prop,
            "ga4_ch": ga4_ch,
            "ga4_cred_path": ga4_cred_path,
            "owner_email": owner_email,
            "stream_progress": stream_progress,
        },
        daemon=True,
    ).start()

    rel = geo.audit_dir_api_rel(adir)
    _write_run_status(
        adir,
        {
            "status": "running",
            "audit_dir": rel,
            "started_at": _utc_now(),
            "percent": 0,
            "detail": "Audit queued…",
            "current_step": "crawl",
        },
    )
    return {"ok": True, "audit_dir": rel, "status": "running"}

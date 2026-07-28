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
# Full audit crawl Job timeout is 2h; allow buffer before treating as orphaned.
STALE_AUDIT_RUN_SECONDS = 4 * 60 * 60
STALE_AUDIT_ORPHAN_SECONDS = 60 * 60


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_run_status(audit_dir: Path, payload: dict[str, Any]) -> None:
    payload = {**payload, "updated_at": _utc_now()}
    audit_dir.mkdir(parents=True, exist_ok=True)
    # Unique-temp + replace avoids GCS FUSE ESTALE when concurrent locale jobs
    # refresh audit_run_status.json during fan-out.
    from api.prompt_jobs import _write_json

    try:
        _write_json(audit_dir / AUDIT_RUN_STATUS_FILE, payload)
    except OSError:
        # Fall back to in-place write so a transient FUSE blip does not fail the job.
        (audit_dir / AUDIT_RUN_STATUS_FILE).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def reconcile_audit_run_status(audit_dir: Path) -> dict[str, Any] | None:
    """Clear orphaned ``audit_run_status.json`` running states that block competitor crawls."""
    path = audit_dir / AUDIT_RUN_STATUS_FILE
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    status = str(raw.get("status") or "")
    if status not in {"running", "starting", "queued"}:
        return raw

    pending = audit_dir / "audit_crawl_pending.json"
    stale = geo.running_status_is_stale(
        raw,
        max_age_seconds=STALE_AUDIT_RUN_SECONDS,
        orphan_after_seconds=STALE_AUDIT_ORPHAN_SECONDS,
        pending_path=pending,
    )
    # If a report already exists and there is no active crawl Job pending, the
    # "running" flag is almost certainly leftover from a recycled service instance.
    # Do not clear while prompt / CQ / sentiment / competitor work is still active.
    if not stale and (audit_dir / "report.html").is_file() and not pending.is_file():
        age = geo.running_status_age_seconds(raw)
        if age is None or age >= 15 * 60:
            try:
                from api.audit_pipeline_status import audit_pipeline_is_running

                pipeline_busy = audit_pipeline_is_running(
                    audit_dir, include_run_status=False
                )
            except Exception:
                pipeline_busy = False
            if not pipeline_busy:
                stale = True

    if not stale:
        return raw

    pending.unlink(missing_ok=True)
    if (audit_dir / "report.html").is_file():
        log.warning(
            "Clearing stale running audit status for %s (report already present)",
            geo.audit_dir_api_rel(audit_dir),
        )
        try:
            summary = geo.load_audit_summary(audit_dir)
            overall = summary.get("overall_score")
        except Exception:
            overall = None
        done = {
            "status": "done",
            "audit_dir": geo.audit_dir_api_rel(audit_dir),
            "overall_score": overall,
            "detail": "Audit complete",
            "percent": 100,
            "stale_cleared": True,
        }
        _write_run_status(audit_dir, done)
        return {**done, "updated_at": _utc_now()}

    log.warning(
        "Marking stale audit run as error for %s",
        geo.audit_dir_api_rel(audit_dir),
    )
    errored = {
        **raw,
        "status": "error",
        "detail": "Previous audit run was interrupted. Start a new run or competitor crawl to continue.",
        "error": "Audit run timed out or the worker stopped unexpectedly.",
        "percent": int(raw.get("percent") or 0),
        "stale": True,
    }
    _write_run_status(audit_dir, errored)
    return {**errored, "updated_at": _utc_now()}


def _attach_probe_progress(audit_dir: Path, status: dict[str, Any]) -> dict[str, Any]:
    """Merge latest prompt-probe progress into a run-status payload when available."""
    if str(status.get("status") or "") not in {"running", "starting", "queued"}:
        return status
    try:
        from api.prompt_performance import read_latest_probe_progress

        latest = read_latest_probe_progress(audit_dir)
    except Exception:
        return status
    if not latest:
        return status
    existing = status.get("probe_progress")
    existing_completed = (
        int(existing.get("completed_calls") or 0) if isinstance(existing, dict) else -1
    )
    latest_completed = int(latest.get("completed_calls") or 0)
    # Prefer the freshest completed_calls count (jsonl may lag behind status writes).
    if not isinstance(existing, dict) or latest_completed >= existing_completed:
        status = {**status, "probe_progress": latest}
    if not status.get("market_count") and latest.get("market_count"):
        status = {**status, "market_count": latest["market_count"]}
    return status


def read_run_status(audit_dir: Path) -> dict[str, Any] | None:
    from api.audit_pipeline_status import attach_pipeline_status

    reconciled = reconcile_audit_run_status(audit_dir)
    if reconciled is not None:
        return attach_pipeline_status(
            _attach_probe_progress(audit_dir, reconciled), audit_dir
        )
    path = audit_dir / AUDIT_RUN_STATUS_FILE
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(raw, dict):
                return attach_pipeline_status(
                    _attach_probe_progress(audit_dir, raw), audit_dir
                )
        except (OSError, json.JSONDecodeError):
            pass
    if (audit_dir / "report.html").is_file():
        try:
            summary = geo.load_audit_summary(audit_dir)
            base = {
                "status": "done",
                "audit_dir": geo.audit_dir_api_rel(audit_dir),
                "overall_score": summary.get("overall_score"),
                "detail": "Audit complete",
                "percent": 100,
            }
        except Exception:
            base = {"status": "done", "audit_dir": geo.audit_dir_api_rel(audit_dir)}
        return attach_pipeline_status(base, audit_dir)
    # The audit directory was seeded by the wizard but the status file hasn't been
    # written yet (GCS FUSE write-back cache delay, or race between thread start and
    # the initial _write_run_status call). Return a "starting" stub so the client
    # keeps polling rather than receiving a 404 and aborting.
    if audit_dir.is_dir():
        return attach_pipeline_status(
            {
                "status": "running",
                "audit_dir": geo.audit_dir_api_rel(audit_dir),
                "percent": 0,
                "detail": "Audit starting…",
                "current_step": "crawl",
                "steps": [],
            },
            audit_dir,
        )
    return None


def _read_run_status_raw(audit_dir: Path) -> dict[str, Any]:
    path = audit_dir / AUDIT_RUN_STATUS_FILE
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_notification_email(
    audit_dir: Path,
    notification_email: str | None,
) -> str | None:
    """Prefer explicit arg, then run status, then onboarding_context.json."""
    for candidate in (
        notification_email,
        _read_run_status_raw(audit_dir).get("notification_email"),
    ):
        email = str(candidate or "").strip()
        if email and "@" in email:
            return email
    ob_path = audit_dir / "onboarding_context.json"
    if ob_path.is_file():
        try:
            ob = json.loads(ob_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(ob, dict):
                email = str(ob.get("notification_email") or "").strip()
                if email and "@" in email:
                    return email
        except (OSError, json.JSONDecodeError):
            pass
    return None


def _maybe_send_completion_email(
    *,
    audit_dir: Path,
    rel: str,
    brand_name: str,
    notification_email: str | None,
    status_payload: dict[str, Any],
) -> None:
    """Send report-ready email once; mark status so retries do not spam."""
    if status_payload.get("notification_email_sent"):
        return
    to_email = _resolve_notification_email(audit_dir, notification_email)
    if not to_email:
        return
    status_payload["notification_email"] = to_email
    try:
        from api.notify_email import send_audit_complete_email

        ok = send_audit_complete_email(
            to_email=to_email,
            brand_name=brand_name,
            audit_dir=rel,
        )
        if ok:
            status_payload["notification_email_sent"] = True
            status_payload["notification_email_sent_at"] = _utc_now()
        else:
            status_payload["notification_email_error"] = "send_failed"
    except Exception as email_exc:
        log.warning("Failed to send notification email: %s", email_exc)
        status_payload["notification_email_error"] = str(email_exc)[:300]
    _write_run_status(audit_dir, status_payload)


def _queue_follow_on_competitor_crawl(audit_dir: Path, competitors: list[str]) -> None:
    """Queue competitor site crawl when competitors are configured (no-op if none)."""
    if not competitors:
        return
    rel = geo.audit_dir_api_rel(audit_dir)
    try:
        from api.crawl_jobs import crawl_jobs_enabled, enqueue_crawl_job

        if crawl_jobs_enabled():
            enqueue_crawl_job(audit_dir, mode="competitors_only", payload={})
            log.info("Queued follow-on competitor crawl for %s", rel)
        else:
            threading.Thread(
                target=run_competitor_crawl_job,
                args=(audit_dir,),
                daemon=True,
            ).start()
            log.info("Started follow-on competitor crawl thread for %s", rel)
    except Exception:
        log.exception("Follow-on competitor crawl failed to queue for %s", rel)


def finalize_audit_run(
    *,
    audit_dir: Path,
    primary: str,
    competitors: list[str],
    owner_email: str | None,
    notification_email: str | None,
    brand_name: str,
    progress_state: Any | None,
) -> None:
    """Archive a completed audit and publish its terminal progress state."""
    from api.audit_progress import complete_all_steps

    rel = geo.audit_dir_api_rel(audit_dir)
    prior = _read_run_status_raw(audit_dir)
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
        brand_name=brand_name.strip() or None,
    )
    payload: dict[str, Any] = {
        "status": "done",
        "audit_dir": rel,
        "overall_score": summary.get("overall_score"),
        "detail": "Audit complete",
        "percent": 100,
    }
    # Preserve prior notification bookkeeping across status rewrites.
    for key in (
        "notification_email",
        "notification_email_sent",
        "notification_email_sent_at",
    ):
        if key in prior and prior[key] is not None:
            payload[key] = prior[key]
    if progress_state is not None:
        payload.update(progress_state.to_payload())
    _write_run_status(audit_dir, payload)
    _maybe_send_completion_email(
        audit_dir=audit_dir,
        rel=rel,
        brand_name=brand_name,
        notification_email=notification_email,
        status_payload=payload,
    )
    try:
        from api.score_history import save_score_snapshot

        save_score_snapshot(audit_dir, source="crawl")
    except Exception:
        log.exception("Failed to save score snapshot after finalize for %s", rel)


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
    notification_email: str | None,
    stream_progress: bool,
) -> None:
    from api.audit_progress import (
        PIPELINE_STEPS,
        AuditProgressState,
        advance_to_step,
        apply_log_line,
    )

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

        _crawl_urls = body_dict.get("crawl_urls") or None
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
            additional_markets=(body_dict.get("wizard_additional_markets") or []),
            ga4_property_id=ga4_prop,
            ga4_ai_channels=ga4_ch,
            ga4_oauth_credentials_path=ga4_cred_path,
            crawl_urls=_crawl_urls,
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

        # Content-quality Gemini runs async after primary crawl artifacts exist.
        # Does not block probes/finalize; competitor crawls are not required.
        try:
            from api.content_quality_jobs import enqueue_content_quality_job

            cq = enqueue_content_quality_job(audit_dir)
            log.info(
                "Content-quality Gemini enqueue for %s: status=%s execution=%s",
                rel,
                cq.get("status"),
                cq.get("execution"),
            )
        except Exception:
            log.exception("Content-quality Gemini enqueue failed for %s (heuristics-only)", rel)

        skip_prompt_probes = bool(body_dict.get("skip_prompt_probes"))
        if progress_state is not None and not skip_prompt_probes:
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

        # Always follow on with competitor crawl when competitors are configured.
        # Client skip flags are ignored so weekly refresh and wizard runs stay consistent.
        follow_on = bool(competitors)

        if not skip_prompt_probes:
            from api.prompt_jobs import enqueue_prompt_job

            queued = enqueue_prompt_job(
                audit_dir,
                mode="post_audit",
                report_mode=True,
                completion={
                    "primary": primary,
                    "competitors": competitors,
                    "owner_email": owner_email,
                    "notification_email": notification_email,
                    "brand_name": str(body_dict.get("brand_name") or ""),
                },
            )
            if progress_state is not None:
                progress_state = advance_to_step(
                    progress_state,
                    "prompt_probes",
                    "AI prompt probe job queued…",
                )
                market_count = int(queued.get("locale_count") or 0) or None
                status_payload: dict[str, Any] = {
                    "status": "running",
                    "audit_dir": rel,
                    "prompt_job_execution": queued.get("execution"),
                    "prompt_job_batch_id": queued.get("batch_id") or queued.get("request_id"),
                    **_progress_payload(progress_state),
                }
                if market_count:
                    status_payload["market_count"] = market_count
                _write_run_status(
                    audit_dir,
                    status_payload,
                )
            if follow_on:
                _queue_follow_on_competitor_crawl(audit_dir, competitors)
            return

        finalize_audit_run(
            audit_dir=audit_dir,
            primary=primary,
            competitors=competitors,
            owner_email=owner_email,
            brand_name=str(body_dict.get("brand_name") or "").strip(),
            notification_email=notification_email,
            progress_state=progress_state,
        )
        if follow_on:
            _queue_follow_on_competitor_crawl(audit_dir, competitors)
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
    notification_email: str | None = None,
) -> dict[str, Any]:
    """
    Seed audit folder, enqueue crawl Job (or local daemon thread), return immediately.
    Client polls :func:`read_run_status` via GET ``/api/audits/{id}/run-status``.
    """
    from geo_setup_llm import normalize_competitor_url

    from api.ga4 import resolve_ga4_for_audit_run

    primary = normalize_competitor_url(body.brand_website.strip())
    if not primary:
        raise ValueError("Invalid brand website URL")

    competitors = [c.strip() for c in body.competitors if c.strip()][:10]
    ga4_prop, ga4_ch, ga4_cred_temp = resolve_ga4_for_audit_run(
        request,
        ga4_property_id=body.ga4_property_id,
        ga4_ai_channels=body.ga4_ai_channels,
    )
    ga4_cred_path = str(ga4_cred_temp) if ga4_cred_temp is not None else None

    adir = geo.audit_dir_for_run(body.out_base, primary)
    body_dict = body.model_dump()
    # Competitor crawl always follows the primary crawl when competitors are listed.
    body_dict["follow_on_competitor_crawl"] = bool(competitors)
    notify = (notification_email or getattr(body, "notification_email", None) or "").strip() or None
    geo.seed_audit_dir_from_wizard(
        adir,
        primary_url=primary,
        brand_name=body.brand_name.strip(),
        industry=body.industry.strip(),
        market_country=body.wizard_market_country.strip(),
        market_country_code=body.wizard_market_country_code.strip(),
        additional_markets=[m.model_dump() for m in body.wizard_additional_markets],
        competitor_urls=competitors,
        products_rows=[p.model_dump() for p in body.wizard_products],
        competitors_detail=[c.model_dump() for c in body.wizard_competitors],
        ga4_property_id=ga4_prop or "",
        ga4_ai_channel_names=ga4_ch or "",
        ga4_conversion_event_name=body.ga4_conversion_event_name,
        crawl_urls=body.crawl_urls or None,
        preserve_prompt_data=body.skip_prompt_probes,
        prompt_locales=list(getattr(body, "wizard_prompt_locales", None) or []),
        notification_email=notify,
    )

    rel = geo.audit_dir_api_rel(adir)
    status_seed: dict[str, Any] = {
        "status": "running",
        "audit_dir": rel,
        "started_at": _utc_now(),
        "percent": 0,
        "detail": "Audit queued…",
        "current_step": "crawl",
    }
    if notify:
        status_seed["notification_email"] = notify
    _write_run_status(adir, status_seed)

    from api.crawl_jobs import crawl_jobs_enabled, enqueue_crawl_job, persist_ga4_adc

    if crawl_jobs_enabled():
        ga4_rel = persist_ga4_adc(adir, ga4_cred_path)
        if ga4_cred_temp is not None:
            try:
                ga4_cred_temp.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            queued = enqueue_crawl_job(
                adir,
                mode="full_audit",
                payload={
                    "primary": primary,
                    "competitors": competitors,
                    "body_dict": body_dict,
                    "ga4_prop": ga4_prop,
                    "ga4_ch": ga4_ch,
                    "ga4_adc_relpath": ga4_rel,
                    "owner_email": owner_email,
                    "notification_email": notify,
                },
            )
        except Exception as exc:
            log.exception("Failed to enqueue crawl job for %s: %s", rel, exc)
            _write_run_status(
                adir,
                {
                    "status": "error",
                    "audit_dir": rel,
                    "error": str(exc),
                    "detail": f"Failed to queue crawl job: {exc}"[:500],
                    "percent": 0,
                },
            )
            raise
        _write_run_status(
            adir,
            {
                "status": "running",
                "audit_dir": rel,
                "started_at": _utc_now(),
                "percent": 0,
                "detail": "Crawl job queued…",
                "current_step": "crawl",
                "crawl_job_execution": queued.get("execution"),
                "crawl_job_request_id": queued.get("request_id"),
                **({"notification_email": notify} if notify else {}),
            },
        )
        return {
            "ok": True,
            "audit_dir": rel,
            "status": "running",
            "execution": queued.get("execution"),
            "request_id": queued.get("request_id"),
            "job": "cloud_run",
        }

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
            "notification_email": notify,
            "stream_progress": True,
        },
        daemon=True,
    ).start()

    return {"ok": True, "audit_dir": rel, "status": "running", "job": "local_thread"}


def run_competitor_crawl_job(audit_dir: Path) -> dict[str, Any]:
    """Execute an in-place competitor crawl (Cloud Run Job or local thread worker)."""
    rel = geo.audit_dir_api_rel(audit_dir)
    urls = geo.competitor_urls_for_crawl(audit_dir)
    if not urls:
        raise ValueError("No competitor websites configured for this audit")
    if not (audit_dir / "audit_summary.json").is_file():
        raise FileNotFoundError("Primary audit summary not found")

    started_at = _utc_now()

    def on_progress(detail: str, done: int, total: int) -> None:
        percent = 5
        if total > 0:
            percent = min(95, 5 + int((done / total) * 85))
        geo.write_competitor_crawl_status(
            audit_dir,
            {
                "status": "running",
                "started_at": started_at,
                "percent": percent,
                "detail": detail,
                "current_step": "competitor_crawl",
                "competitor_count": len(urls),
                "seen": False,
            },
        )

    geo.write_competitor_crawl_status(
        audit_dir,
        {
            "status": "running",
            "started_at": started_at,
            "percent": 0,
            "detail": f"Crawling {len(urls)} competitor site(s)…",
            "current_step": "competitor_crawl",
            "competitor_count": len(urls),
            "seen": False,
        },
    )
    try:
        result = geo.crawl_competitors_in_place(audit_dir, on_progress=on_progress)
        geo.write_competitor_crawl_status(
            audit_dir,
            {
                "status": "done",
                "started_at": started_at,
                "finished_at": _utc_now(),
                "percent": 100,
                "detail": f"Crawled {result.get('competitor_count', 0)} competitor site(s)",
                "current_step": "done",
                "crawled": result.get("crawled") or [],
                "skipped": result.get("skipped") or [],
                "competitor_count": result.get("competitor_count") or 0,
                "archived_previous_id": result.get("archived_id"),
                "seen": False,
            },
        )
        try:
            from api.score_history import save_score_snapshot

            save_score_snapshot(audit_dir, source="competitor_crawl")
        except Exception:
            log.exception("Failed to save score snapshot after competitor crawl for %s", rel)
        # Content-quality Gemini per competitor (async; does not block crawl status).
        try:
            from api.content_quality_jobs import enqueue_competitor_content_quality_jobs

            cq_results = enqueue_competitor_content_quality_jobs(audit_dir)
            log.info(
                "Competitor content-quality enqueue for %s: %s job(s)",
                rel,
                len(cq_results),
            )
        except Exception:
            log.exception(
                "Competitor content-quality Gemini enqueue failed for %s (heuristics-only)",
                rel,
            )
        return result
    except Exception as exc:
        log.exception("Competitor crawl failed for %s: %s", rel, exc)
        geo.write_competitor_crawl_status(
            audit_dir,
            {
                "status": "error",
                "started_at": started_at,
                "finished_at": _utc_now(),
                "percent": 0,
                "error": str(exc),
                "detail": str(exc)[:500],
                "current_step": "error",
                "competitor_count": len(urls),
                "seen": False,
            },
        )
        raise


def start_competitor_crawl(audit_dir: Path) -> dict[str, Any]:
    """Queue an in-place competitor-site crawl; poll via competitor crawl status."""
    rel = geo.audit_dir_api_rel(audit_dir)
    crawl_status = geo.reconcile_competitor_crawl_status(audit_dir)
    if crawl_status and str(crawl_status.get("status") or "") == "running":
        raise ValueError("A competitor crawl is already running for this folder")
    existing = reconcile_audit_run_status(audit_dir) or read_run_status(audit_dir)
    if (
        existing
        and str(existing.get("status") or "") == "running"
        and str(existing.get("job_type") or "") != "competitor_crawl"
        # Ignore the ephemeral "Audit starting…" stub when no status file exists.
        and (audit_dir / AUDIT_RUN_STATUS_FILE).is_file()
    ):
        raise ValueError("An audit is already running for this folder")

    urls = geo.competitor_urls_for_crawl(audit_dir)
    if not urls:
        raise ValueError("No competitor websites configured for this audit")
    if not (audit_dir / "audit_summary.json").is_file():
        raise FileNotFoundError("Primary audit summary not found")

    from api.crawl_jobs import crawl_jobs_enabled, enqueue_crawl_job

    started_at = _utc_now()
    geo.write_competitor_crawl_status(
        audit_dir,
        {
            "status": "running",
            "started_at": started_at,
            "percent": 0,
            "detail": f"Queuing crawl for {len(urls)} competitor site(s)…",
            "current_step": "competitor_crawl",
            "competitor_count": len(urls),
            "seen": False,
        },
    )

    if crawl_jobs_enabled():
        try:
            queued = enqueue_crawl_job(audit_dir, mode="competitors_only", payload={})
        except Exception as exc:
            log.exception("Failed to enqueue competitor crawl job for %s: %s", rel, exc)
            geo.write_competitor_crawl_status(
                audit_dir,
                {
                    "status": "error",
                    "started_at": started_at,
                    "finished_at": _utc_now(),
                    "percent": 0,
                    "error": str(exc),
                    "detail": f"Failed to queue crawl job: {exc}"[:500],
                    "current_step": "error",
                    "competitor_count": len(urls),
                    "seen": False,
                },
            )
            raise
        geo.write_competitor_crawl_status(
            audit_dir,
            {
                "status": "running",
                "started_at": started_at,
                "percent": 0,
                "detail": f"Crawl job queued for {len(urls)} competitor site(s)…",
                "current_step": "competitor_crawl",
                "competitor_count": len(urls),
                "seen": False,
                "crawl_job_execution": queued.get("execution"),
                "crawl_job_request_id": queued.get("request_id"),
            },
        )
        return {
            "ok": True,
            "audit_dir": rel,
            "status": "running",
            "job_type": "competitor_crawl",
            "competitor_count": len(urls),
            "execution": queued.get("execution"),
            "request_id": queued.get("request_id"),
            "job": "cloud_run",
        }

    threading.Thread(target=run_competitor_crawl_job, args=(audit_dir,), daemon=True).start()
    return {
        "ok": True,
        "audit_dir": rel,
        "status": "running",
        "job_type": "competitor_crawl",
        "competitor_count": len(urls),
        "job": "local_thread",
    }

"""Enqueue durable Gemini content-quality jobs (Cloud Run Job or local thread).

Runs after primary crawl/audit artifacts exist so page loads read
``content_quality_gemini.json`` instead of blocking the crawl or report path.

Competitor sites: after competitor crawl finalize, enqueue one job per
``competitors/<host>/`` directory (same file name + merge rules as brand).
Default sample cap is **20 per site** (brand and each competitor). Override
competitor-only with ``CONTENT_QUALITY_COMPETITOR_SAMPLE_CAP`` (5–20).
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
from urllib.parse import urlparse

from api import geo_services as geo

log = logging.getLogger(__name__)

CONTENT_QUALITY_JOB_REQUESTS_DIR = "content_quality_job_requests"
CONTENT_QUALITY_PENDING_FILE = "content_quality_gemini_pending.json"
CONTENT_QUALITY_JOB_CONTEXT_FILE = "content_quality_job_context.json"


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


def content_quality_job_name() -> str:
    return (os.getenv("CONTENT_QUALITY_JOB_NAME") or "").strip()


def content_quality_job_region() -> str:
    return (
        os.getenv("CONTENT_QUALITY_JOB_REGION")
        or os.getenv("CLOUD_RUN_REGION")
        or "europe-west1"
    ).strip()


def content_quality_job_project() -> str:
    project = (
        os.getenv("CONTENT_QUALITY_JOB_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or ""
    ).strip()
    if not project:
        raise RuntimeError("CONTENT_QUALITY_JOB_PROJECT is not configured")
    return project


def content_quality_jobs_enabled() -> bool:
    force_local = (os.getenv("CONTENT_QUALITY_FORCE_LOCAL") or "").strip().lower()
    if force_local in {"1", "true", "yes"}:
        return False
    return bool(content_quality_job_name())


def pending_path(audit_dir: Path) -> Path:
    return audit_dir / CONTENT_QUALITY_PENDING_FILE


def job_context_path(audit_dir: Path) -> Path:
    return audit_dir / CONTENT_QUALITY_JOB_CONTEXT_FILE


def _active_request(pending: Path) -> dict[str, Any] | None:
    payload = _read_json(pending)
    if not payload:
        return None
    if str(payload.get("status") or "") in {"queued", "starting", "running"}:
        return payload
    return None


def get_content_quality_job_status(audit_dir: Path) -> dict[str, Any]:
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


def _resolve_job_context(
    audit_dir: Path,
    *,
    brand_name: str = "",
    site_url: str = "",
    sample_cap: int | None = None,
) -> dict[str, Any]:
    """Build brand/site/cap context for a CQ run (competitor dirs lack onboarding)."""
    ctx = _read_json(job_context_path(audit_dir)) or {}
    brand = (brand_name or str(ctx.get("brand_name") or "")).strip()
    site = (site_url or str(ctx.get("site_url") or "")).strip()
    cap = sample_cap
    if cap is None and ctx.get("sample_cap") is not None:
        try:
            cap = int(ctx["sample_cap"])
        except (TypeError, ValueError):
            cap = None

    if not brand or not site:
        onboarding_path = audit_dir / "onboarding_context.json"
        onboarding = _read_json(onboarding_path) or {}
        brand = brand or str(
            onboarding.get("brand_name_used") or onboarding.get("brand_name") or ""
        ).strip()
        site = site or str(
            onboarding.get("website_url") or onboarding.get("site_url") or ""
        ).strip()

    if not site:
        summary = _read_json(audit_dir / "audit_summary.json") or {}
        site = str(summary.get("base_url") or "").strip()

    if not brand and site:
        host = (urlparse(site).hostname or "").lower().removeprefix("www.")
        brand = host.split(".")[0].replace("-", " ").title() if host else "the brand"

    return {
        "brand_name": brand or "the brand",
        "site_url": site,
        "sample_cap": cap,
    }


def run_content_quality_analysis(
    audit_dir: Path,
    *,
    request_id: str | None = None,
    brand_name: str = "",
    site_url: str = "",
    sample_cap: int | None = None,
) -> dict[str, str]:
    """Generate Gemini content-quality overlay and persist ``content_quality_gemini.json``."""
    from content_quality_llm import (
        is_content_quality_soft_fail,
        load_or_generate_content_quality_gemini,
    )

    audit_dir = audit_dir.resolve()
    audit_id = geo.audit_dir_api_rel(audit_dir)
    pending = pending_path(audit_dir)
    if request_id:
        cur = _read_json(pending) or {}
        if str(cur.get("request_id") or "") == request_id:
            cur.update({"status": "running", "updated_at": _utc_now()})
            _write_json(pending, cur)

    ctx = _resolve_job_context(
        audit_dir,
        brand_name=brand_name,
        site_url=site_url,
        sample_cap=sample_cap,
    )

    payload, err = load_or_generate_content_quality_gemini(
        audit_dir,
        brand_name=str(ctx["brand_name"]),
        site_url=str(ctx["site_url"]),
        force=True,
        cap=ctx.get("sample_cap"),
    )
    if payload:
        outcome = {"content_quality": "done", "pages_analyzed": str(payload.get("pages_analyzed") or 0)}
        status = "done"
    elif is_content_quality_soft_fail(err):
        # Empty crawl / nothing to assess — clear pending without failing the job.
        outcome = {
            "content_quality": "skipped",
            "reason": str(err or "no_pages"),
            "pages_analyzed": "0",
        }
        status = "skipped"
        err = None
    else:
        outcome = {"content_quality": f"error: {err or 'unknown'}"}
        status = "error"

    if request_id and pending.is_file():
        cur = _read_json(pending) or {}
        if str(cur.get("request_id") or "") == request_id:
            cur.update(
                {
                    "status": status,
                    "updated_at": _utc_now(),
                    "outcome": outcome,
                    "error": None
                    if status in {"done", "skipped"}
                    else (err or "Content quality analysis failed"),
                }
            )
            _write_json(pending, cur)
    log.info("Content quality Gemini %s for %s: %s", status, audit_id, outcome)
    return outcome


def enqueue_content_quality_job(
    audit_dir: Path,
    *,
    brand_name: str = "",
    site_url: str = "",
    sample_cap: int | None = None,
) -> dict[str, Any]:
    """Queue Gemini content-quality analysis after crawl (Cloud Run Job or local thread)."""
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

    # Skip enqueue when a current cache already exists for this audit_summary.
    try:
        from content_quality_llm import load_cached_content_quality_gemini

        if load_cached_content_quality_gemini(audit_dir):
            return {
                "status": "done",
                "audit_id": audit_id,
                "request_id": "",
                "execution": "",
                "already_running": False,
                "cached": True,
            }
    except Exception:
        pass

    ctx = _resolve_job_context(
        audit_dir,
        brand_name=brand_name,
        site_url=site_url,
        sample_cap=sample_cap,
    )
    _write_json(
        job_context_path(audit_dir),
        {
            "brand_name": ctx["brand_name"],
            "site_url": ctx["site_url"],
            "sample_cap": ctx.get("sample_cap"),
            "updated_at": _utc_now(),
        },
    )

    request_id = uuid.uuid4().hex
    requests_dir = audit_dir / CONTENT_QUALITY_JOB_REQUESTS_DIR
    requests_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = requests_dir / f"{request_id}.json"
    manifest = {
        "request_id": request_id,
        "audit_id": audit_id,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "status": "queued",
        "brand_name": ctx["brand_name"],
        "site_url": ctx["site_url"],
        "sample_cap": ctx.get("sample_cap"),
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

    if content_quality_jobs_enabled():
        try:
            execution = _execute_content_quality_job(
                audit_id=audit_id,
                request_id=request_id,
                brand_name=str(ctx["brand_name"]),
                site_url=str(ctx["site_url"]),
                sample_cap=ctx.get("sample_cap"),
            )
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
            log.exception("Failed to launch content-quality Cloud Run Job for %s", audit_id)
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
            # Fall through to local thread so analysis still completes when possible.

    def _worker() -> None:
        try:
            run_content_quality_analysis(
                audit_dir,
                request_id=request_id,
                brand_name=str(ctx["brand_name"]),
                site_url=str(ctx["site_url"]),
                sample_cap=ctx.get("sample_cap"),
            )
            if manifest_path.is_file():
                m = _read_json(manifest_path) or manifest
                m.update({"status": "done", "updated_at": _utc_now()})
                _write_json(manifest_path, m)
        except Exception:
            log.exception("Local content-quality Gemini failed for %s", audit_id)
            if manifest_path.is_file():
                m = _read_json(manifest_path) or manifest
                m.update({"status": "error", "updated_at": _utc_now()})
                _write_json(manifest_path, m)

    threading.Thread(
        target=_worker,
        name=f"content-quality-{request_id[:8]}",
        daemon=True,
    ).start()
    return {
        "status": "queued",
        "audit_id": audit_id,
        "request_id": request_id,
        "execution": "local-thread",
        "already_running": False,
    }


def list_competitor_content_quality_dirs(audit_dir: Path) -> list[Path]:
    """Competitor crawl dirs under ``audit_dir/competitors/`` that have summaries."""
    root = audit_dir.resolve() / "competitors"
    if not root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "audit_summary.json").is_file():
            out.append(child.resolve())
    return out


def _competitor_brand_for_url(audit_dir: Path, site_url: str) -> str:
    config = _read_json(audit_dir / "onboarding_context.json") or {}
    target = site_url.lower().rstrip("/")
    for row in config.get("competitors_detail") or []:
        if not isinstance(row, dict):
            continue
        website = str(row.get("competitor_website") or "").strip().lower().rstrip("/")
        name = str(row.get("competitor_brand") or "").strip()
        if name and website and (website == target or target.startswith(website) or website.startswith(target)):
            return name
    host = (urlparse(site_url).hostname or "").lower().removeprefix("www.")
    return host.split(".")[0].replace("-", " ").title() if host else "Competitor"


def enqueue_competitor_content_quality_jobs(audit_dir: Path) -> list[dict[str, Any]]:
    """Enqueue CQ Gemini for each crawled competitor site (non-blocking).

    Sample cap defaults to **20 per competitor** (same as brand). Set
    ``CONTENT_QUALITY_COMPETITOR_SAMPLE_CAP`` to lower per-competitor cost.
    """
    from content_quality_llm import competitor_sample_cap

    audit_dir = audit_dir.resolve()
    cap = competitor_sample_cap()
    results: list[dict[str, Any]] = []
    for comp_dir in list_competitor_content_quality_dirs(audit_dir):
        summary = _read_json(comp_dir / "audit_summary.json") or {}
        site_url = str(summary.get("base_url") or "").strip()
        brand_name = _competitor_brand_for_url(audit_dir, site_url)
        try:
            result = enqueue_content_quality_job(
                comp_dir,
                brand_name=brand_name,
                site_url=site_url,
                sample_cap=cap,
            )
            result["competitor_dir"] = geo.audit_dir_api_rel(comp_dir)
            results.append(result)
            log.info(
                "Competitor content-quality enqueue for %s: status=%s",
                result.get("competitor_dir"),
                result.get("status"),
            )
        except Exception:
            log.exception("Competitor content-quality enqueue failed for %s", comp_dir)
            results.append(
                {
                    "status": "error",
                    "audit_id": geo.audit_dir_api_rel(comp_dir),
                    "competitor_dir": geo.audit_dir_api_rel(comp_dir),
                    "error": "enqueue_failed",
                }
            )
    return results


def _execute_content_quality_job(
    *,
    audit_id: str,
    request_id: str,
    brand_name: str = "",
    site_url: str = "",
    sample_cap: int | None = None,
) -> str:
    from google.cloud import run_v2

    project = content_quality_job_project()
    region = content_quality_job_region()
    name = content_quality_job_name()
    job_path = f"projects/{project}/locations/{region}/jobs/{name}"
    env = [
        {"name": "CONTENT_QUALITY_AUDIT_ID", "value": audit_id},
        {"name": "CONTENT_QUALITY_REQUEST_ID", "value": request_id},
    ]
    if brand_name:
        env.append({"name": "CONTENT_QUALITY_BRAND_NAME", "value": brand_name})
    if site_url:
        env.append({"name": "CONTENT_QUALITY_SITE_URL", "value": site_url})
    if sample_cap is not None:
        env.append({"name": "CONTENT_QUALITY_SAMPLE_CAP_OVERRIDE", "value": str(int(sample_cap))})
    overrides = {
        "container_overrides": [
            {
                "env": env,
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

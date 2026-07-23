"""Unified audit pipeline idle/running detection.

An audit is still running until *all* related work for that run is idle:
site crawl, prompt probes (when enqueued), content-quality Gemini (brand +
competitors), and sentiment when kicked off as a post-probe side effect.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ACTIVE_STATUSES = frozenset({"queued", "starting", "running"})

# Prefer the earliest unfinished phase when several components are active.
_PHASE_PRIORITY: tuple[str, ...] = (
    "crawl",
    "competitor_crawl",
    "prompt_probes",
    "content_quality",
    "sentiment",
)

_PHASE_DETAIL: dict[str, str] = {
    "crawl": "Site crawl still running…",
    "competitor_crawl": "Competitor crawl still running…",
    "prompt_probes": "AI prompt probes still running…",
    "content_quality": "Content quality analysis still running…",
    "sentiment": "Sentiment analysis still running…",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _pending_file_active(path: Path) -> bool:
    payload = _read_json(path)
    if not payload:
        return False
    return str(payload.get("status") or "") in ACTIVE_STATUSES


def crawl_job_is_active(audit_dir: Path) -> bool:
    """True while a full-audit Cloud Run / local crawl job pending file is active."""
    from api.crawl_jobs import FULL_AUDIT_PENDING_FILE

    return _pending_file_active(audit_dir / FULL_AUDIT_PENDING_FILE)


def competitor_crawl_is_active(audit_dir: Path) -> bool:
    """True while competitor crawl pending or status says in-flight."""
    from api.crawl_jobs import COMPETITOR_CRAWL_PENDING_FILE

    if _pending_file_active(audit_dir / COMPETITOR_CRAWL_PENDING_FILE):
        return True
    try:
        from api import geo_services as geo

        status = geo.read_competitor_crawl_status(audit_dir)
    except Exception:
        return False
    if not status:
        return False
    return str(status.get("status") or "") in ACTIVE_STATUSES


def run_status_file_is_active(audit_dir: Path) -> bool:
    """True when ``audit_run_status.json`` itself reports an in-flight status.

    Reads the file directly (no reconcile) so callers can combine this with
    other signals without recursive status rewrites.
    """
    payload = _read_json(audit_dir / "audit_run_status.json")
    if not payload:
        return False
    return str(payload.get("status") or "") in ACTIVE_STATUSES


def prompt_probes_are_active(audit_dir: Path) -> bool:
    """True while prompt probe pending / locale fan-out is not idle."""
    try:
        from api.prompt_performance import live_probe_is_pending

        return bool(live_probe_is_pending(audit_dir))
    except Exception:
        # Fall back to pending file only if import/reconcile fails.
        return _pending_file_active(audit_dir / "prompt_performance_probe_pending.json")


def content_quality_is_active(audit_dir: Path) -> bool:
    """True while brand or any competitor CQ Gemini job is queued/running."""
    from api.content_quality_jobs import (
        CONTENT_QUALITY_PENDING_FILE,
        list_competitor_content_quality_dirs,
    )

    if _pending_file_active(audit_dir / CONTENT_QUALITY_PENDING_FILE):
        return True
    for comp_dir in list_competitor_content_quality_dirs(audit_dir):
        if _pending_file_active(comp_dir / CONTENT_QUALITY_PENDING_FILE):
            return True
    return False


def sentiment_is_active(audit_dir: Path) -> bool:
    """True while post-probe sentiment job pending is active."""
    from api.sentiment_jobs import SENTIMENT_PENDING_FILE

    return _pending_file_active(audit_dir / SENTIMENT_PENDING_FILE)


def get_audit_run_phase(
    audit_dir: Path,
    *,
    include_run_status: bool = True,
) -> dict[str, Any]:
    """Return pipeline phase + per-component flags for an audit directory.

    ``include_run_status``: when False, ignore ``audit_run_status.json`` so
    orphaned "running" flags can be cleared once other work is idle.
    """
    audit_dir = audit_dir.resolve()
    components = {
        "crawl": crawl_job_is_active(audit_dir),
        "competitor_crawl": competitor_crawl_is_active(audit_dir),
        "prompt_probes": prompt_probes_are_active(audit_dir),
        "content_quality": content_quality_is_active(audit_dir),
        "sentiment": sentiment_is_active(audit_dir),
    }
    if include_run_status:
        components["run_status"] = run_status_file_is_active(audit_dir)
    else:
        components["run_status"] = False

    still_running = any(components.values())
    phase = "idle"
    if still_running:
        for name in _PHASE_PRIORITY:
            if components.get(name):
                phase = name
                break
        else:
            # Only run_status file left — treat as generic crawl/progress.
            phase = "crawl" if components.get("run_status") else "idle"

    return {
        "still_running": still_running,
        "phase": phase,
        "detail": _PHASE_DETAIL.get(phase, "Audit still running…") if still_running else "Audit complete",
        "components": components,
    }


def audit_pipeline_is_running(
    audit_dir: Path,
    *,
    include_run_status: bool = True,
) -> bool:
    """True when any crawl / probe / CQ / sentiment work for this audit is active."""
    return bool(
        get_audit_run_phase(audit_dir, include_run_status=include_run_status)["still_running"]
    )


def attach_pipeline_status(status: dict[str, Any], audit_dir: Path) -> dict[str, Any]:
    """Merge pipeline fields into a run-status payload; keep ``status`` running if busy.

    Clients that treat ``status === "done"`` as navigation-ready (wizard, config
    re-run) will keep polling until the whole pipeline is idle.
    """
    pipeline = get_audit_run_phase(audit_dir)
    out = {
        **status,
        "still_running": pipeline["still_running"],
        "pipeline_phase": pipeline["phase"],
        "pipeline_components": pipeline["components"],
    }
    if not pipeline["still_running"]:
        return out

    current = str(out.get("status") or "")
    if current in {"error"}:
        # Preserve hard failures; still expose that side work may be active.
        return out

    if current not in ACTIVE_STATUSES:
        out["status"] = "running"
        out["detail"] = pipeline["detail"]
        # Avoid advertising 100% while follow-on jobs are still in flight.
        try:
            pct = int(out.get("percent") or 0)
        except (TypeError, ValueError):
            pct = 0
        if pct >= 100:
            out["percent"] = 99
        if not out.get("current_step") or out.get("current_step") in {"finish", "done"}:
            out["current_step"] = pipeline["phase"]
    elif not out.get("detail"):
        out["detail"] = pipeline["detail"]
    return out

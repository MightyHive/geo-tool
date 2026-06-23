"""Executive summary generation (Gemini) for GEO audit reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api import geo_services as geo
from executive_summary_llm import (
    EXECUTIVE_SUMMARY_FILE,
    generate_and_cache_for_audit_dir,
    load_cached_executive_summary,
    sanitize_executive_html,
)

router = APIRouter(prefix="/api/audits", tags=["executive-summary"])


class ExecutiveSummaryBody(BaseModel):
    model: str | None = Field(default=None, description="Optional Gemini model override")
    refresh: bool = Field(default=False, description="Regenerate even if cache exists")


def _audit_dir_or_404(audit_id: str) -> Path:
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    return audit_dir


@router.get("/{audit_id}/executive-summary")
def get_executive_summary(audit_id: str) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    cached = load_cached_executive_summary(audit_dir)
    if not cached or not str(cached.get("paragraph_html") or "").strip():
        raise HTTPException(404, f"No cached executive summary ({EXECUTIVE_SUMMARY_FILE})")
    return {
        "audit_dir": geo.audit_dir_api_rel(audit_dir),
        "cached": True,
        **cached,
    }


@router.post("/{audit_id}/executive-summary")
def post_executive_summary(audit_id: str, body: ExecutiveSummaryBody | None = None) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    refresh = body.refresh if body is not None else False
    model = body.model if body is not None else None
    if not refresh:
        cached = load_cached_executive_summary(audit_dir)
        if cached and str(cached.get("paragraph_html") or "").strip():
            return {
                "audit_dir": geo.audit_dir_api_rel(audit_dir),
                "cached": True,
                **cached,
            }
    try:
        doc = generate_and_cache_for_audit_dir(audit_dir, model=model)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Executive summary generation failed: {exc}") from exc
    return {
        "audit_dir": geo.audit_dir_api_rel(audit_dir),
        "cached": False,
        **doc,
        "paragraph_html": sanitize_executive_html(str(doc.get("paragraph_html") or "")),
    }

"""Recommendations tab: Gemini action plan + projected performance narrative."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api import geo_services as geo
from recommendations_llm import (
    RECOMMENDATIONS_FILE,
    generate_and_cache_for_audit_dir,
    load_cached_recommendations,
)

router = APIRouter(prefix="/api/audits", tags=["recommendations"])


class RecommendationsBody(BaseModel):
    model: str | None = Field(default=None, description="Optional Gemini model override")
    refresh: bool = Field(default=False, description="Regenerate even if cache exists")


def _audit_dir_or_404(audit_id: str) -> Path:
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    return audit_dir


@router.get("/{audit_id}/recommendations")
def get_recommendations(audit_id: str) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    cached = load_cached_recommendations(audit_dir)
    if not cached or not (
        cached.get("quick") or cached.get("medium") or cached.get("strategic")
    ):
        raise HTTPException(404, f"No cached recommendations ({RECOMMENDATIONS_FILE})")
    return {
        "audit_dir": geo.audit_dir_api_rel(audit_dir),
        "cached": True,
        **cached,
    }


@router.post("/{audit_id}/recommendations")
def post_recommendations(audit_id: str, body: RecommendationsBody | None = None) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    refresh = body.refresh if body is not None else False
    model = body.model if body is not None else None
    if not refresh:
        cached = load_cached_recommendations(audit_dir)
        if cached and (
            cached.get("quick") or cached.get("medium") or cached.get("strategic")
        ):
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
        raise HTTPException(502, f"Recommendations generation failed: {exc}") from exc
    return {
        "audit_dir": geo.audit_dir_api_rel(audit_dir),
        "cached": False,
        **doc,
    }

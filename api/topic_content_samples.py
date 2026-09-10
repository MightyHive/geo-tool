"""FastAPI routes for evidence-backed topic content samples."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel

from api import geo_services as geo
from api.topic_content_jobs import (
    ACTIVE_STATES,
    enqueue_topic_content_job,
    get_topic_content_job_states,
)
from topic_content_generator import _is_ok_or_below, ensure_evidence, parse_structure

router = APIRouter(prefix="/api/audits", tags=["topic-content"])
log = logging.getLogger(__name__)


class TopicContentRequest(BaseModel):
    topic: str
    structure: Any | None = None
    refresh: bool = False


def _audit_dir_or_404(audit_id: str) -> Path:
    """Match other report APIs: existence only, no ownership gate.

    Local/shared audits are readable through the same path as executive
    summary and recommendations. Ownership checks caused false 404s for
    valid report sessions that were not listed in the user's archive runs.
    """
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    return audit_dir


def _canonical_topic(artifact: dict[str, Any], requested: str) -> str:
    clean = requested.strip()
    topics = artifact.get("topics") if isinstance(artifact.get("topics"), dict) else {}
    if clean in topics:
        return clean
    match = next((topic for topic in topics if topic.casefold() == clean.casefold()), "")
    if not match:
        raise HTTPException(404, "Topic not found in completed prompt responses")
    return match


def _safe_http_url(value: Any) -> str | None:
    text = str(value or "").strip()
    parsed = urlparse(text)
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _response_payload(
    audit_id: str,
    artifact: dict[str, Any],
    states: dict[str, Any],
    *,
    auto_enqueued: dict[str, Any] | None = None,
) -> dict[str, Any]:
    topics = artifact.get("topics") if isinstance(artifact.get("topics"), dict) else {}
    ordered = sorted(
        (
            value.get("evidence")
            for value in topics.values()
            if isinstance(value, dict) and isinstance(value.get("evidence"), dict)
        ),
        key=lambda item: (int(item.get("rank") or 10**9), str(item.get("topic") or "").casefold()),
    )
    rows: list[dict[str, Any]] = []
    for evidence in ordered:
        topic = str(evidence.get("topic") or "")
        entry = topics.get(topic) if isinstance(topics.get(topic), dict) else {}
        sample = entry.get("sample") if isinstance(entry.get("sample"), dict) else None
        state = states.get(topic) if isinstance(states.get(topic), dict) else {}
        state_status = str(state.get("status") or "")
        if state_status in ACTIVE_STATES:
            topic_status = "running" if state_status in {"running", "retrying"} else "queued"
        elif state_status == "error":
            topic_status = "error"
        elif sample:
            topic_status = "done"
        else:
            topic_status = "idle"

        raw_outline = sample.get("outline") if sample and isinstance(sample.get("outline"), dict) else None
        outline = None
        if raw_outline:
            sample_evidence = (
                sample.get("evidence_snapshot")
                if isinstance(sample.get("evidence_snapshot"), dict)
                else evidence
            )
            evidence_sources: list[dict[str, Any]] = []
            for example in (sample_evidence.get("evidence_examples") or [])[:6]:
                if not isinstance(example, dict):
                    continue
                citations = [
                    item
                    for item in (example.get("citations") or [])
                    if isinstance(item, dict)
                ]
                first_citation = citations[0] if citations else {}
                evidence_sources.append(
                    {
                        "title": example.get("prompt") or "Prompt response",
                        "url": _safe_http_url(
                            first_citation.get("url") or first_citation.get("link")
                        ),
                        "source": example.get("platform") or "AI response",
                        "detail": str(example.get("response_excerpt") or "")[:260],
                    }
                )
            outline = {
                "title": raw_outline.get("title") or "",
                "meta_description": raw_outline.get("meta_description") or "",
                "audience": raw_outline.get("audience") or "",
                "intent": raw_outline.get("intent") or "",
                "sections": [
                    {
                        "id": section.get("id") or "",
                        "heading": section.get("heading") or "",
                        "level": 3 if str(section.get("level") or "").upper() == "H3" else 2,
                        "instructions": section.get("instructions") or "",
                        "sample_copy": section.get("sample_copy") or "",
                        "evidence_opportunities": section.get(
                            "evidence_citation_opportunities"
                        )
                        or [],
                    }
                    for section in (raw_outline.get("sections") or [])
                    if isinstance(section, dict)
                ],
                "faqs": [
                    {
                        "question": faq.get("question") or "",
                        "guidance": faq.get("answer_guidance") or "",
                        "evidence_opportunities": faq.get(
                            "evidence_citation_opportunities"
                        )
                        or [],
                    }
                    for faq in (raw_outline.get("faqs") or [])
                    if isinstance(faq, dict)
                ],
                "internal_links": [
                    {
                        "anchor_text": link.get("anchor_text") or "",
                        "target_url": link.get("target_url_or_path") or "",
                        "rationale": link.get("rationale") or "",
                    }
                    for link in (raw_outline.get("internal_links") or [])
                    if isinstance(link, dict)
                ],
                "provenance": evidence_sources + [
                    {
                        "source": "Audit prompt evidence",
                        "detail": (
                            f"{int(evidence.get('brand_mentions') or 0)} brand mentions "
                            f"across {int(evidence.get('responses') or 0)} completed responses"
                        ),
                    },
                    {
                        "source": sample.get("model") or "gemini-3.5-flash",
                        "detail": f"Generated {sample.get('generated_at') or ''}".strip(),
                    },
                ],
            }

        related_prompts = evidence.get("related_prompts") or []
        evidence_examples = evidence.get("evidence_examples") or []
        suggested = bool(evidence.get("suggested"))
        if "suggested" not in evidence and evidence.get("visibility") is not None:
            suggested = _is_ok_or_below(float(evidence["visibility"]))
        rows.append(
            {
                "topic": topic,
                "visibility": evidence.get("visibility"),
                "suggested": suggested,
                "evidence_count": len(evidence_examples) or len(related_prompts),
                "response_count": int(evidence.get("responses") or 0),
                "mention_count": int(evidence.get("brand_mentions") or 0),
                "status": topic_status,
                "outline": outline,
                "error": state.get("error"),
                "updated_at": (
                    sample.get("generated_at") if sample else state.get("updated_at")
                ),
            }
        )

    return {
        "audit_id": audit_id,
        "version": artifact.get("version"),
        "updated_at": artifact.get("updated_at"),
        "source_probe": artifact.get("source_probe") or {},
        "has_probe_data": bool(rows),
        "topics": rows,
        "auto_enqueued": auto_enqueued,
    }


@router.get("/{audit_id}/topic-content-samples")
def get_topic_content_samples(audit_id: str) -> dict[str, Any]:
    """Return current evidence/samples and queue the lowest topic once if absent."""
    audit_dir = _audit_dir_or_404(audit_id)
    try:
        # ensure_evidence also supplies the legacy fallback when no versioned
        # topic artifact has been written yet.
        artifact = ensure_evidence(audit_dir)
    except Exception as exc:
        log.exception("Could not build topic evidence for %s", audit_id)
        raise HTTPException(500, f"Could not build topic evidence: {exc}") from exc
    states = get_topic_content_job_states(audit_dir)
    topics = artifact.get("topics") if isinstance(artifact.get("topics"), dict) else {}
    evidence = [
        value.get("evidence")
        for value in topics.values()
        if isinstance(value, dict) and isinstance(value.get("evidence"), dict)
    ]
    evidence.sort(
        key=lambda item: (
            0 if item.get("suggested") else 1,
            float(item.get("visibility") if item.get("visibility") is not None else 10**9),
            int(item.get("rank") or 10**9),
            str(item.get("topic") or "").casefold(),
        )
    )
    auto: dict[str, Any] | None = None
    if evidence:
        # Prefer the weakest Recommendations-aligned (OK-or-below) topic.
        lowest = str(evidence[0].get("topic") or "")
        entry = topics.get(lowest) if isinstance(topics.get(lowest), dict) else {}
        state = states.get(lowest) if isinstance(states.get(lowest), dict) else {}
        if (
            lowest
            and not entry.get("sample")
            and not state
        ):
            try:
                auto = enqueue_topic_content_job(audit_dir, topic=lowest)
                states = get_topic_content_job_states(audit_dir)
            except Exception as exc:
                log.exception("Could not auto-enqueue topic sample for %s", audit_id)
                auto = {"status": "error", "topic": lowest, "error": str(exc)}
    return _response_payload(audit_id, artifact, states, auto_enqueued=auto)


@router.post(
    "/{audit_id}/topic-content-samples",
    status_code=status.HTTP_202_ACCEPTED,
)
def request_topic_content_sample(
    audit_id: str,
    body: TopicContentRequest,
    response: Response,
) -> dict[str, Any]:
    """Queue an on-demand sample or replace one using an edited structure."""
    audit_dir = _audit_dir_or_404(audit_id)
    try:
        artifact = ensure_evidence(audit_dir, refresh=body.refresh)
    except Exception as exc:
        raise HTTPException(500, f"Could not refresh topic evidence: {exc}") from exc
    topic = _canonical_topic(artifact, body.topic)
    try:
        structure = parse_structure(body.structure)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    entry = (artifact.get("topics") or {}).get(topic) or {}
    if entry.get("sample") is not None and not body.refresh and structure is None:
        response.status_code = status.HTTP_200_OK
        return _response_payload(
            audit_id,
            artifact,
            get_topic_content_job_states(audit_dir),
        )
    try:
        queued = enqueue_topic_content_job(
            audit_dir,
            topic=topic,
            structure=structure,
            refresh=body.refresh,
        )
    except Exception as exc:
        log.exception("Could not enqueue topic sample for %s / %s", audit_id, topic)
        raise HTTPException(502, str(exc)) from exc
    states = get_topic_content_job_states(audit_dir)
    latest = ensure_evidence(audit_dir)
    return _response_payload(audit_id, latest, states, auto_enqueued=queued)

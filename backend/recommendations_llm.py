"""
Gemini-generated prioritized action plan and projected-performance narrative.

Uses ``google.genai`` ``generate_content`` (same stack as :mod:`executive_summary_llm`).
Editorial rules: ``skills/action-plan.md`` and ``skills/create-report.md`` § Section 7.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from executive_summary_llm import (
    build_executive_digest,
    score_audit_for_executive,
)
from geo_setup_llm import build_genai_client
from report_copy import client_friendly_text, prepare_report_priorities
from report_llm_util import (
    REPO_ROOT,
    load_skill_chunk,
    read_json,
    sanitize_limited_html,
    truthy_env,
)

ACTION_PLAN_SKILL_PATH = REPO_ROOT / "skills" / "action-plan.md"
CREATE_REPORT_SKILL_PATH = REPO_ROOT / "skills" / "create-report.md"
RECOMMENDATIONS_FILE = "recommendations.json"
GEMINI_MODEL = (os.environ.get("GEMINI_RECOMMENDATIONS_MODEL") or "gemini-3.5-flash").strip()
MAX_OUTPUT_TOKENS = 8192
MAX_ITEMS_PER_PHASE = 5


class ActionPlanItem(BaseModel):
    action: str = Field(
        description=(
            "One assignable action in plain English (starts with a verb). "
            "Include Owner and Effort only if space allows; do not return owner/effort alone."
        ),
        min_length=24,
    )


class RecommendationsResponse(BaseModel):
    quick: list[ActionPlanItem] = Field(
        description="Quick wins (0–30 days): max 5 items",
        max_length=MAX_ITEMS_PER_PHASE,
    )
    medium: list[ActionPlanItem] = Field(
        description="Medium-term (30–90 days): max 5 items",
        max_length=MAX_ITEMS_PER_PHASE,
    )
    strategic: list[ActionPlanItem] = Field(
        description=(
            "Strategic (90+ days): max 5 programme-scale actions focused on content, "
            "brand authority, and citability"
        ),
        max_length=MAX_ITEMS_PER_PHASE,
    )
    policy_notes: list[str] = Field(
        description="Optional policy-only notes (training crawlers, etc.), max 5",
        max_length=MAX_ITEMS_PER_PHASE,
    )
    projected_narrative_html: str = Field(
        description=(
            "2–4 sentences for the Projected performance section: directional only, "
            "not a guarantee. Mention current composite score and rough bands after phases. "
            "Use <strong> for numbers. No <p> wrapper."
        )
    )


def load_action_plan_skills() -> str:
    action = ""
    if ACTION_PLAN_SKILL_PATH.is_file():
        action = ACTION_PLAN_SKILL_PATH.read_text(encoding="utf-8", errors="replace")[:12_000]
    section7 = load_skill_chunk(
        CREATE_REPORT_SKILL_PATH,
        "# Section 7: Prioritised action plan",
        end_marker="# Section 8:",
    )
    parts = []
    if section7:
        parts.append("## create-report.md — Section 7\n\n" + section7)
    if action:
        parts.append("## action-plan.md\n\n" + action)
    return "\n\n---\n\n".join(parts) if parts else section7 or action


def _normalize_lines(items: list[str], *, limit: int = MAX_ITEMS_PER_PHASE) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        line = client_friendly_text(str(raw).strip())
        if not line:
            continue
        key = " ".join(line.lower().split())[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _backfill_strategic(strategic: list[str]) -> list[str]:
    from report_copy import _backfill_strategic_column

    return _backfill_strategic_column(strategic, limit=MAX_ITEMS_PER_PHASE)


def build_recommendations_digest(
    audit: dict[str, Any],
    *,
    overall: float,
    categories: list[Any],
    priorities: list[str],
    working: list[str],
    fallback_phases: tuple[list[str], list[str], list[str], list[str]],
) -> dict[str, Any]:
    digest = build_executive_digest(
        audit,
        overall=overall,
        categories=categories,
        priorities=priorities,
        working=working,
    )
    backlog: list[str] = []
    for c in categories:
        for x in getattr(c, "improvements", None) or []:
            t = client_friendly_text(str(x).strip())
            if t:
                backlog.append(t)
    for p in priorities:
        t = client_friendly_text(str(p).strip())
        if t:
            backlog.append(t)
    seen: set[str] = set()
    unique_backlog: list[str] = []
    for line in backlog:
        k = line.lower()[:100]
        if k in seen:
            continue
        seen.add(k)
        unique_backlog.append(line)
    quick_f, medium_f, strategic_f, policy_f = fallback_phases
    digest["improvement_backlog"] = unique_backlog[:45]
    digest["deterministic_plan_hint"] = {
        "quick": [client_friendly_text(x) for x in quick_f[:MAX_ITEMS_PER_PHASE]],
        "medium": [client_friendly_text(x) for x in medium_f[:MAX_ITEMS_PER_PHASE]],
        "strategic": [client_friendly_text(x) for x in strategic_f[:MAX_ITEMS_PER_PHASE]],
        "policy_notes": [client_friendly_text(x) for x in policy_f[:MAX_ITEMS_PER_PHASE]],
    }
    return digest


def _generation_config() -> Any:
    from google.genai import types

    return types.GenerateContentConfig(
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.35,
        top_p=0.9,
        response_mime_type="application/json",
        response_schema=RecommendationsResponse,
    )


def generate_recommendations(
    digest: dict[str, Any],
    *,
    model: str | None = None,
) -> RecommendationsResponse:
    skills = load_action_plan_skills()
    payload = json.dumps(digest, ensure_ascii=False, indent=2)
    brand = (digest.get("brand") or "").strip() or "the brand"
    site = (digest.get("site_url") or "").strip() or "the site"
    prompt = f"""
You are building the **Recommendations** tab of a GEO audit report for **{brand}** ({site}).

Produce:
1. A **prioritized action plan** in three horizons (Quick wins 0–30d, Medium-term 30–90d, Strategic 90+d).
2. A short **projected performance** narrative (directional score bands, not a forecast).

Follow the skills below. Use the audit JSON and ``deterministic_plan_hint`` as input—improve wording for a marketing lead, fix ordering (blockers before measurement tasks), and dedupe themes. Strategic items must be long-horizon content/brand/citability programmes, not quick technical patches.

Rules:
- At most **5** items per horizon; policy_notes separate (training-crawler policy only).
- Each action item must be a full sentence starting with a verb (e.g. Publish, Update, Add)—never return only "Owner" or "Effort" metadata.
- Plain English; explain technical terms briefly when used.
- Do not mention internal file paths or audit tooling.
- projected_narrative_html: directional disclaimer; reference overall_score and realistic post-phase bands.

## Skills

{skills}

---

## Audit data (JSON)

{payload}
""".strip()

    client = build_genai_client()
    mid = (model or GEMINI_MODEL).strip()
    resp = client.models.generate_content(model=mid, contents=prompt, config=_generation_config())
    text = (resp.text or "").strip()
    if not text:
        raise ValueError("Empty Gemini response for recommendations")
    data = json.loads(text)
    return RecommendationsResponse.model_validate(data)


def _items_to_lines(items: list[ActionPlanItem]) -> list[str]:
    return _normalize_lines([x.action for x in items])


def phases_from_response(parsed: RecommendationsResponse) -> tuple[list[str], list[str], list[str], list[str], str]:
    quick = _items_to_lines(parsed.quick)
    medium = _items_to_lines(parsed.medium)
    strategic = _backfill_strategic(_items_to_lines(parsed.strategic))
    policy = _normalize_lines(parsed.policy_notes, limit=MAX_ITEMS_PER_PHASE)
    narrative = sanitize_limited_html(parsed.projected_narrative_html)
    return quick, medium, strategic, policy, narrative


def load_cached_recommendations(audit_dir: Path) -> dict[str, Any] | None:
    return read_json(audit_dir / RECOMMENDATIONS_FILE)


def save_recommendations_cache(
    audit_dir: Path,
    *,
    quick: list[str],
    medium: list[str],
    strategic: list[str],
    policy_notes: list[str],
    projected_narrative_html: str,
    source: str = "gemini",
) -> dict[str, Any]:
    doc = {
        "quick": quick,
        "medium": medium,
        "strategic": strategic,
        "policy_notes": policy_notes,
        "projected_narrative_html": projected_narrative_html,
        "source": source,
        "model": GEMINI_MODEL,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    path = audit_dir / RECOMMENDATIONS_FILE
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


def score_audit_with_plan_phases(
    audit_dir: Path,
) -> tuple[dict[str, Any], float, list[Any], list[str], list[str], tuple[list[str], list[str], list[str], list[str]]]:
    """Score audit and return deterministic action-plan phases (fallback)."""
    audit, overall, categories, priorities, working = score_audit_for_executive(audit_dir)
    from api.geo_services import load_create_report

    cr = load_create_report()
    weights = dict(getattr(cr, "DEFAULT_WEIGHTS", {}))
    wsum = sum(weights.values())
    if abs(wsum - 100.0) > 0.01 and wsum > 0:
        weights = {k: v * 100.0 / wsum for k, v in weights.items()}
    comp_path = audit_dir / "comparison.json"
    if not comp_path.is_file():
        comp_path = None
    _, ci, _, _ = cr.build_competitive_section(comp_path, audit.get("base_url") or "", weights)
    priorities_raw = cr._consolidate_improvement_lines(
        cr._unique_preserve([x for c in categories for x in c.improvements] + ci)
    )
    quick, medium, strategic, policy, _narr = prepare_report_priorities(priorities_raw)
    return audit, overall, categories, priorities, working, (quick, medium, strategic, policy)


def generate_and_cache_for_audit_dir(audit_dir: Path, *, model: str | None = None) -> dict[str, Any]:
    audit_dir = audit_dir.resolve()
    audit, overall, categories, priorities, working, fallback = score_audit_with_plan_phases(audit_dir)
    digest = build_recommendations_digest(
        audit,
        overall=overall,
        categories=categories,
        priorities=priorities,
        working=working,
        fallback_phases=fallback,
    )
    parsed = generate_recommendations(digest, model=model)
    quick, medium, strategic, policy, narrative = phases_from_response(parsed)
    return save_recommendations_cache(
        audit_dir,
        quick=quick,
        medium=medium,
        strategic=strategic,
        policy_notes=policy,
        projected_narrative_html=narrative,
    )


def _phases_from_cache(cached: dict[str, Any]) -> tuple[list[str], list[str], list[str], list[str], str]:
    quick = _normalize_lines(list(cached.get("quick") or []))
    medium = _normalize_lines(list(cached.get("medium") or []))
    strategic = _backfill_strategic(_normalize_lines(list(cached.get("strategic") or [])))
    policy = _normalize_lines(list(cached.get("policy_notes") or []))
    narrative = sanitize_limited_html(str(cached.get("projected_narrative_html") or ""))
    return quick, medium, strategic, policy, narrative


def resolve_recommendations_for_report(
    audit_dir: Path,
    audit: dict[str, Any],
    overall: float,
    categories: list[Any],
    priorities: list[str],
    working: list[str],
    fallback_phases: tuple[list[str], list[str], list[str], list[str]],
    *,
    model: str | None = None,
) -> tuple[tuple[list[str], list[str], list[str], list[str]], str | None]:
    """
    Return (quick, medium, strategic, policy) and optional projected-performance narrative HTML.
    Raises on disabled; caller should fall back to deterministic phases.
    """
    audit_dir = audit_dir.resolve()
    if truthy_env("GEO_RECOMMENDATIONS_USE_CACHE"):
        cached = load_cached_recommendations(audit_dir)
        if cached and (cached.get("quick") or cached.get("medium") or cached.get("strategic")):
            q, m, s, p, narrative = _phases_from_cache(cached)
            return (q, m, s, p), narrative or None
    if truthy_env("GEO_RECOMMENDATIONS_DISABLE"):
        raise RuntimeError("Recommendations LLM disabled (GEO_RECOMMENDATIONS_DISABLE)")
    digest = build_recommendations_digest(
        audit,
        overall=overall,
        categories=categories,
        priorities=priorities,
        working=working,
        fallback_phases=fallback_phases,
    )
    parsed = generate_recommendations(digest, model=model)
    quick, medium, strategic, policy, narrative = phases_from_response(parsed)
    try:
        save_recommendations_cache(
            audit_dir,
            quick=quick,
            medium=medium,
            strategic=strategic,
            policy_notes=policy,
            projected_narrative_html=narrative,
        )
    except OSError:
        pass
    return (quick, medium, strategic, policy), narrative or None

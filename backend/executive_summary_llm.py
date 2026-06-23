"""
Gemini-generated executive summary paragraph for GEO audit reports.

Uses ``google.genai`` ``generate_content`` (same client as :mod:`geo_setup_llm` / :mod:`insights_llm`).
Editorial rules come from ``skills/create-report.md`` § Section 2: Executive summary.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from geo_setup_llm import build_genai_client
from report_copy import client_friendly_text, priorities_for_executive

from geo_app_env import REPO_ROOT

SKILL_PATH = REPO_ROOT / "skills" / "create-report.md"
EXECUTIVE_SUMMARY_FILE = "executive_summary.json"
GEMINI_MODEL = (os.environ.get("GEMINI_EXEC_SUMMARY_MODEL") or "gemini-3.5-flash").strip()
MAX_OUTPUT_TOKENS = 2048


class ExecutiveSummaryResponse(BaseModel):
    paragraph_html: str = Field(
        description=(
            "Single executive-summary paragraph as HTML fragment: 4–6 sentences, "
            "plain English, no tool narration. Use <strong> for page count, score mention, "
            "and exactly three top priorities. Do not wrap in <p>."
        )
    )


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def load_executive_summary_skill() -> str:
    """Extract Section 2 (Executive summary) from the create-report skill."""
    if not SKILL_PATH.is_file():
        return (
            "Write exactly one paragraph (4–6 sentences). Include pages sampled, "
            "overall score if useful, the single most important finding, three bolded priorities, "
            "and business impact. Use <strong> for priorities."
        )
    text = SKILL_PATH.read_text(encoding="utf-8", errors="replace")
    start = text.find("# Section 2: Executive summary")
    if start < 0:
        return text[:4000]
    end = text.find("# Section 3:", start)
    chunk = text[start:end] if end > start else text[start:]
    return chunk.strip()


def _score_label(score: float) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 60:
        return "Moderate"
    if score >= 40:
        return "Weak"
    return "Poor"


def _crawl_caveats(audit: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    pages = audit.get("pages") if isinstance(audit.get("pages"), list) else []
    n = len(pages)
    fe = ""
    for pg in pages:
        if isinstance(pg, dict) and str(pg.get("fetch_error") or "").strip():
            fe = str(pg.get("fetch_error") or "").strip()
            break
    rt = audit.get("robots_txt") if isinstance(audit.get("robots_txt"), dict) else {}
    rterr = str(rt.get("error") or "").strip() if isinstance(rt, dict) else ""
    err_blob = fe or rterr
    el = err_blob.lower()
    if n == 0:
        notes.append("No sampled pages were recorded.")
    elif n == 1 and err_blob and (
        "certificate_verify_failed" in el or "certificate verify failed" in el
    ):
        notes.append("Single page sample; TLS certificate verification failed on crawl.")
    elif n == 1 and err_blob:
        notes.append("Single page sample; first fetch failed before crawl branched.")
    elif n <= 2 and err_blob:
        notes.append("Small sample with fetch issues—scores are provisional.")
    elif n <= 2:
        notes.append("Small page sample—not a full-site pass.")
    return notes


def build_executive_digest(
    audit: dict[str, Any],
    *,
    overall: float,
    categories: list[Any],
    priorities: list[str],
    working: list[str],
) -> dict[str, Any]:
    """Compact audit context for the model."""
    inputs = audit.get("audit_inputs") if isinstance(audit.get("audit_inputs"), dict) else {}
    pages = audit.get("pages") if isinstance(audit.get("pages"), list) else []
    exec_priorities = priorities_for_executive(list(priorities))
    friendly_priorities = [
        client_friendly_text(p) for p in exec_priorities[:10] if str(p).strip()
    ]
    friendly_strengths = [
        client_friendly_text(s) for s in (working or [])[:6] if str(s).strip()
    ]
    cap_notes = audit.get("_overall_score_cap_notes") or []
    if not isinstance(cap_notes, list):
        cap_notes = []
    cat_rows = []
    for c in categories:
        cat_rows.append(
            {
                "key": getattr(c, "key", ""),
                "title": getattr(c, "title", ""),
                "score": round(float(getattr(c, "score", 0)), 1),
                "weight_pct": round(float(getattr(c, "weight", 0)), 1),
                "top_improvements": [
                    client_friendly_text(x)
                    for x in (getattr(c, "improvements", None) or [])[:4]
                    if str(x).strip()
                ],
            }
        )
    weakest = sorted(cat_rows, key=lambda x: x["score"])[:1]
    return {
        "site_url": str(audit.get("base_url") or "").strip(),
        "brand": str(inputs.get("brand") or "").strip(),
        "industry": str(inputs.get("industry") or "").strip(),
        "pages_sampled": len(pages),
        "crawl_caveats": _crawl_caveats(audit),
        "overall_score": round(float(overall), 1),
        "overall_rating": _score_label(overall),
        "category_scores": cat_rows,
        "weakest_category": weakest[0]["title"] if weakest else "",
        "top_priorities": friendly_priorities,
        "top_strengths": friendly_strengths,
        "score_cap_notes": [client_friendly_text(str(n)) for n in cap_notes[:3] if str(n).strip()],
    }


def sanitize_executive_html(raw: str) -> str:
    """Keep plain text and <strong> only; strip wrapper <p> tags."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = re.sub(r"^<p[^>]*>\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*</p>\s*$", "", s, flags=re.IGNORECASE)

    parts: list[str] = []
    pos = 0
    for m in re.finditer(r"<[^>]+>", s):
        parts.append(s[pos : m.start()])
        tag = m.group(0)
        if re.fullmatch(r"</?strong>", tag, re.IGNORECASE):
            parts.append(tag)
        pos = m.end()
    parts.append(s[pos:])
    out = "".join(parts)
    return " ".join(out.split())


def _generation_config() -> Any:
    from google.genai import types

    return types.GenerateContentConfig(
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.35,
        top_p=0.9,
        response_mime_type="application/json",
        response_schema=ExecutiveSummaryResponse,
    )


def generate_executive_summary_html(
    digest: dict[str, Any],
    *,
    model: str | None = None,
) -> str:
    """Call Gemini and return sanitized HTML fragment for the report callout."""
    skill = load_executive_summary_skill()
    payload = json.dumps(digest, ensure_ascii=False, indent=2)
    brand = (digest.get("brand") or "").strip() or "the brand"
    site = (digest.get("site_url") or "").strip() or "the site"
    prompt = f"""
You are writing the **Executive summary** section of a GEO (Generative Engine Optimization) audit report for **{brand}** ({site}).

Follow the editorial skill below. Prioritise business impact and the audit's weakest areas. Do not narrate tools, crawlers, or file names unless the crawl caveats require a brief caveat.

## Editorial skill (Section 2: Executive summary)

{skill}

---

## Audit data (JSON)

{payload}

---

Return JSON matching the schema: one field ``paragraph_html`` only.
""".strip()

    client = build_genai_client()
    mid = (model or GEMINI_MODEL).strip()
    resp = client.models.generate_content(model=mid, contents=prompt, config=_generation_config())
    text = (resp.text or "").strip()
    if not text:
        raise ValueError("Empty Gemini response for executive summary")
    data = json.loads(text)
    parsed = ExecutiveSummaryResponse.model_validate(data)
    html = sanitize_executive_html(parsed.paragraph_html)
    if not html:
        raise ValueError("Executive summary model returned empty paragraph_html")
    return html


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def load_cached_executive_summary(audit_dir: Path) -> dict[str, Any] | None:
    return _read_json(audit_dir / EXECUTIVE_SUMMARY_FILE)


def save_executive_summary_cache(audit_dir: Path, paragraph_html: str, *, source: str = "gemini") -> dict[str, Any]:
    doc = {
        "paragraph_html": paragraph_html,
        "source": source,
        "model": GEMINI_MODEL,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    path = audit_dir / EXECUTIVE_SUMMARY_FILE
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


def score_audit_for_executive(audit_dir: Path) -> tuple[dict[str, Any], float, list[Any], list[str], list[str]]:
    """Load audit JSON and compute report scores/priorities (same as create-report)."""
    from api.geo_services import load_create_report

    cr = load_create_report()
    summary_path = audit_dir / "audit_summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(summary_path)
    audit = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
    weights = dict(getattr(cr, "DEFAULT_WEIGHTS", {}))
    wsum = sum(weights.values())
    if abs(wsum - 100.0) > 0.01 and wsum > 0:
        weights = {k: v * 100.0 / wsum for k, v in weights.items()}
    cr.ensure_brand_visibility_on_audit(audit)
    overall, categories = cr.score_audit(audit, weights)
    comp_path = audit_dir / "comparison.json"
    if not comp_path.is_file():
        comp_path = None
    cs, ci, _, _ = cr.build_competitive_section(comp_path, audit.get("base_url") or "", weights)
    working = cr._consolidate_strength_lines(
        cr._unique_preserve([x for c in categories for x in c.strengths] + cs)
    )
    priorities_raw = cr._consolidate_improvement_lines(
        cr._unique_preserve([x for c in categories for x in c.improvements] + ci)
    )
    _, _, _, _, priorities = cr.prepare_report_priorities(priorities_raw)
    return audit, overall, categories, priorities, working


def generate_and_cache_for_audit_dir(audit_dir: Path, *, model: str | None = None) -> dict[str, Any]:
    audit_dir = audit_dir.resolve()
    audit, overall, categories, priorities, working = score_audit_for_executive(audit_dir)
    digest = build_executive_digest(
        audit,
        overall=overall,
        categories=categories,
        priorities=priorities,
        working=working,
    )
    html = generate_executive_summary_html(digest, model=model)
    return save_executive_summary_cache(audit_dir, html)


def paragraph_for_report(
    audit_dir: Path,
    audit: dict[str, Any],
    overall: float,
    categories: list[Any],
    priorities: list[str],
    working: list[str],
    *,
    model: str | None = None,
) -> str:
    """
    Resolve executive summary HTML for report rendering: optional cache, then Gemini, else caller fallback.
    """
    audit_dir = audit_dir.resolve()
    if _truthy("GEO_EXEC_SUMMARY_USE_CACHE"):
        cached = load_cached_executive_summary(audit_dir)
        if cached and str(cached.get("paragraph_html") or "").strip():
            return sanitize_executive_html(str(cached["paragraph_html"]))
    if _truthy("GEO_EXEC_SUMMARY_DISABLE"):
        raise RuntimeError("Executive summary LLM disabled (GEO_EXEC_SUMMARY_DISABLE)")
    digest = build_executive_digest(
        audit,
        overall=overall,
        categories=categories,
        priorities=priorities,
        working=working,
    )
    html = generate_executive_summary_html(digest, model=model)
    try:
        save_executive_summary_cache(audit_dir, html)
    except OSError:
        pass
    return html

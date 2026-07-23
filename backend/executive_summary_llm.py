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
from report_copy import client_friendly_text

from geo_app_env import REPO_ROOT
from report_score import format_report_score, round_report_score, score_label as _score_label

SKILL_PATH = REPO_ROOT / "skills" / "create-report.md"
EXECUTIVE_SUMMARY_FILE = "executive_summary.json"
# v4: pass canonical score bands + SOV rank framing so the model cannot invent Weak for 60
# or treat #1 SOV as a negative absolute-% story.
EXECUTIVE_SUMMARY_SCHEMA_VERSION = 4
GEMINI_MODEL = (os.environ.get("GEMINI_EXEC_SUMMARY_MODEL") or "gemini-3.5-flash").strip()
MAX_OUTPUT_TOKENS = 2048

# Matches web/src/lib/reportScore.ts scoreLabel (bands apply to rounded display integers).
SCORE_LABEL_BANDS: tuple[dict[str, Any], ...] = (
    {"min": 90, "max": 100, "label": "Excellent"},
    {"min": 75, "max": 89, "label": "Good"},
    {"min": 60, "max": 74, "label": "OK"},
    {"min": 40, "max": 59, "label": "Weak"},
    {"min": 0, "max": 39, "label": "Poor"},
)

PILLAR_TITLES: tuple[str, ...] = (
    "AI Visibility",
    "Technical Setup",
    "Content Quality & Structure",
)


class ExecutiveSummaryResponse(BaseModel):
    paragraph_html: str = Field(
        description=(
            "Single executive-summary paragraph as HTML fragment: 2–3 sentences, "
            "plain English, no tool narration or recommendations. Keep it to 2–3 concise "
            "sentences and use <strong> sparingly for key evidence. Do not wrap in <p>."
        )
    )


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def load_executive_summary_skill() -> str:
    """Extract Section 2 (Executive summary) from the create-report skill."""
    if not SKILL_PATH.is_file():
        return (
            "Write exactly one concise paragraph of 2–3 sentences. Summarise the overall score, "
            "the most important audit finding, and prompt-performance evidence. Do not recommend actions."
        )
    text = SKILL_PATH.read_text(encoding="utf-8", errors="replace")
    start = text.find("# Section 2: Executive summary")
    if start < 0:
        return text[:4000]
    end = text.find("# Section 3:", start)
    chunk = text[start:end] if end > start else text[start:]
    return chunk.strip()


def _sov_framing(
    *,
    sov_rank: int | None,
    sov_pct: float,
    competitor_count: int,
) -> dict[str, Any]:
    """Narrative guidance for share of voice — rank leads absolute % when brand is #1."""
    if sov_rank is None:
        return {
            "tone": "neutral",
            "leads_tracked_competitors": False,
            "instruction": (
                "Share of voice ranking is unavailable. Do not invent a rank or claim "
                "competitors capture the majority."
            ),
        }
    rank = int(sov_rank)
    if rank == 1:
        peers = (
            f"among {competitor_count} tracked competitors"
            if competitor_count > 0
            else "among tracked competitors"
        )
        return {
            "tone": "strength",
            "leads_tracked_competitors": True,
            "instruction": (
                f"Frame share of voice as a strength: the brand ranks #1 {peers} "
                f"at {sov_pct:.1f}% SOV. Do NOT treat the absolute percentage as a weakness, "
                f"and do NOT claim competitors capture the majority while the brand leads."
            ),
        }
    return {
        "tone": "gap",
        "leads_tracked_competitors": False,
        "instruction": (
            f"Frame share of voice as a competitive gap: the brand ranks #{rank} "
            f"at {sov_pct:.1f}% SOV"
            + (
                f" among {competitor_count} tracked competitors."
                if competitor_count > 0
                else "."
            )
        ),
    }


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
    prompt_performance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact audit context for the model."""
    inputs = audit.get("audit_inputs") if isinstance(audit.get("audit_inputs"), dict) else {}
    pages = audit.get("pages") if isinstance(audit.get("pages"), list) else []
    del priorities
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
    rating = _score_label(overall)
    return {
        "site_url": str(audit.get("base_url") or "").strip(),
        "brand": str(inputs.get("brand") or "").strip(),
        "industry": str(inputs.get("industry") or "").strip(),
        "pages_sampled": len(pages),
        "crawl_caveats": _crawl_caveats(audit),
        "overall_score": round_report_score(float(overall)),
        "overall_rating": rating,
        "score_label_bands": list(SCORE_LABEL_BANDS),
        "pillar_names": list(PILLAR_TITLES),
        "narrative_rules": {
            "use_overall_rating_verbatim": True,
            "overall_rating_must_be": rating,
            "do_not_invent_score_bands": True,
            "score_band_reminder": (
                f"{format_report_score(float(overall))} maps to '{rating}' "
                "(bands use the nearest-integer display score: "
                "90–100 Excellent, 75–89 Good, 60–74 OK, 40–59 Weak, 0–39 Poor)."
            ),
        },
        "category_scores": cat_rows,
        "weakest_category": weakest[0]["title"] if weakest else "",
        "top_strengths": friendly_strengths,
        "score_cap_notes": [client_friendly_text(str(n)) for n in cap_notes[:3] if str(n).strip()],
        "prompt_performance": prompt_performance or {"available": False},
    }


def build_prompt_performance_digest(audit_dir: Path, brand: str) -> dict[str, Any]:
    """Summarise saved live-probe results for executive-summary evidence."""
    # Prefer canonical product metrics (website-backed SOV + relative rank).
    try:
        from api.geo_services import load_prompt_visibility_metrics

        metrics = load_prompt_visibility_metrics(audit_dir)
    except Exception:
        metrics = None

    raw = _read_json(audit_dir / "prompt_performance_live_probe.json")
    live = raw.get("live_probe", raw) if isinstance(raw, dict) else {}
    rows = live.get("per_prompt") if isinstance(live, dict) else None
    if not isinstance(rows, list) or not rows:
        if isinstance(metrics, dict) and metrics:
            sov_pct = float(metrics.get("sov_pct") or 0)
            sov_rank = metrics.get("sov_rank")
            competitor_count = int(metrics.get("competitor_count") or 0)
            framing = _sov_framing(
                sov_rank=int(sov_rank) if sov_rank is not None else None,
                sov_pct=sov_pct,
                competitor_count=competitor_count,
            )
            return {
                "available": True,
                "prompt_count": int(metrics.get("tested_prompt_count") or metrics.get("prompt_count") or 0),
                "response_count": int(metrics.get("response_count") or 0),
                "brand_visible_response_count": int(
                    metrics.get("visible_response_count") or metrics.get("visible_prompt_count") or 0
                ),
                "cross_platform_visibility_pct": float(metrics.get("visibility_pct") or 0),
                "share_of_voice_pct": sov_pct,
                "sov_rank": sov_rank,
                "sov_performance_score": float(metrics.get("sov_performance_score") or 0),
                "competitor_count": competitor_count,
                "top_competitor_sov_pct": float(metrics.get("top_competitor_sov_pct") or 0),
                "sov_framing": framing,
                "platforms": metrics.get("per_platform") or {},
            }
        return {"available": False}

    platforms: dict[str, Any] = {}
    all_prompt_keys: set[str] = set()
    all_response_count = 0
    visible_response_count = 0
    total_brand_hits = 0.0
    total_competitor_hits = 0.0
    brand_lower = brand.lower().strip()

    for platform in ("gemini", "openai", "google_aio", "claude"):
        responses = 0
        visible = 0
        platform_brand_hits = 0.0
        platform_competitor_hits = 0.0
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            prompt_key = str(row.get("prompt") or index)
            all_prompt_keys.add(prompt_key)
            raw_runs = (row.get("runs") or {}).get(platform) or []
            completed_runs = [
                run for run in raw_runs
                if isinstance(run, dict) and run.get("response") and not run.get("error")
            ]
            response_rows = completed_runs or [{
                "response": row.get(f"{platform}_response"),
                "mention_scores": row.get(f"mention_scores_{platform}") or {},
            }]
            for run in response_rows:
                response = str(run.get("response") or "").strip()
                if not response:
                    continue
                responses += 1
                scores = run.get("mention_scores") or {}
                stored_brand_signal = float(scores.get("brand_signal") or 0) if isinstance(scores, dict) else 0.0
                competitor_detail = scores.get("competitor_detail") or {} if isinstance(scores, dict) else {}
                competitor_hits = (
                    sum(float(value or 0) for value in competitor_detail.values())
                    if isinstance(competitor_detail, dict)
                    else 0.0
                )
                is_visible = stored_brand_signal > 0 or (brand_lower and brand_lower in response.lower())
                brand_signal = stored_brand_signal if stored_brand_signal > 0 else (1.0 if is_visible else 0.0)
                if is_visible:
                    visible += 1
                platform_brand_hits += brand_signal
                platform_competitor_hits += competitor_hits

        if responses:
            denominator = platform_brand_hits + platform_competitor_hits
            platforms[platform] = {
                "responses": responses,
                "brand_visible_responses": visible,
                "visibility_pct": round(100 * visible / responses, 1),
                "share_of_voice_pct": round(100 * platform_brand_hits / denominator, 1) if denominator else 0,
            }
            total_brand_hits += platform_brand_hits
            total_competitor_hits += platform_competitor_hits
            all_response_count += responses
            visible_response_count += visible

    total_hits = total_brand_hits + total_competitor_hits
    fallback_sov = round(100 * total_brand_hits / total_hits, 1) if total_hits else 0.0
    fallback_visibility = (
        round(100 * visible_response_count / all_response_count, 1) if all_response_count else 0.0
    )

    if isinstance(metrics, dict) and metrics:
        sov_pct = float(metrics.get("sov_pct") or fallback_sov)
        sov_rank_raw = metrics.get("sov_rank")
        sov_rank = int(sov_rank_raw) if sov_rank_raw is not None else None
        competitor_count = int(metrics.get("competitor_count") or 0)
        visibility_pct = float(metrics.get("visibility_pct") or fallback_visibility)
        sov_performance_score = float(metrics.get("sov_performance_score") or 0)
        top_competitor_sov = float(metrics.get("top_competitor_sov_pct") or 0)
        response_count = int(metrics.get("response_count") or all_response_count)
        visible_count = int(
            metrics.get("visible_response_count")
            or metrics.get("visible_prompt_count")
            or visible_response_count
        )
        prompt_count = int(
            metrics.get("tested_prompt_count") or metrics.get("prompt_count") or len(all_prompt_keys)
        )
    else:
        # Approximate rank from unfiltered competitor hits when canonical metrics are missing.
        sov_pct = fallback_sov
        visibility_pct = fallback_visibility
        response_count = all_response_count
        visible_count = visible_response_count
        prompt_count = len(all_prompt_keys)
        sov_performance_score = 0.0
        top_competitor_sov = 0.0
        competitor_count = 0
        if total_brand_hits > 0 and total_competitor_hits <= 0:
            sov_rank = 1
            sov_performance_score = 100.0
        elif total_brand_hits <= 0:
            sov_rank = None
        else:
            # Without per-competitor breakdown here, only treat clear majority SOV as a lead signal.
            sov_rank = 1 if sov_pct >= 50.0 else None
            competitor_count = 0

    framing = _sov_framing(
        sov_rank=sov_rank,
        sov_pct=sov_pct,
        competitor_count=competitor_count,
    )
    return {
        "available": bool(platforms) or bool(metrics),
        "prompt_count": prompt_count,
        "brand_visible_prompt_count": visible_count,
        "response_count": response_count,
        "brand_visible_response_count": visible_count,
        "cross_platform_visibility_pct": visibility_pct,
        "share_of_voice_pct": sov_pct,
        "sov_rank": sov_rank,
        "sov_performance_score": sov_performance_score,
        "competitor_count": competitor_count,
        "top_competitor_sov_pct": top_competitor_sov,
        "sov_framing": framing,
        "platforms": platforms,
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


def build_executive_summary_prompt(digest: dict[str, Any]) -> str:
    """Build the Gemini prompt (exported for unit tests)."""
    skill = load_executive_summary_skill()
    payload = json.dumps(digest, ensure_ascii=False, indent=2)
    brand = (digest.get("brand") or "").strip() or "the brand"
    site = (digest.get("site_url") or "").strip() or "the site"
    overall_score = digest.get("overall_score")
    overall_rating = str(digest.get("overall_rating") or "").strip() or _score_label(
        float(overall_score or 0)
    )
    pp = digest.get("prompt_performance") if isinstance(digest.get("prompt_performance"), dict) else {}
    sov_framing = pp.get("sov_framing") if isinstance(pp.get("sov_framing"), dict) else {}
    sov_instruction = str(sov_framing.get("instruction") or "").strip()
    sov_tone = str(sov_framing.get("tone") or "").strip()
    return f"""
You are writing the **Executive summary** section of a GEO (Generative Engine Optimization) audit report for **{brand}** ({site}).

Follow the editorial skill below. Summarise business impact and the audit's weakest areas. When prompt-performance data is available, use at least one concrete visibility or share-of-voice finding. Do not narrate tools, crawlers, or file names unless the crawl caveats require a brief caveat. Do not include recommendations, priorities, or next steps.

## Hard constraints (do not violate)

1. **Score label:** Use the supplied ``overall_rating`` exactly — it is **{overall_rating}** for score **{overall_score}**.
   Canonical bands (do not invent others): 90–100 Excellent · 75–89 Good · **60–74 OK** · 40–59 Weak · 0–39 Poor.
   Example: **60.0 is OK, not Weak.** Never call an OK score Weak or Poor.
2. **Pillars:** Refer to pillars only by these names when needed: AI Visibility, Technical Setup, Content Quality & Structure.
3. **Share of voice:** Follow ``prompt_performance.sov_framing`` when present.
   {f"SOV framing tone is **{sov_tone}**. {sov_instruction}" if sov_instruction else "If SOV rank is #1 (or leads tracked competitors), treat SOV as a strength even when the absolute % is modest; do not claim competitors capture the majority while the brand leads."}
   Always state rank and SOV % accurately when available (e.g. rank #1 at 29.9% SOV).

## Editorial skill (Section 2: Executive summary)

{skill}

---

## Audit data (JSON)

{payload}

---

Return JSON matching the schema: one field ``paragraph_html`` only.
""".strip()


def generate_executive_summary_html(
    digest: dict[str, Any],
    *,
    model: str | None = None,
) -> str:
    """Call Gemini and return sanitized HTML fragment for the report callout."""
    prompt = build_executive_summary_prompt(digest)

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
    """Return cached summary only when schema_version matches the current generator."""
    raw = _read_json(audit_dir / EXECUTIVE_SUMMARY_FILE)
    if not raw or not str(raw.get("paragraph_html") or "").strip():
        return None
    try:
        version = int(raw.get("schema_version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version < EXECUTIVE_SUMMARY_SCHEMA_VERSION:
        return None
    return raw


def save_executive_summary_cache(audit_dir: Path, paragraph_html: str, *, source: str = "gemini") -> dict[str, Any]:
    doc = {
        "schema_version": EXECUTIVE_SUMMARY_SCHEMA_VERSION,
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
    from api import geo_services as geo

    cr = geo.load_create_report()
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
    integrated = geo.load_integrated_scores(audit_dir)
    if integrated.get("overall") is not None:
        overall = float(integrated["overall"])
        for category in categories:
            integrated_score = integrated.get(getattr(category, "key", ""))
            if integrated_score is not None:
                category.score = float(integrated_score)
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
        prompt_performance=build_prompt_performance_digest(
            audit_dir,
            str((audit.get("audit_inputs") or {}).get("brand") or ""),
        ),
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
        prompt_performance=build_prompt_performance_digest(
            audit_dir,
            str((audit.get("audit_inputs") or {}).get("brand") or ""),
        ),
    )
    html = generate_executive_summary_html(digest, model=model)
    try:
        save_executive_summary_cache(audit_dir, html)
    except OSError:
        pass
    return html

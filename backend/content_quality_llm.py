"""
Gemini content-quality evaluator for E-E-A-T and answerability.

Runs asynchronously (Cloud Run Job or local thread). Crawl heuristics remain
the source of truth for schema / formatting / meta proxies; this module overlays
qualitative body-copy judgment on a sampled page set.

Score merge rule (documented for UI/API consumers)
-------------------------------------------------
When a valid ``content_quality_gemini.json`` is present:
  - **Replace** crawl heuristic scores for E-E-A-T criteria and for
    ``original_information_gain`` / ``passage_answerability``.
  - **Prefer** Gemini evidence cards / finding summaries for those pillars;
    fall back to crawl snippets when Gemini has no evidence for a criterion.
  - **Keep** crawl scores for ``content_formatting``, ``schema_entity_markup``,
    and ``brand_visibility_authority`` (structure/schema proxies stay heuristic).
  - Recalculate the Content Quality pillar score from the merged components.
When Gemini is missing or invalid: heuristics-only (no score change).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from report_score import format_report_score

CONTENT_QUALITY_GEMINI_FILE = "content_quality_gemini.json"
CONTENT_QUALITY_SCHEMA_VERSION = 1
DEFAULT_SAMPLE_CAP = 20
MIN_SAMPLE_CAP = 5
MAX_SAMPLE_CAP = 20
# Cap visible text sent to the model per page (chars).
_PAGE_TEXT_CHARS = 12_000
_FETCH_TIMEOUT_S = 20

log = logging.getLogger(__name__)


class CriterionFinding(BaseModel):
    score: float = Field(ge=0, le=100, description="0–100 score for this criterion")
    summary: str = Field(description="1–2 sentences in English explaining the score")
    evidence: list[str] = Field(
        default_factory=list,
        description="Up to 3 short quotes from the page (may be in the source language)",
        max_length=3,
    )


class PageContentQualityResponse(BaseModel):
    source_language: str = Field(
        description="BCP-47-ish language code detected from the page (e.g. en, de, fr, es)"
    )
    experience: CriterionFinding
    expertise: CriterionFinding
    authoritativeness: CriterionFinding
    trust: CriterionFinding
    original_information_gain: CriterionFinding
    passage_answerability: CriterionFinding
    schema_notes: str = Field(
        default="",
        description="Optional short English note on schema/entity clarity from the body copy only",
    )


def sample_cap() -> int:
    raw = (os.getenv("CONTENT_QUALITY_SAMPLE_CAP") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_SAMPLE_CAP
    except ValueError:
        value = DEFAULT_SAMPLE_CAP
    return max(MIN_SAMPLE_CAP, min(MAX_SAMPLE_CAP, value))


def competitor_sample_cap() -> int:
    """Per-competitor page sample size (default: same as brand, 20).

    Override with ``CONTENT_QUALITY_COMPETITOR_SAMPLE_CAP`` (clamped 5–20) when
    multi-competitor cost needs a lower budget. Brand still uses ``sample_cap()``.
    """
    raw = (os.getenv("CONTENT_QUALITY_COMPETITOR_SAMPLE_CAP") or "").strip()
    if not raw:
        return sample_cap()
    try:
        value = int(raw)
    except ValueError:
        return sample_cap()
    return max(MIN_SAMPLE_CAP, min(MAX_SAMPLE_CAP, value))


def _file_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime if path.is_file() else None
    except OSError:
        return None


def _path_depth(url: str) -> int:
    path = urlparse(url).path or "/"
    parts = [p for p in path.split("/") if p]
    return len(parts)


def _is_homepage(url: str, base_url: str) -> bool:
    try:
        u = urlparse(url)
        b = urlparse(base_url)
    except Exception:
        return False
    if (u.netloc or "").lower().removeprefix("www.") != (b.netloc or "").lower().removeprefix("www."):
        return False
    path = (u.path or "/").rstrip("/") or "/"
    return path == "/"


def _ga4_traffic_ranks(audit_dir: Path) -> dict[str, int]:
    """Lower rank = more important. Empty when GA4 top pages are unavailable."""
    path = audit_dir / "ga4_top_pages.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    pages = raw.get("pages") or raw.get("top_pages") if isinstance(raw, dict) else None
    if not isinstance(pages, list):
        return {}
    ranks: dict[str, int] = {}
    for idx, row in enumerate(pages):
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or row.get("pagePath") or row.get("page") or "").strip()
        if not url:
            continue
        # Normalise bare paths against later full-URL matching in the sampler.
        ranks[url] = idx
        if url.startswith("/") and "://" not in url:
            ranks[url.rstrip("/") or "/"] = idx
        else:
            parsed = urlparse(url)
            ranks[(parsed.path or "/").rstrip("/") or "/"] = idx
    return ranks


def _traffic_rank_for(url: str, ranks: dict[str, int]) -> int | None:
    if url in ranks:
        return ranks[url]
    parsed = urlparse(url)
    path = (parsed.path or "/").rstrip("/") or "/"
    if path in ranks:
        return ranks[path]
    if url.rstrip("/") in ranks:
        return ranks[url.rstrip("/")]
    return None


def _page_has_url(page: dict[str, Any]) -> bool:
    return bool(str(page.get("final_url") or page.get("url") or "").strip())


def _homepage_stub(base_url: str) -> dict[str, Any]:
    """Minimal page row so Gemini can re-fetch when the crawl left no usable pages."""
    home = base_url.rstrip("/") + "/"
    return {
        "url": home,
        "final_url": home,
        "http_status": 0,
        "page_title": "",
        "content_signals": {},
    }


def _candidate_pages_for_sampling(audit: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Prefer HTTP 200 crawl rows; fall back to any URL-bearing rows.

    Competitor crawls often record homepage/sitemap URLs with ``http_status`` null
    (timeout) or 403/404. Those still carry sampleable URLs for a live Gemini fetch.
    When the pages list is empty but ``base_url`` exists, synthesize the homepage.
    """
    raw_pages = [page for page in (audit.get("pages") or []) if isinstance(page, dict)]
    ok_pages = [
        page for page in raw_pages if int(page.get("http_status") or 0) == 200 and _page_has_url(page)
    ]
    if ok_pages:
        return ok_pages
    url_pages = [page for page in raw_pages if _page_has_url(page)]
    if url_pages:
        return url_pages
    base = str(audit.get("base_url") or "").strip()
    if base:
        return [_homepage_stub(base)]
    return []


def sample_pages_for_content_quality(
    audit: dict[str, Any],
    *,
    audit_dir: Path | None = None,
    cap: int | None = None,
) -> list[dict[str, Any]]:
    """
    Select homepage + top N pages by importance proxies.

    Priority: homepage, GA4 traffic (when present), editorial content, shallow depth,
    meaningful sentence count. Cap defaults to 20 (env ``CONTENT_QUALITY_SAMPLE_CAP``).

    Prefer successfully crawled (HTTP 200) pages. When none exist — common for thin
    competitor crawls — fall back to any URL-bearing crawl rows, then ``base_url`` homepage.
    """
    limit = cap if cap is not None else sample_cap()
    base = str(audit.get("base_url") or "").strip()
    pages = _candidate_pages_for_sampling(audit)
    if not pages:
        return []

    ranks = _ga4_traffic_ranks(audit_dir) if audit_dir is not None else {}

    scored: list[tuple[float, dict[str, Any]]] = []
    for page in pages:
        url = str(page.get("final_url") or page.get("url") or "").strip()
        if not url:
            continue
        signals = page.get("content_signals") if isinstance(page.get("content_signals"), dict) else {}
        editorial = 1.0 if signals.get("has_editorial_content") else 0.0
        words = float(signals.get("visible_words") or 0)
        meaningful = float(signals.get("meaningful_sentence_n") or 0)
        depth = _path_depth(url)
        home = 1.0 if base and _is_homepage(url, base) else 0.0
        traffic = _traffic_rank_for(url, ranks)
        # Lower GA4 rank → higher score; missing traffic → mid penalty.
        traffic_score = (40.0 - min(40.0, float(traffic))) if traffic is not None else 0.0
        # Prefer shallow editorial pages and homepage; diversify depth slightly.
        priority = (
            200.0 * home
            + traffic_score
            + 35.0 * editorial
            + min(25.0, meaningful * 3.0)
            + min(15.0, words / 200.0)
            + max(0.0, 12.0 - depth * 1.5)
        )
        scored.append((priority, page))

    scored.sort(key=lambda item: item[0], reverse=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, page in scored:
        url = str(page.get("final_url") or page.get("url") or "").strip()
        key = url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        selected.append(page)
        if len(selected) >= limit:
            break
    return selected


# Soft-fail reasons: empty crawl / nothing to sample — not Gemini API failures.
CONTENT_QUALITY_SOFT_FAIL_REASONS = frozenset({"no_pages"})


def is_content_quality_soft_fail(error: str | None) -> bool:
    """True when the job should exit 0 (skipped) rather than hard-fail."""
    if not error:
        return False
    reason = str(error).strip()
    lowered = reason.lower()
    if lowered in CONTENT_QUALITY_SOFT_FAIL_REASONS or lowered == "no_page_results":
        return True
    # Fetch-only failures look like ``https://…: no text (timeout)``.
    parts = [part.strip() for part in reason.split(";") if part.strip()]
    if parts and all("no text" in part.lower() for part in parts):
        return True
    return False


def _visible_text_from_html(html: str, *, max_chars: int = _PAGE_TEXT_CHARS) -> str:
    try:
        from crawl_site_loader import rough_visible_text  # type: ignore

        return rough_visible_text(html, max_chars=max_chars)
    except Exception:
        pass
    try:
        # Prefer the crawl module's helper when available under its file name.
        import importlib.util

        from geo_app_env import BACKEND_ROOT

        path = BACKEND_ROOT / "crawl-site.py"
        spec = importlib.util.spec_from_file_location("crawl_site_cq", path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            text = mod._rough_visible_text(html, max_chars=max_chars)
            return str(text or "")
    except Exception:
        pass
    # Minimal fallback: strip tags.
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _fetch_page_text(url: str) -> tuple[str, bool, str | None]:
    """Return (text, fetch_ok, error)."""
    try:
        import importlib.util

        from geo_app_env import BACKEND_ROOT

        path = BACKEND_ROOT / "crawl-site.py"
        spec = importlib.util.spec_from_file_location("crawl_site_cq_fetch", path)
        if not spec or not spec.loader:
            raise RuntimeError("crawl-site unavailable")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result = mod._request_urllib(url)
        status = int(result.status or 0)
        if status != 200 or not result.body:
            return "", False, result.error or f"HTTP {status}"
        html = result.body.decode("utf-8", errors="replace")
        text = _visible_text_from_html(html)
        if not text.strip():
            return "", False, "empty body text"
        return text, True, None
    except Exception as exc:
        return "", False, str(exc)


def _fallback_text_from_page(page: dict[str, Any]) -> str:
    signals = page.get("content_signals") if isinstance(page.get("content_signals"), dict) else {}
    parts: list[str] = []
    excerpt = str(signals.get("representative_excerpt") or "").strip()
    if excerpt:
        parts.append(excerpt)
    evidence = signals.get("eeat_evidence") if isinstance(signals.get("eeat_evidence"), dict) else {}
    for criterion in ("experience", "expertise", "authoritativeness", "trust"):
        for snippet in evidence.get(criterion) or []:
            s = str(snippet).strip()
            if s and s not in parts:
                parts.append(s)
    for snippet in signals.get("originality_evidence") or []:
        s = str(snippet).strip()
        if s and s not in parts:
            parts.append(s)
    for snippet in signals.get("formatting_evidence") or []:
        s = str(snippet).strip()
        if s and s not in parts:
            parts.append(s)
    title = str(page.get("page_title") or "").strip()
    if title:
        parts.insert(0, f"Title: {title}")
    return "\n".join(parts)[:_PAGE_TEXT_CHARS]


def _generate_page_assessment(
    *,
    url: str,
    title: str,
    text: str,
    brand_name: str,
    site_url: str,
) -> PageContentQualityResponse:
    from insights_llm import _generate_structured

    brand = (brand_name or "the brand").strip() or "the brand"
    site = (site_url or "").strip() or "the site"
    prompt = f"""
You are a GEO (Generative Engine Optimization) content-quality rater.

Brand: **{brand}** (site: **{site}**)
Page URL: {url}
Page title: {title or "(untitled)"}

TASK
1. Detect the primary language of the page text below.
2. Analyse the page **in that source language** (do not translate the body to English first).
3. Score E-E-A-T and answerability from the body copy.
4. Write all ``summary`` / ``schema_notes`` fields in **UK English** for the client report.
5. Keep ``evidence`` quotes in the **source language** (short, verbatim).

SCORING (0–100 each)
- experience: first-hand use, testing, case studies, process, specific outcomes
- expertise: credentials, depth, methodology, sourced claims, correct terminology
- authoritativeness: recognition, accreditation, partnerships, standing in the field
- trust: policies, contactability, disclosures, review dates, sourcing transparency
- original_information_gain: novel first-party data, benchmarks, proprietary frameworks
- passage_answerability: self-contained passages that could answer a question when quoted alone

Rules:
- Judge only from the provided text (and obvious gaps).
- If the text is thin or mostly navigation/product grid, scores should be low with clear summaries.
- Prefer specific evidence over generic praise.
- ``source_language`` must be a short code like en, de, fr, es, it, nl, pt, ja, zh.

PAGE TEXT:
{text}
""".strip()
    return _generate_structured(prompt, PageContentQualityResponse)  # type: ignore[return-value]


def _criterion_avg(pages: list[dict[str, Any]], key: str) -> float | None:
    values: list[float] = []
    for page in pages:
        scores = page.get("scores") if isinstance(page.get("scores"), dict) else {}
        try:
            values.append(float(scores[key]))
        except (KeyError, TypeError, ValueError):
            continue
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def load_cached_content_quality_gemini(audit_dir: Path) -> dict[str, Any] | None:
    cache = audit_dir / CONTENT_QUALITY_GEMINI_FILE
    summary = audit_dir / "audit_summary.json"
    if not cache.is_file() or not summary.is_file():
        return None
    try:
        raw = json.loads(cache.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if int(raw.get("schema_version") or 0) < CONTENT_QUALITY_SCHEMA_VERSION:
        return None
    src_mtime = _file_mtime(summary)
    if src_mtime is None or float(raw.get("source_mtime") or 0) != src_mtime:
        return None
    pages = raw.get("pages")
    if not isinstance(pages, list) or not pages:
        return None
    return raw


def save_content_quality_gemini(audit_dir: Path, payload: dict[str, Any]) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True)
    out = audit_dir / CONTENT_QUALITY_GEMINI_FILE
    summary = audit_dir / "audit_summary.json"
    payload = {
        **payload,
        "schema_version": CONTENT_QUALITY_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_mtime": _file_mtime(summary),
        "merge_rule": "replace_eeat_and_answerability",
    }
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(out)
    return out


def generate_content_quality_gemini(
    audit_dir: Path,
    *,
    brand_name: str = "",
    site_url: str = "",
    force: bool = False,
    cap: int | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Sample pages, evaluate with Gemini, persist ``content_quality_gemini.json``.

    Returns ``(payload, error)``. When Gemini/API is unavailable, returns ``(None, err)``
    without raising — callers keep crawl heuristics.
    """
    audit_dir = audit_dir.resolve()
    if not force:
        cached = load_cached_content_quality_gemini(audit_dir)
        if cached:
            return cached, None

    summary_path = audit_dir / "audit_summary.json"
    if not summary_path.is_file():
        return None, "no_audit_summary"
    try:
        audit = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"audit_summary_unreadable: {exc}"
    if not isinstance(audit, dict):
        return None, "audit_summary_invalid"

    if not brand_name or not site_url:
        onboarding_path = audit_dir / "onboarding_context.json"
        try:
            onboarding = json.loads(onboarding_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            onboarding = {}
        if isinstance(onboarding, dict):
            brand_name = brand_name or str(
                onboarding.get("brand_name_used") or onboarding.get("brand_name") or ""
            ).strip()
            site_url = site_url or str(
                onboarding.get("website_url") or onboarding.get("site_url") or ""
            ).strip()
    brand_name = brand_name or "the brand"
    site_url = site_url or str(audit.get("base_url") or "")

    selected = sample_pages_for_content_quality(audit, audit_dir=audit_dir, cap=cap)
    if not selected:
        return None, "no_pages"

    page_results: list[dict[str, Any]] = []
    errors: list[str] = []
    for page in selected:
        url = str(page.get("final_url") or page.get("url") or "").strip()
        title = str(page.get("page_title") or url or "Sampled page").strip()
        text, fetch_ok, fetch_err = _fetch_page_text(url)
        if not text.strip():
            text = _fallback_text_from_page(page)
            fetch_ok = False
            if not text.strip():
                errors.append(f"{url}: no text ({fetch_err or 'empty'})")
                continue
        try:
            assessment = _generate_page_assessment(
                url=url,
                title=title,
                text=text,
                brand_name=brand_name,
                site_url=site_url,
            )
        except Exception as exc:
            log.exception("Gemini content-quality failed for %s", url)
            errors.append(f"{url}: {exc}")
            continue

        page_results.append(
            {
                "url": url,
                "title": title,
                "source_language": assessment.source_language,
                "fetch_ok": fetch_ok,
                "fetch_error": fetch_err if not fetch_ok else None,
                "scores": {
                    "experience": round(float(assessment.experience.score), 1),
                    "expertise": round(float(assessment.expertise.score), 1),
                    "authoritativeness": round(float(assessment.authoritativeness.score), 1),
                    "trust": round(float(assessment.trust.score), 1),
                    "original_information_gain": round(
                        float(assessment.original_information_gain.score), 1
                    ),
                    "passage_answerability": round(
                        float(assessment.passage_answerability.score), 1
                    ),
                },
                "findings": {
                    "experience": assessment.experience.model_dump(),
                    "expertise": assessment.expertise.model_dump(),
                    "authoritativeness": assessment.authoritativeness.model_dump(),
                    "trust": assessment.trust.model_dump(),
                    "original_information_gain": assessment.original_information_gain.model_dump(),
                    "passage_answerability": assessment.passage_answerability.model_dump(),
                },
                "schema_notes": assessment.schema_notes,
            }
        )

    if not page_results:
        return None, "; ".join(errors) or "no_page_results"

    eeat_keys = ("experience", "expertise", "authoritativeness", "trust")
    eeat_vals = [_criterion_avg(page_results, key) for key in eeat_keys]
    eeat_present = [v for v in eeat_vals if v is not None]
    languages = sorted(
        {
            str(p.get("source_language") or "").strip().lower()
            for p in page_results
            if str(p.get("source_language") or "").strip()
        }
    )
    aggregate = {
        "experience": eeat_vals[0],
        "expertise": eeat_vals[1],
        "authoritativeness": eeat_vals[2],
        "trust": eeat_vals[3],
        "eeat": round(sum(eeat_present) / len(eeat_present), 1) if eeat_present else None,
        "original_information_gain": _criterion_avg(page_results, "original_information_gain"),
        "passage_answerability": _criterion_avg(page_results, "passage_answerability"),
        "languages_seen": languages,
        "finding_summary": (
            f"Gemini assessed {len(page_results)} sampled page(s)"
            + (f" (languages: {', '.join(languages)})" if languages else "")
            + ". E-E-A-T and answerability scores replace crawl heuristics when present."
        ),
        "pages_failed": len(errors),
    }
    payload = {
        "sample_cap": cap if cap is not None else sample_cap(),
        "pages_analyzed": len(page_results),
        "aggregate": aggregate,
        "pages": page_results,
        "errors": errors[:20],
    }
    save_content_quality_gemini(audit_dir, payload)
    return payload, None


def load_or_generate_content_quality_gemini(
    audit_dir: Path,
    *,
    brand_name: str = "",
    site_url: str = "",
    force: bool = False,
    cap: int | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    return generate_content_quality_gemini(
        audit_dir,
        brand_name=brand_name,
        site_url=site_url,
        force=force,
        cap=cap,
    )


def apply_gemini_to_content_components(
    components: list[dict[str, Any]],
    gemini: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], float | None]:
    """Overlay Gemini E-E-A-T / answerability onto comparison content components.

    Same replace/keep rules as ``merge_gemini_into_content_quality``. Returns
    ``(components, weighted_pillar_score_or_None)``.
    """
    if not components:
        return [], None
    if not gemini or not isinstance(gemini, dict):
        return [dict(c) for c in components if isinstance(c, dict)], None

    aggregate = gemini.get("aggregate") if isinstance(gemini.get("aggregate"), dict) else {}
    out: list[dict[str, Any]] = []
    for component in components:
        if not isinstance(component, dict):
            continue
        component = dict(component)
        key = str(component.get("key") or "")
        if key == "eeat" and aggregate.get("eeat") is not None:
            component["score"] = round(float(aggregate["eeat"]), 1)
            component["finding_summary"] = str(
                aggregate.get("finding_summary")
                or (
                    f"Gemini E-E-A-T average "
                    f"{format_report_score(float(component['score']))}/100 across sampled pages."
                )
            )
            component["scoring_source"] = "gemini"
            examples = _gemini_evidence_for_criterion(gemini, "experience")
            if examples:
                component["evidence_example"] = examples[0]["snippet"]
                component["verified_site"] = True
        elif key == "structure_answerability":
            # Grouped Overview row: replace originality + answerability (30pts)
            # while keeping crawl formatting as the remaining 10pts proxy.
            oi = aggregate.get("original_information_gain")
            pa = aggregate.get("passage_answerability")
            gemini_vals = [float(v) for v in (oi, pa) if v is not None]
            if gemini_vals:
                crawl_score = float(component.get("score") or 0.0)
                gemini_pair = sum(gemini_vals) / len(gemini_vals)
                component["score"] = round((30.0 * gemini_pair + 10.0 * crawl_score) / 40.0, 1)
                summary_bits = [
                    s
                    for s in (
                        _gemini_finding_summary(gemini, "original_information_gain"),
                        _gemini_finding_summary(gemini, "passage_answerability"),
                    )
                    if s
                ]
                if summary_bits:
                    component["finding_summary"] = " ".join(summary_bits)
                examples = _gemini_evidence_for_criterion(
                    gemini, "passage_answerability"
                ) or _gemini_evidence_for_criterion(gemini, "original_information_gain")
                if examples:
                    component["evidence_example"] = examples[0]["snippet"]
                component["scoring_source"] = "gemini"
                component["verified_site"] = True
        elif key in {"original_information_gain", "passage_answerability"} and aggregate.get(
            key
        ) is not None:
            component["score"] = round(float(aggregate[key]), 1)
            summary = _gemini_finding_summary(gemini, key)
            if summary:
                component["finding_summary"] = summary
            examples = _gemini_evidence_for_criterion(gemini, key)
            if examples:
                component["evidence_example"] = examples[0]["snippet"]
            component["scoring_source"] = "gemini"
            component["verified_site"] = True
        else:
            component.setdefault("scoring_source", "crawl_heuristic")
        out.append(component)

    total_w = sum(float(c.get("weight_pct") or 0.0) for c in out)
    if total_w <= 0:
        return out, None
    score = round(
        sum(float(c.get("score") or 0.0) * float(c.get("weight_pct") or 0.0) for c in out)
        / total_w,
        1,
    )
    return out, score


# ---------------------------------------------------------------------------
# Merge helpers (pure; used by geo_services + tests)
# ---------------------------------------------------------------------------

_EEAT_NAME_TO_KEY = {
    "experience": "experience",
    "expertise": "expertise",
    "authoritativeness": "authoritativeness",
    "trust": "trust",
}


def _eeat_key_from_name(name: str) -> str | None:
    lowered = (name or "").strip().lower()
    for key in _EEAT_NAME_TO_KEY:
        if key in lowered:
            return key
    return None


def _gemini_evidence_for_criterion(
    gemini: dict[str, Any],
    criterion: str,
    *,
    limit: int = 3,
) -> list[dict[str, str]]:
    examples: list[dict[str, str]] = []
    for page in gemini.get("pages") or []:
        if not isinstance(page, dict):
            continue
        findings = page.get("findings") if isinstance(page.get("findings"), dict) else {}
        row = findings.get(criterion) if isinstance(findings.get(criterion), dict) else {}
        for snippet in row.get("evidence") or []:
            text = str(snippet).strip()
            if not text:
                continue
            examples.append(
                {
                    "url": str(page.get("url") or ""),
                    "title": str(page.get("title") or page.get("url") or "Sampled page"),
                    "snippet": text[:420],
                }
            )
            if len(examples) >= limit:
                return examples
    return examples


def _gemini_finding_summary(gemini: dict[str, Any], criterion: str) -> str:
    summaries: list[str] = []
    for page in gemini.get("pages") or []:
        if not isinstance(page, dict):
            continue
        findings = page.get("findings") if isinstance(page.get("findings"), dict) else {}
        row = findings.get(criterion) if isinstance(findings.get(criterion), dict) else {}
        summary = str(row.get("summary") or "").strip()
        if summary:
            lang = str(page.get("source_language") or "").strip()
            prefix = f"[{lang}] " if lang else ""
            summaries.append(f"{prefix}{summary}")
        if len(summaries) >= 2:
            break
    return " ".join(summaries)


def merge_gemini_into_content_quality(
    details: dict[str, Any],
    gemini: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Overlay Gemini scores/evidence onto crawl-built content quality details.

    See module docstring for the replace vs keep rules.
    """
    if not details or not isinstance(details, dict):
        return details
    if not gemini or not isinstance(gemini, dict):
        details = dict(details)
        details["gemini_overlay"] = {"available": False, "status": "missing"}
        return details

    aggregate = gemini.get("aggregate") if isinstance(gemini.get("aggregate"), dict) else {}
    merged = dict(details)
    merged["gemini_overlay"] = {
        "available": True,
        "status": "applied",
        "merge_rule": str(gemini.get("merge_rule") or "replace_eeat_and_answerability"),
        "pages_analyzed": int(gemini.get("pages_analyzed") or 0),
        "languages_seen": list(aggregate.get("languages_seen") or []),
        "finding_summary": str(aggregate.get("finding_summary") or ""),
        "schema_version": int(gemini.get("schema_version") or CONTENT_QUALITY_SCHEMA_VERSION),
    }

    # --- E-E-A-T rows ---
    eeat_rows: list[dict[str, Any]] = []
    for row in details.get("eeat") or []:
        if not isinstance(row, dict):
            continue
        row = dict(row)
        key = _eeat_key_from_name(str(row.get("name") or ""))
        if key and aggregate.get(key) is not None:
            try:
                row["score"] = round(float(aggregate[key]), 1)
            except (TypeError, ValueError):
                pass
            gemini_examples = _gemini_evidence_for_criterion(gemini, key)
            if gemini_examples:
                row["evidence"] = gemini_examples
                row["evidence_note"] = (
                    f"Gemini qualitative evidence from {int(gemini.get('pages_analyzed') or 0)} "
                    f"sampled page(s); source-language quotes retained."
                )
            summary = _gemini_finding_summary(gemini, key)
            if summary:
                row["how_scored"] = (
                    "Gemini assessed body copy in the page's source language; "
                    "findings are summarised in English. Crawl schema/meta heuristics are unchanged."
                )
                # Prefer Gemini narrative when present without wiping the criterion meaning.
                row["evidence_note"] = summary
            row["scoring_source"] = "gemini"
        else:
            row["scoring_source"] = "crawl_heuristic"
        eeat_rows.append(row)
    merged["eeat"] = eeat_rows

    # --- Structure / answerability (replace originality + answerability only) ---
    structure_rows: list[dict[str, Any]] = []
    for row in details.get("structure_answerability") or []:
        if not isinstance(row, dict):
            continue
        row = dict(row)
        key = str(row.get("key") or "")
        if key in {"original_information_gain", "passage_answerability"} and aggregate.get(key) is not None:
            try:
                row["score"] = round(float(aggregate[key]), 1)
            except (TypeError, ValueError):
                pass
            gemini_examples = _gemini_evidence_for_criterion(gemini, key)
            if gemini_examples:
                row["examples"] = gemini_examples
            summary = _gemini_finding_summary(gemini, key)
            if summary:
                row["empty_message"] = summary
            row["scoring_source"] = "gemini"
        else:
            row["scoring_source"] = "crawl_heuristic"
        structure_rows.append(row)
    merged["structure_answerability"] = structure_rows

    # --- Components ---
    components: list[dict[str, Any]] = []
    for component in details.get("components") or []:
        if not isinstance(component, dict):
            continue
        component = dict(component)
        key = str(component.get("key") or "")
        if key == "eeat" and aggregate.get("eeat") is not None:
            component["score"] = round(float(aggregate["eeat"]), 1)
            component["finding_summary"] = str(
                aggregate.get("finding_summary")
                or f"Gemini E-E-A-T average {format_report_score(float(component['score']))}/100 across sampled pages."
            )
            component["scoring_source"] = "gemini"
            # Prefer Gemini site examples from experience evidence.
            examples = _gemini_evidence_for_criterion(gemini, "experience")
            if examples:
                component["site_examples"] = [
                    {
                        "url": ex["url"],
                        "title": ex["title"],
                        "excerpt": ex["snippet"],
                        "context": "Gemini E-E-A-T evidence from this page.",
                    }
                    for ex in examples
                ]
                component["evidence_example"] = examples[0]["snippet"]
        elif key in {"original_information_gain", "passage_answerability"} and aggregate.get(key) is not None:
            component["score"] = round(float(aggregate[key]), 1)
            summary = _gemini_finding_summary(gemini, key)
            if summary:
                component["finding_summary"] = summary
            examples = _gemini_evidence_for_criterion(gemini, key)
            if examples:
                component["site_examples"] = [
                    {
                        "url": ex["url"],
                        "title": ex["title"],
                        "excerpt": ex["snippet"],
                        "context": "Gemini qualitative evidence.",
                    }
                    for ex in examples
                ]
                component["evidence_example"] = examples[0]["snippet"]
            component["scoring_source"] = "gemini"
        else:
            component["scoring_source"] = "crawl_heuristic"
        components.append(component)

    if components:
        content_score = sum(
            float(component.get("score") or 0) * float(component.get("weight_pct") or 0) / 100.0
            for component in components
        )
        merged["components"] = components
        merged["score"] = round(content_score, 1)

    return merged

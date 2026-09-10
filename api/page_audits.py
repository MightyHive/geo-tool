"""Isolated single-page audits nested under a master (primary) audit."""

from __future__ import annotations

import copy
import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api import geo_services as geo
from api.workshop_dashboards import _audit_dir_or_404, _require_write_access

log = logging.getLogger(__name__)

router = APIRouter(tags=["page-audits"])

PAGE_AUDITS_DIR = "page_audits"
INDEX_FILE = "index.json"
PAGE_AUDIT_FILE = "page_audit.json"
PAGE_SNAPSHOT_FILE = "page_snapshot.json"
PAGE_BREAKDOWN_FILE = "page_breakdown.json"
GEMINI_FILE = "content_quality_gemini.json"
PAGE_PROMPTS_FILE = "page_prompts.json"
PAGE_PROBE_FILE = "page_probe.json"
PAGE_PROBE_PENDING_FILE = "page_probe_pending.json"
PAGE_PROBE_PROGRESS_FILE = "page_probe_progress.jsonl"
MAX_PAGE_PROMPTS = 25
DEFAULT_PAGE_PROMPTS = 25

# Derived artifacts wiped when a page is audited again, so a re-run rebuilds everything
# instead of inheriting a stale snapshot, prompt set, or half-finished probe.
PAGE_RERUN_STALE_FILES = (
    "audit_summary.json",
    GEMINI_FILE,
    PAGE_BREAKDOWN_FILE,
    PAGE_PROBE_FILE,
    PAGE_PROBE_PENDING_FILE,
    PAGE_PROBE_PROGRESS_FILE,
    PAGE_PROMPTS_FILE,
    PAGE_SNAPSHOT_FILE,
)

INHERITED_KEYS = (
    "ai_crawler_report",
    "brand_entity_visibility",
    "brand_visibility_authority",
    "platform_readiness",
    "robots_txt",
    "llms_txt",
    "tls",
)

PAGE_SPECIFIC_KEYS = (
    "ai_citability",
    "ai_search_success",
    "query_coverage_footprint",
    "indexability_crawl_health",
    "ssr_html_completeness",
    "performance_page_experience",
    "discovery_signals",
    "eeat",
    "original_information_gain",
    "passage_answerability",
    "json_ld",
    "content_formatting",
    "schema_entity_markup",
    "source_transparency_governance",
    "page_citations",
)

_INDEX_LOCK = threading.Lock()
_JOBS_LOCK = threading.Lock()
_RUNNING_JOBS: set[str] = set()


class CreatePageAuditBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class RunPagePromptsBody(BaseModel):
    prompts: list[str] = Field(min_length=1, max_length=MAX_PAGE_PROMPTS)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def page_audits_root(audit_dir: Path) -> Path:
    return audit_dir / PAGE_AUDITS_DIR


def index_path(audit_dir: Path) -> Path:
    return page_audits_root(audit_dir) / INDEX_FILE


def normalize_http_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError("URL is required")
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a valid http(s) URL")
    path = parsed.path or "/"
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, "")
    )


def hostname_key(value: str) -> str:
    return geo._website_host(value)


def urls_are_same_site(page_url: str, base_url: str) -> bool:
    page_host = hostname_key(page_url)
    base_host = hostname_key(base_url)
    if not page_host or not base_host:
        return False
    if page_host == base_host:
        return True
    return page_host.endswith(f".{base_host}") or base_host.endswith(f".{page_host}")


def normalize_citation_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = (parsed.path or "/").rstrip("/") or "/"
    return f"{host}{path}"


def page_id_for_url(url: str) -> str:
    crawl = geo.load_crawl_site()
    return str(crawl.slug_from_url(normalize_http_url(url)))


def citation_presence_score(citation_count: int) -> float:
    if citation_count <= 0:
        return 0.0
    return min(100.0, 55.0 + 15.0 * citation_count)


def overall_score(ai_visibility: float, technical_setup: float, content_quality: float) -> float:
    return round(0.40 * ai_visibility + 0.30 * technical_setup + 0.30 * content_quality, 1)


def component_scope_for(key: str) -> str:
    if key in INHERITED_KEYS:
        return "inherited"
    return "page_specific"


def _stamp_component_scope(payload: Any, page_url: str) -> Any:
    target = normalize_citation_url(page_url) if page_url else ""

    def keep_example(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        url = str(item.get("url") or "")
        if not url or not target:
            return True
        return normalize_citation_url(url) == target

    if isinstance(payload, list):
        return [_stamp_component_scope(item, page_url) for item in payload]
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    key = str(out.get("key") or "")
    if key:
        out["scope"] = component_scope_for(key)
    if isinstance(out.get("components"), list):
        out["components"] = [_stamp_component_scope(item, page_url) for item in out["components"]]
    if isinstance(out.get("site_examples"), list):
        out["site_examples"] = [item for item in out["site_examples"] if keep_example(item)]
    if isinstance(out.get("evidence"), list):
        out["evidence"] = [item for item in out["evidence"] if keep_example(item) or not isinstance(item, dict)]
    if isinstance(out.get("examples"), list):
        out["examples"] = [item for item in out["examples"] if keep_example(item)]
    if isinstance(out.get("eeat"), list):
        out["eeat"] = [_stamp_component_scope(item, page_url) for item in out["eeat"]]
    if isinstance(out.get("structure_answerability"), list):
        out["structure_answerability"] = [
            _stamp_component_scope(item, page_url) for item in out["structure_answerability"]
        ]
    if isinstance(out.get("schema_entity"), dict):
        out["schema_entity"] = _stamp_component_scope(out["schema_entity"], page_url)
        out["schema_entity"]["scope"] = "page_specific"
    if isinstance(out.get("brand_visibility_authority"), dict):
        out["brand_visibility_authority"] = _stamp_component_scope(
            out["brand_visibility_authority"], page_url
        )
        out["brand_visibility_authority"]["scope"] = "inherited"
    if isinstance(out.get("details"), dict):
        out["details"] = {
            name: _stamp_component_scope(block, page_url) for name, block in out["details"].items()
        }
    if isinstance(out.get("content_quality_details"), dict):
        out["content_quality_details"] = _stamp_component_scope(
            out["content_quality_details"], page_url
        )
    if isinstance(out.get("crawler_access"), dict):
        out["crawler_access"] = dict(out["crawler_access"])
        out["crawler_access"]["scope"] = "inherited"
    if isinstance(out.get("platform_readiness"), list):
        stamped_rows = []
        for row in out["platform_readiness"]:
            if isinstance(row, dict):
                next_row = dict(row)
                next_row["scope"] = "inherited"
                stamped_rows.append(next_row)
            else:
                stamped_rows.append(row)
        out["platform_readiness"] = stamped_rows
    return out


def build_page_breakdown(
    page_dir: Path,
    *,
    scores: dict[str, Any],
    citations: list[dict[str, Any]],
    page_url: str,
) -> dict[str, Any]:
    details = geo.load_category_score_details(page_dir)
    technical_display = geo.load_technical_display_data(page_dir)
    content_quality_details = geo.load_content_quality_details(page_dir)
    readiness_detail = details.get("ai_visibility") if isinstance(details.get("ai_visibility"), dict) else {}
    foundation_detail = details.get("technical_setup") if isinstance(details.get("technical_setup"), dict) else {}
    content_detail = details.get("content_structure") if isinstance(details.get("content_structure"), dict) else {}

    citability = float((scores.get("details") or {}).get("page_citability") or 0.0)
    citation_presence = float((scores.get("details") or {}).get("citation_presence") or 0.0)
    citation_count = int((scores.get("details") or {}).get("citation_count") or len(citations))
    ai_visibility = float(scores.get("ai_visibility") or 0.0)
    technical = float(scores.get("technical_setup") or 0.0)
    content = content_quality_details.get("score")
    if content is None:
        content = scores.get("content_quality")
    content = float(content or 0.0)

    ai_components = list(readiness_detail.get("components") or [])
    citability_row = next(
        (row for row in ai_components if isinstance(row, dict) and row.get("key") == "ai_citability"),
        None,
    )
    if isinstance(citability_row, dict):
        citability_row["score"] = round(citability, 1)
        if isinstance(scores.get("prompt_metrics"), dict):
            citability_row["weight_pct"] = 0.0
    ai_components.insert(
        0,
        {
            "key": "page_citations",
            "title": "Citations of this page",
            "score": round(citation_presence, 1),
            "weight_pct": 0.0,
            "detail": "Whether master-audit or page-specific prompt probes cited this URL.",
            "finding_summary": (
                f"This URL appeared in {citation_count} matching citation(s) from master and/or page probes."
                if citation_count
                else "This URL was not cited in master or page-specific prompt probes."
            ),
            "evidence_example": "",
            "strengths": (
                [f"Cited {citation_count} time(s) in prompt probes."] if citation_count else []
            ),
            "improvements": (
                []
                if citation_count
                else ["Improve extractability and entity markup so answer engines can cite this page."]
            ),
            "report_section": "citations",
            "site_examples": [],
            "scope": "page_specific",
        },
    )
    prompt_metrics = (
        scores.get("prompt_metrics") if isinstance(scores.get("prompt_metrics"), dict) else None
    )
    if prompt_metrics:
        ai_components.insert(
            0,
            {
                "key": "share_of_voice",
                "title": "Share of voice performance",
                "score": float(prompt_metrics.get("sov_performance_score") or 0.0),
                "weight_pct": 40.0,
                "detail": (
                    "Relative rank against the 10 most-mentioned website-backed competitors "
                    "in page-specific prompt responses."
                ),
                "finding_summary": (
                    f"Relative SOV score {float(prompt_metrics.get('sov_performance_score') or 0.0):.1f}/100; "
                    f"raw SOV {float(prompt_metrics.get('sov_pct') or 0.0):.1f}%."
                ),
                "evidence_example": "",
                "strengths": [],
                "improvements": [],
                "report_section": "page-prompts",
                "site_examples": [],
                "scope": "page_specific",
            },
        )
        ai_components.insert(
            0,
            {
                "key": "brand_visibility",
                "title": "Brand visibility",
                "score": float(prompt_metrics.get("visibility_pct") or 0.0),
                "weight_pct": 60.0,
                "detail": (
                    "Page-specific platform responses mentioning the brand divided by all "
                    "completed platform responses."
                ),
                "finding_summary": (
                    f"The brand appeared in {int(prompt_metrics.get('visible_response_count') or 0)} "
                    f"of {int(prompt_metrics.get('response_count') or 0)} responses."
                ),
                "evidence_example": "",
                "strengths": [],
                "improvements": [],
                "report_section": "page-prompts",
                "site_examples": [],
                "scope": "page_specific",
            },
        )

    readiness_components = {
        component.get("key"): dict(component)
        for component in ai_components
        if isinstance(component, dict) and component.get("key")
    }
    foundation_components = {
        component.get("key"): dict(component)
        for component in (foundation_detail.get("components") or [])
        if isinstance(component, dict) and component.get("key")
    }
    technical_component_weights = {
        "ai_crawler_report": 25.0,
        "ai_citability": 25.0,
        "ai_search_success": 12.5,
        "query_coverage_footprint": 12.5,
        "platform_readiness": 25.0,
    }
    technical_components: list[dict[str, Any]] = []
    for key, weight in technical_component_weights.items():
        source = (
            foundation_components.get(key)
            if key == "ai_crawler_report"
            else readiness_components.get(key)
        )
        if not source:
            continue
        source["weight_pct"] = weight
        technical_components.append(source)

    merged_content_components = (
        content_quality_details.get("components")
        or content_detail.get("components")
        or []
    )
    payload: dict[str, Any] = {
        "overall": overall_score(ai_visibility, technical, content),
        "ai_visibility": round(ai_visibility, 1),
        "technical_setup": round(technical, 1),
        "content_structure": round(content, 1),
        "prompt_metrics": prompt_metrics,
        "surface_metrics": scores.get("surface_metrics"),
        "platform_metrics": scores.get("platform_metrics"),
        "platform_readiness": technical_display.get("platform_readiness") or [],
        "crawler_access": technical_display.get("crawler_access") or {"rows": []},
        "content_quality_details": content_quality_details,
        "details": {
            "ai_visibility": {
                "score": round(ai_visibility, 1),
                "components": ai_components,
            },
            "technical_setup": {
                "score": round(technical, 1),
                "components": technical_components or foundation_detail.get("components") or [],
            },
            "technical_foundation": foundation_detail,
            "content_structure": {
                "score": round(content, 1),
                "components": merged_content_components,
            },
        },
        "component_scope": {
            **{key: "inherited" for key in INHERITED_KEYS},
            **{key: "page_specific" for key in PAGE_SPECIFIC_KEYS},
        },
        "scoring_meta": {
            "audit_label": "single_page",
            "ai_visibility_formula": (
                "0.60 * prompt_visibility + 0.40 * relative_sov"
                if prompt_metrics
                else "provisional: 0.70 * page_citability + 0.30 * citation_presence"
            ),
            "page_citability": round(citability, 1),
            "citation_presence": round(citation_presence, 1),
            "citation_count": citation_count,
        },
    }
    return _stamp_component_scope(payload, page_url)


def citations_matching_page(audit_dir: Path, page_url: str) -> list[dict[str, Any]]:
    target = normalize_citation_url(page_url)
    if not target:
        return []
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def consider(url: str, extra: dict[str, Any] | None = None) -> None:
        raw = str(url or "").strip()
        if not raw:
            return
        key = normalize_citation_url(raw)
        if key != target:
            return
        payload = extra or {}
        identity = (
            key,
            str(payload.get("platform") or ""),
            str(payload.get("prompt") or payload.get("query") or ""),
        )
        if identity in seen:
            return
        seen.add(identity)
        matches.append({"url": raw, **payload})

    citations_payload = _read_json(audit_dir / "prompt_performance_citations.json") or {}
    for item in citations_payload.get("top_cited_urls") or citations_payload.get("urls") or []:
        if isinstance(item, dict):
            consider(str(item.get("url") or ""), item)
        elif isinstance(item, str):
            consider(item)

    metrics = _read_json(audit_dir / "prompt_performance_metrics.json") or {}
    live = metrics.get("live_probe") if isinstance(metrics.get("live_probe"), dict) else {}
    _collect_citations_from_per_prompt(live.get("per_prompt") or [], consider, source="master")

    return matches


def _collect_citations_from_per_prompt(rows: Any, consider: Any, *, source: str) -> None:
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        prompt = str(row.get("prompt") or "")
        for key, value in row.items():
            if not str(key).startswith("citations_") or not isinstance(value, list):
                continue
            platform = str(key)[len("citations_") :]
            for citation in value:
                extra = {"platform": platform, "prompt": prompt, "source": source}
                if isinstance(citation, dict):
                    consider(str(citation.get("url") or ""), {**citation, **extra, "prompt": prompt})
                elif isinstance(citation, str):
                    consider(citation, extra)


def citations_from_page_probe(page_dir: Path, page_url: str) -> list[dict[str, Any]]:
    target = normalize_citation_url(page_url)
    if not target:
        return []
    probe = _read_json(page_dir / PAGE_PROBE_FILE) or {}
    live = probe.get("live_probe") if isinstance(probe.get("live_probe"), dict) else probe
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def consider(url: str, extra: dict[str, Any] | None = None) -> None:
        raw = str(url or "").strip()
        if not raw:
            return
        key = normalize_citation_url(raw)
        if key != target:
            return
        payload = extra or {}
        identity = (
            key,
            str(payload.get("platform") or ""),
            str(payload.get("prompt") or ""),
        )
        if identity in seen:
            return
        seen.add(identity)
        matches.append({"url": raw, **payload})

    if isinstance(live, dict):
        _collect_citations_from_per_prompt(live.get("per_prompt") or [], consider, source="page_probe")
    return matches


def merge_page_citations(audit_dir: Path, page_id: str, page_url: str) -> list[dict[str, Any]]:
    page_dir = page_audits_root(audit_dir) / page_id
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in citations_matching_page(audit_dir, page_url) + citations_from_page_probe(page_dir, page_url):
        if not isinstance(item, dict):
            continue
        identity = (
            normalize_citation_url(str(item.get("url") or "")),
            str(item.get("platform") or ""),
            str(item.get("prompt") or item.get("query") or ""),
        )
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(item)
    return merged


def read_index(audit_dir: Path) -> dict[str, Any]:
    payload = _read_json(index_path(audit_dir))
    if not payload:
        return {"schema_version": 1, "items": []}
    if not isinstance(payload.get("items"), list):
        payload["items"] = []
    return payload


def _index_summary(record: dict[str, Any]) -> dict[str, Any]:
    scores = record.get("scores") if isinstance(record.get("scores"), dict) else {}
    return {
        "id": record.get("id"),
        "url": record.get("url"),
        "parent_audit_id": record.get("parent_audit_id"),
        "status": record.get("status"),
        "stage": record.get("stage"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "title": record.get("title") or record.get("url"),
        "error": record.get("error"),
        "scores": {
            "ai_visibility": scores.get("ai_visibility"),
            "technical_setup": scores.get("technical_setup"),
            "content_quality": scores.get("content_quality"),
            "overall": scores.get("overall"),
        },
    }


def _upsert_index_item(audit_dir: Path, item: dict[str, Any]) -> None:
    with _INDEX_LOCK:
        index = read_index(audit_dir)
        items = [row for row in index.get("items") or [] if isinstance(row, dict)]
        page_id = str(item.get("id") or "")
        next_items = [row for row in items if str(row.get("id") or "") != page_id]
        next_items.insert(0, item)
        index["items"] = next_items
        index["updated_at"] = _utc_now()
        _write_json(index_path(audit_dir), index)


def list_page_audits_for_parent(audit_dir: Path) -> list[dict[str, Any]]:
    return [row for row in (read_index(audit_dir).get("items") or []) if isinstance(row, dict)]


def _parent_base_url(audit_dir: Path) -> str:
    try:
        summary = geo.load_audit_summary(audit_dir)
    except FileNotFoundError:
        summary = _read_json(audit_dir / "audit_summary.json") or {}
    return str(summary.get("base_url") or summary.get("primary_url") or "").strip()


def _parent_brand(audit_dir: Path) -> str:
    onboarding = _read_json(audit_dir / "onboarding_context.json") or {}
    return str(
        onboarding.get("brand_name_used") or onboarding.get("brand_name") or ""
    ).strip()


def _parent_competitors(audit_dir: Path) -> list[dict[str, str]]:
    onboarding = _read_json(audit_dir / "onboarding_context.json") or {}
    detail = onboarding.get("competitors_detail")
    if not isinstance(detail, list):
        return []
    rows: list[dict[str, str]] = []
    for item in detail:
        if not isinstance(item, dict):
            continue
        website = str(item.get("competitor_website") or "").strip()
        brand = str(item.get("competitor_brand") or "").strip()
        if not website and not brand:
            continue
        rows.append({"competitor_brand": brand, "competitor_website": website})
    return rows


def list_all_page_audits() -> list[dict[str, Any]]:
    root = geo.audit_output_base()
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for audit_dir in root.iterdir():
        if not audit_dir.is_dir() or not (audit_dir / "audit_summary.json").is_file():
            continue
        for item in list_page_audits_for_parent(audit_dir):
            row = dict(item)
            row.setdefault("parent_audit_id", audit_dir.name)
            row["parent_brand_name"] = _parent_brand(audit_dir)
            row["parent_base_url"] = _parent_base_url(audit_dir)
            rows.append(row)
    rows.sort(
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    return rows


def load_page_audit(audit_dir: Path, page_id: str) -> dict[str, Any] | None:
    return _read_json(page_audits_root(audit_dir) / page_id / PAGE_AUDIT_FILE)


def _existing_page_row(audit: dict[str, Any], page_url: str) -> dict[str, Any] | None:
    target = normalize_citation_url(page_url)
    for page in audit.get("pages") or []:
        if not isinstance(page, dict):
            continue
        candidate = str(page.get("final_url") or page.get("url") or "")
        if normalize_citation_url(candidate) == target:
            return page
    return None


def fetch_page_snapshot(page_url: str, *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    crawl = geo.load_crawl_site()
    result = crawl._request(page_url)
    entry: dict[str, Any] = {
        "url": page_url,
        "http_status": result.status,
        "fetch_error": result.error,
        "json_ld_saved": None,
        "og_images_saved": [],
    }
    if result.status != 200 or not result.body:
        if fallback:
            cloned = copy.deepcopy(fallback)
            cloned["_fetch_fallback"] = True
            cloned.setdefault("url", page_url)
            return cloned
        return entry

    entry["final_url"] = (result.final_url or page_url or "").strip()
    try:
        html = result.body.decode("utf-8", errors="replace")
    except Exception:
        html = result.body.decode("latin-1", errors="replace")
    headers = result.headers or {}
    entry["x_robots_tag"] = headers.get("x-robots-tag") if isinstance(headers, dict) else None
    generic_robots, named_robots = crawl.extract_robots_meta_for_page(html)
    entry["meta_robots_generic"] = generic_robots
    entry["meta_robots_named"] = named_robots
    entry["page_title"] = crawl.extract_html_title(html)
    entry["headings"] = crawl.extract_page_headings(html)
    entry["content_signals"] = crawl.compute_page_content_signals(html)
    blocks, _, _ = crawl.parse_json_ld_blocks(html)
    entry["has_json_ld"] = len(blocks) > 0
    entry["json_ld_blocks"] = len(blocks)
    entry["template_hint"] = crawl.crawl_template_hint(page_url)
    entry["json_ld_types"] = crawl.schema_types_from_nodes(blocks)
    same: list[str] = []
    for block in blocks:
        crawl.collect_same_as_from_obj(block, same)
    entry["same_as"] = same
    entry["og_image_urls"] = crawl.extract_og_images(html)
    entry["meta_description"] = crawl.extract_meta_description(html)
    return entry


def _category_by_key(categories: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for category in categories:
        key = getattr(category, "key", None)
        if key is None and isinstance(category, dict):
            key = category.get("key")
        if key:
            out[str(key)] = category
    return out


def _sub_score(category: Any, key: str) -> float | None:
    if category is None:
        return None
    subs = getattr(category, "subs", None)
    if subs is None and isinstance(category, dict):
        subs = category.get("subs") or []
    for sub in subs or []:
        sub_key = getattr(sub, "key", None)
        if sub_key is None and isinstance(sub, dict):
            sub_key = sub.get("key")
        if str(sub_key) != key:
            continue
        score = getattr(sub, "score", None) if not isinstance(sub, dict) else sub.get("score")
        try:
            return float(score)
        except (TypeError, ValueError):
            return None
    return None


def _category_score(category: Any) -> float | None:
    if category is None:
        return None
    score = getattr(category, "score", None) if not isinstance(category, dict) else category.get("score")
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def score_page_audit(
    *,
    parent_audit: dict[str, Any],
    page_row: dict[str, Any],
    citations: list[dict[str, Any]],
    prompt_metrics: dict[str, Any] | None = None,
    surface_metrics: dict[str, Any] | None = None,
    platform_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    synthetic = copy.deepcopy(parent_audit)
    synthetic["pages"] = [page_row]
    synthetic["audit_label"] = "single_page"
    create_report = geo.load_create_report()
    _unused, categories = create_report.score_audit(synthetic)
    by_key = _category_by_key(categories)

    citability = _sub_score(by_key.get("ai_visibility"), "ai_citability")
    if citability is None:
        citability = _category_score(by_key.get("ai_visibility")) or 0.0
    citation_score = citation_presence_score(len(citations))
    if prompt_metrics:
        ai_visibility = round(float(prompt_metrics.get("score") or 0.0), 1)
    else:
        ai_visibility = round(0.70 * citability + 0.30 * citation_score, 1)

    technical = _category_score(by_key.get("technical_setup")) or 0.0
    content = _category_score(by_key.get("content_structure")) or 0.0
    return {
        "ai_visibility": ai_visibility,
        "technical_setup": round(technical, 1),
        "content_quality": round(content, 1),
        "overall": overall_score(ai_visibility, technical, content),
        "prompt_metrics": prompt_metrics,
        "surface_metrics": surface_metrics,
        "platform_metrics": platform_metrics,
        "details": {
            "page_citability": round(citability, 1),
            "citation_presence": round(citation_score, 1),
            "citation_count": len(citations),
        },
    }


def page_probe_metrics(
    audit_dir: Path,
    live: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Use the same visibility/SOV model and surface groups as the master report."""
    overall = geo.load_prompt_visibility_metrics(
        audit_dir,
        prefer_persisted=False,
        live_override=live,
    )
    if not overall:
        raise ValueError("Page prompt probes returned no completed platform responses")
    surfaces = {
        "chatbots": geo.load_prompt_visibility_metrics(
            audit_dir,
            prefer_persisted=False,
            live_override=live,
            platforms=("gemini", "openai", "claude"),
        ),
        "overviews": geo.load_prompt_visibility_metrics(
            audit_dir,
            prefer_persisted=False,
            live_override=live,
            platforms=("google_aio",),
        ),
    }
    per_platform = {
        platform: geo.load_prompt_visibility_metrics(
            audit_dir,
            prefer_persisted=False,
            live_override=live,
            platforms=(platform,),
        )
        for platform in ("gemini", "openai", "claude", "google_aio")
    }
    return overall, surfaces, per_platform


def _maybe_gemini_overlay(
    page_dir: Path,
    parent_audit: dict[str, Any],
    page_row: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        from content_quality_llm import generate_content_quality_gemini
    except Exception:
        return None
    mini = copy.deepcopy(parent_audit)
    mini["pages"] = [page_row]
    mini["audit_label"] = "single_page"
    _write_json(page_dir / "audit_summary.json", mini)
    try:
        payload, error = generate_content_quality_gemini(
            page_dir,
            brand_name=str(parent_audit.get("brand_name") or ""),
            site_url=str(parent_audit.get("base_url") or ""),
            force=True,
            cap=1,
        )
    except Exception:
        log.exception("Gemini overlay failed for page audit %s", page_dir)
        return None
    if not payload:
        log.info("Gemini overlay skipped: %s", error)
        return None
    _write_json(page_dir / GEMINI_FILE, payload)
    return payload


def run_page_audit_job(audit_dir: Path, page_id: str, page_url: str) -> dict[str, Any]:
    page_dir = page_audits_root(audit_dir) / page_id
    record = load_page_audit(audit_dir, page_id) or {}
    record.update(
        {
            "id": page_id,
            "url": page_url,
            "parent_audit_id": audit_dir.name,
            "status": "running",
            "updated_at": _utc_now(),
            "error": None,
        }
    )
    _write_json(page_dir / PAGE_AUDIT_FILE, record)
    _upsert_index_item(audit_dir, _index_summary(record))

    try:
        parent = geo.load_audit_summary(audit_dir)
        fallback = _existing_page_row(parent, page_url)
        snapshot = fetch_page_snapshot(page_url, fallback=fallback)
        _write_json(page_dir / PAGE_SNAPSHOT_FILE, snapshot)
        synthetic = copy.deepcopy(parent)
        synthetic["pages"] = [snapshot]
        synthetic["audit_label"] = "single_page"
        _write_json(page_dir / "audit_summary.json", synthetic)
        _maybe_gemini_overlay(page_dir, parent, snapshot)
        record.update(
            {
                "status": "running",
                "stage": "generating_prompts",
                "updated_at": _utc_now(),
                "title": snapshot.get("page_title") or page_url,
                "http_status": snapshot.get("http_status"),
                "scores": None,
                "inherited": list(INHERITED_KEYS),
                "page_specific": list(PAGE_SPECIFIC_KEYS),
                "citations": [],
                "used_crawl_fallback": bool(snapshot.get("_fetch_fallback")),
            }
        )
        _write_json(page_dir / PAGE_AUDIT_FILE, record)
        _upsert_index_item(audit_dir, _index_summary(record))

        generated = suggest_prompts_for_page_audit(audit_dir, page_id, require_done=False)
        prompts = normalize_page_prompts(generated.get("prompts"))
        if not prompts:
            raise RuntimeError("Page prompt generation returned no usable prompts")
        record.update(
            {
                "status": "running",
                "stage": "probing",
                "probe_status": "starting",
                "updated_at": _utc_now(),
            }
        )
        _write_json(page_dir / PAGE_AUDIT_FILE, record)
        _upsert_index_item(audit_dir, _index_summary(record))
        from api.prompt_jobs import enqueue_page_prompt_job

        queued = enqueue_page_prompt_job(audit_dir, page_id=page_id, prompts=prompts)
        record["probe_status"] = str(queued.get("status") or "queued")
        return record
    except Exception as exc:
        log.exception("Page audit failed for %s %s", audit_dir, page_url)
        record.update({"status": "error", "updated_at": _utc_now(), "error": str(exc)})
        _write_json(page_dir / PAGE_AUDIT_FILE, record)
        _upsert_index_item(audit_dir, _index_summary(record))
        return record
    finally:
        with _JOBS_LOCK:
            _RUNNING_JOBS.discard(_job_key(audit_dir, page_id))


def enqueue_page_audit(audit_dir: Path, raw_url: str) -> dict[str, Any]:
    try:
        page_url = normalize_http_url(raw_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    base_url = _parent_base_url(audit_dir)
    if not base_url:
        raise HTTPException(400, "Master audit is missing a base URL")
    if not urls_are_same_site(page_url, base_url):
        raise HTTPException(
            400,
            f"URL must be on the same site as the master audit ({hostname_key(base_url)})",
        )
    page_id = page_id_for_url(page_url)
    existing = load_page_audit(audit_dir, page_id)
    if existing and str(existing.get("status") or "") in {"queued", "running"}:
        return existing
    if existing:
        reset_page_audit_artifacts(audit_dir, page_id)

    now = _utc_now()
    record = {
        "id": page_id,
        "url": page_url,
        "parent_audit_id": audit_dir.name,
        "status": "queued",
        "stage": "queued",
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
        "title": page_url,
        "scores": None,
        "citations": [],
        "probe_status": None,
        "probe_error": None,
        "probe_progress": None,
        "inherited": list(INHERITED_KEYS),
        "page_specific": list(PAGE_SPECIFIC_KEYS),
        "error": None,
    }
    _write_json(page_audits_root(audit_dir) / page_id / PAGE_AUDIT_FILE, record)
    _upsert_index_item(audit_dir, _index_summary(record))

    # The persisted status above is the source of truth for "already running"; this set only
    # guards a same-instant double submit, so a leaked claim must never block a re-run.
    job_key = _job_key(audit_dir, page_id)
    with _JOBS_LOCK:
        _RUNNING_JOBS.add(job_key)
    try:
        threading.Thread(
            target=run_page_audit_job,
            args=(audit_dir, page_id, page_url),
            name=f"page-audit-{page_id[:12]}",
            daemon=True,
        ).start()
    except Exception:
        with _JOBS_LOCK:
            _RUNNING_JOBS.discard(job_key)
        raise
    return record


def _job_key(audit_dir: Path, page_id: str) -> str:
    return f"{audit_dir.resolve()}:{page_id}"


def reset_page_audit_artifacts(audit_dir: Path, page_id: str) -> list[str]:
    """Drop cached page artifacts so a repeat request re-runs the full audit."""
    page_dir = page_audits_root(audit_dir) / page_id
    removed: list[str] = []
    for name in PAGE_RERUN_STALE_FILES:
        path = page_dir / name
        if not path.is_file():
            continue
        try:
            path.unlink()
        except OSError:
            log.warning("Could not clear stale page artifact %s", path)
            continue
        removed.append(name)
    return removed


def _require_page_id(page_id: str) -> str:
    if "/" in page_id or "\\" in page_id or page_id in {".", ".."} or not page_id.strip():
        raise HTTPException(404, "Page audit not found")
    return page_id


def normalize_page_prompts(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        text = " ".join(str(item or "").split()).strip()
        if len(text) < 8:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= MAX_PAGE_PROMPTS:
            break
    return out


def page_probe_pending(page_dir: Path) -> dict[str, Any] | None:
    payload = _read_json(page_dir / PAGE_PROBE_PENDING_FILE)
    if not payload:
        return None
    if str(payload.get("status") or "") in {"queued", "starting", "running"}:
        return payload
    return None


def _parent_industry(audit_dir: Path) -> str:
    onboarding = _read_json(audit_dir / "onboarding_context.json") or {}
    return str(onboarding.get("industry_used") or onboarding.get("industry") or "").strip()


def _parent_market(audit_dir: Path) -> tuple[str, str]:
    onboarding = _read_json(audit_dir / "onboarding_context.json") or {}
    return (
        str(onboarding.get("geo_market_country") or "").strip(),
        str(onboarding.get("geo_market_country_code") or "").strip(),
    )


def snapshot_excerpt(snapshot: dict[str, Any]) -> str:
    signals = snapshot.get("content_signals") if isinstance(snapshot.get("content_signals"), dict) else {}
    return str(signals.get("representative_excerpt") or "").strip()


def suggest_prompts_for_page_audit(
    audit_dir: Path,
    page_id: str,
    *,
    require_done: bool = True,
) -> dict[str, Any]:
    record = load_page_audit(audit_dir, page_id)
    if not record:
        raise HTTPException(404, "Page audit not found")
    if require_done and str(record.get("status") or "") != "done":
        raise HTTPException(400, "Finish the page audit before generating prompts")
    page_dir = page_audits_root(audit_dir) / page_id
    snapshot = _read_json(page_dir / PAGE_SNAPSHOT_FILE) or {}
    page_url = str(record.get("url") or snapshot.get("url") or "").strip()
    brand = _parent_brand(audit_dir)
    if not brand:
        raise HTTPException(400, "Master audit is missing a brand name")
    from prompt_suggest import suggest_prompts_for_page

    headings = snapshot.get("headings") if isinstance(snapshot.get("headings"), dict) else {}
    market_country, market_code = _parent_market(audit_dir)
    try:
        prompts = suggest_prompts_for_page(
            page_url=page_url,
            brand_name=brand,
            site_url=_parent_base_url(audit_dir),
            page_title=str(snapshot.get("page_title") or ""),
            meta_description=str(snapshot.get("meta_description") or ""),
            headings=headings,
            json_ld_types=list(snapshot.get("json_ld_types") or [])
            if isinstance(snapshot.get("json_ld_types"), list)
            else [],
            template_hint=str(snapshot.get("template_hint") or ""),
            excerpt=snapshot_excerpt(snapshot),
            industry=_parent_industry(audit_dir),
            market_country=market_country,
            market_country_code=market_code,
            max_prompts=DEFAULT_PAGE_PROMPTS,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log.exception("Page prompt suggestion failed for %s %s", audit_dir, page_id)
        raise HTTPException(502, f"Could not generate page prompts: {exc}") from exc
    payload = {
        "prompts": prompts,
        "generated_at": _utc_now(),
        "source": "page_content",
        "page_url": page_url,
    }
    _write_json(page_dir / PAGE_PROMPTS_FILE, payload)
    return payload


def apply_page_probe_to_scores(audit_dir: Path, page_id: str) -> dict[str, Any]:
    page_dir = page_audits_root(audit_dir) / page_id
    record = load_page_audit(audit_dir, page_id) or {}
    snapshot = _read_json(page_dir / PAGE_SNAPSHOT_FILE) or {}
    page_url = str(record.get("url") or snapshot.get("url") or "")
    citations = merge_page_citations(audit_dir, page_id, page_url)
    try:
        parent = geo.load_audit_summary(audit_dir)
    except FileNotFoundError:
        parent = _read_json(audit_dir / "audit_summary.json") or {}
    probe = _read_json(page_dir / PAGE_PROBE_FILE) or {}
    live = probe.get("live_probe") if isinstance(probe.get("live_probe"), dict) else {}
    prompt_metrics, surface_metrics, platform_metrics = page_probe_metrics(audit_dir, live)
    scores = score_page_audit(
        parent_audit=parent,
        page_row=snapshot or {"url": page_url},
        citations=citations,
        prompt_metrics=prompt_metrics,
        surface_metrics=surface_metrics,
        platform_metrics=platform_metrics,
    )
    breakdown = build_page_breakdown(
        page_dir,
        scores=scores,
        citations=citations,
        page_url=page_url,
    )
    content = breakdown.get("content_structure")
    if isinstance(content, (int, float)):
        scores["content_quality"] = round(float(content), 1)
        scores["overall"] = overall_score(
            float(scores["ai_visibility"]),
            float(scores["technical_setup"]),
            float(scores["content_quality"]),
        )
    record.update(
        {
            "status": "done",
            "stage": "complete",
            "updated_at": _utc_now(),
            "scores": scores,
            "citations": citations[:20],
            "probe_status": "done",
        }
    )
    _write_json(page_dir / PAGE_AUDIT_FILE, record)
    _write_json(page_dir / PAGE_BREAKDOWN_FILE, breakdown)
    _upsert_index_item(audit_dir, _index_summary(record))
    return record


def run_page_prompt_job(audit_dir: Path, page_id: str, prompts: list[str]) -> dict[str, Any]:
    """Execute live probes for one page and persist only under that page dir."""
    from api.prompt_performance import (
        _brand_and_site,
        _competitor_lists,
        _competitors_detail,
        _load_audit_onboarding,
        _primary_market_from_context,
    )
    from prompt_suggest import run_live_prompt_probes

    page_dir = page_audits_root(audit_dir) / page_id
    page_dir.mkdir(parents=True, exist_ok=True)
    used = normalize_page_prompts(prompts)
    if not used:
        raise ValueError("At least one prompt is required")

    pending = _read_json(page_dir / PAGE_PROBE_PENDING_FILE) or {}
    pending.update({"status": "running", "updated_at": _utc_now()})
    _write_json(page_dir / PAGE_PROBE_PENDING_FILE, pending)

    ctx = _load_audit_onboarding(audit_dir)
    brand, site = _brand_and_site(ctx)
    if not brand:
        brand = _parent_brand(audit_dir)
    if not site:
        site = _parent_base_url(audit_dir)
    if not brand:
        raise ValueError("Brand name is required")
    market_country, market_code = _primary_market_from_context(ctx)
    competitor_urls, competitor_brands = _competitor_lists(
        _competitors_detail(ctx),
        report_mode=True,
    )
    progress_events: list[dict[str, Any]] = []

    def _on_progress(event: dict[str, Any]) -> None:
        progress_events.append(dict(event))
        latest = page_dir / PAGE_PROBE_PROGRESS_FILE
        try:
            latest.write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in progress_events[-40:]),
                encoding="utf-8",
            )
        except OSError:
            pass
        current = _read_json(page_dir / PAGE_PROBE_PENDING_FILE) or pending
        current["probe_progress"] = event
        current["status"] = "running"
        current["updated_at"] = _utc_now()
        _write_json(page_dir / PAGE_PROBE_PENDING_FILE, current)

    live = run_live_prompt_probes(
        used,
        brand_name=brand,
        brand_site_url=site,
        competitor_urls=competitor_urls,
        competitor_brands=competitor_brands,
        num_runs=1,
        max_prompts=MAX_PAGE_PROMPTS,
        market_country=market_country,
        market_country_code=market_code,
        progress_callback=_on_progress,
    )
    prompt_metrics, surface_metrics, platform_metrics = page_probe_metrics(audit_dir, live)
    payload = {
        "live_probe": live,
        "prompt_metrics": prompt_metrics,
        "surface_metrics": surface_metrics,
        "platform_metrics": platform_metrics,
        "prompts": used,
        "page_id": page_id,
        "completed_at": _utc_now(),
        "num_runs": 1,
        "source": "page_content",
    }
    _write_json(page_dir / PAGE_PROBE_FILE, payload)
    apply_page_probe_to_scores(audit_dir, page_id)
    return payload


def enqueue_page_prompts(audit_dir: Path, page_id: str, prompts: list[str]) -> dict[str, Any]:
    record = load_page_audit(audit_dir, page_id)
    if not record:
        raise HTTPException(404, "Page audit not found")
    if str(record.get("status") or "") != "done":
        raise HTTPException(400, "Finish the page audit before running prompts")
    used = normalize_page_prompts(prompts)
    if not used:
        raise HTTPException(400, "Enter at least one prompt")
    page_dir = page_audits_root(audit_dir) / page_id
    existing_prompts = _read_json(page_dir / PAGE_PROMPTS_FILE) or {}
    _write_json(
        page_dir / PAGE_PROMPTS_FILE,
        {
            **existing_prompts,
            "prompts": used,
            "updated_at": _utc_now(),
            "source": existing_prompts.get("source") or "page_content",
        },
    )
    from api.prompt_jobs import enqueue_page_prompt_job

    record["status"] = "running"
    record["stage"] = "probing"
    record["probe_status"] = "starting"
    record["updated_at"] = _utc_now()
    _write_json(page_dir / PAGE_AUDIT_FILE, record)
    _upsert_index_item(audit_dir, _index_summary(record))
    try:
        queued = enqueue_page_prompt_job(audit_dir, page_id=page_id, prompts=used)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**queued, "prompts": used, "page_id": page_id}


@router.post("/api/audits/{audit_id}/page-audits")
def create_page_audit(audit_id: str, body: CreatePageAuditBody, request: Request) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    _require_write_access(request, audit_dir)
    return enqueue_page_audit(audit_dir, body.url)


@router.get("/api/audits/{audit_id}/page-audits")
def get_parent_page_audits(audit_id: str) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    return {"parent_audit_id": audit_dir.name, "items": list_page_audits_for_parent(audit_dir)}


@router.get("/api/audits/{audit_id}/page-audits/{page_id}")
def get_page_audit(audit_id: str, page_id: str) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    if "/" in page_id or "\\" in page_id or page_id in {".", ".."}:
        raise HTTPException(404, "Page audit not found")
    record = load_page_audit(audit_dir, page_id)
    if not record:
        raise HTTPException(404, "Page audit not found")
    snapshot = _read_json(page_audits_root(audit_dir) / page_id / PAGE_SNAPSHOT_FILE)
    record["snapshot"] = snapshot
    record["parent_brand_name"] = _parent_brand(audit_dir)
    record["parent_base_url"] = _parent_base_url(audit_dir)
    record["parent_competitors"] = _parent_competitors(audit_dir)
    record["breakdown"] = ensure_page_breakdown(audit_dir, page_id, record)
    record["component_scope"] = (record.get("breakdown") or {}).get("component_scope") or {
        **{key: "inherited" for key in INHERITED_KEYS},
        **{key: "page_specific" for key in PAGE_SPECIFIC_KEYS},
    }
    page_dir = page_audits_root(audit_dir) / page_id
    record["page_prompts"] = _read_json(page_dir / PAGE_PROMPTS_FILE)
    record["page_probe"] = _read_json(page_dir / PAGE_PROBE_FILE)
    pending = page_probe_pending(page_dir)
    if pending:
        record["probe_status"] = str(pending.get("status") or "queued")
        record["probe_progress"] = pending.get("probe_progress")
    elif record.get("page_probe"):
        record["probe_status"] = "done"
    else:
        record.setdefault("probe_status", None)
    return record


@router.post("/api/audits/{audit_id}/page-audits/{page_id}/suggest-prompts")
def post_suggest_page_prompts(audit_id: str, page_id: str, request: Request) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    _require_write_access(request, audit_dir)
    page_id = _require_page_id(page_id)
    return suggest_prompts_for_page_audit(audit_dir, page_id)


@router.post("/api/audits/{audit_id}/page-audits/{page_id}/run-prompts")
def post_run_page_prompts(
    audit_id: str,
    page_id: str,
    body: RunPagePromptsBody,
    request: Request,
) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    _require_write_access(request, audit_dir)
    page_id = _require_page_id(page_id)
    return enqueue_page_prompts(audit_dir, page_id, body.prompts)


def ensure_page_breakdown(
    audit_dir: Path,
    page_id: str,
    record: dict[str, Any],
) -> dict[str, Any] | None:
    page_dir = page_audits_root(audit_dir) / page_id
    existing = _read_json(page_dir / PAGE_BREAKDOWN_FILE)
    if existing:
        return existing
    if str(record.get("status") or "") != "done":
        return None
    snapshot = _read_json(page_dir / PAGE_SNAPSHOT_FILE) or {}
    try:
        parent = geo.load_audit_summary(audit_dir)
    except FileNotFoundError:
        parent = _read_json(audit_dir / "audit_summary.json") or {}
    synthetic = copy.deepcopy(parent)
    synthetic["pages"] = [snapshot] if snapshot else []
    synthetic["audit_label"] = "single_page"
    _write_json(page_dir / "audit_summary.json", synthetic)
    page_url = str(record.get("url") or snapshot.get("url") or "")
    citations = record.get("citations")
    if not isinstance(citations, list):
        citations = citations_matching_page(audit_dir, page_url)
    scores = record.get("scores") if isinstance(record.get("scores"), dict) else {}
    try:
        breakdown = build_page_breakdown(
            page_dir,
            scores=scores,
            citations=citations,
            page_url=page_url,
        )
    except Exception:
        log.exception("Failed to backfill page breakdown for %s %s", audit_dir, page_id)
        return None
    _write_json(page_dir / PAGE_BREAKDOWN_FILE, breakdown)
    return breakdown


@router.get("/api/page-audits")
def get_all_page_audits() -> dict[str, Any]:
    return {"items": list_all_page_audits()}

"""Reddit insights — extract Reddit citations from live probe data.

Reddit's API requires formal approval under their Responsible Builder Policy
(https://support.reddithelp.com/hc/en-us/articles/42728983564564).
Rather than requiring API credentials, this module extracts all useful metadata
directly from the Reddit URL structure:

  https://www.reddit.com/r/{subreddit}/comments/{post_id}/{title_slug}/
                             ↑ subreddit                    ↑ readable title

This gives us: subreddit, post title, post ID, and a direct link — without
any API calls. Engagement data (upvotes/comments) is omitted since it requires
API access.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from api import geo_services as geo
from citation_context import (
    build_brand_tokens,
    build_competitor_tokens,
    infer_citation_brand_context,
    merge_brand_tokens,
)

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audits", tags=["reddit-insights"])

_REDDIT_DOMAINS = frozenset({"reddit.com", "www.reddit.com", "old.reddit.com", "redd.it"})


def _is_reddit_domain(domain: str) -> bool:
    d = domain.lower().removeprefix("www.")
    return d == "reddit.com" or d == "redd.it" or d.endswith(".reddit.com")


def _citation_text(response_text: str, citation_url: str, citation_domain: str) -> tuple[str, bool]:
    """Return the response passage containing an inline citation when available.

    Structured grounding citations are not always rendered inline. In that case,
    return a bounded copy of the associated response and mark it as non-exact.
    """
    text = (response_text or "").strip()
    if not text:
        return "", False

    sections = [
        section.strip()
        for section in re.split(r"(?m)(?:^#{1,3}\s+|\n{2,})", text)
        if section.strip()
    ]
    url_needle = (citation_url or "").lower().rstrip("/")
    domain_needle = (citation_domain or "").lower().removeprefix("www.")
    for section in sections:
        section_lower = section.lower()
        if (url_needle and url_needle in section_lower) or (
            domain_needle and domain_needle in section_lower
        ):
            return section[:1600], True

    return text[:1600], False


def _build_positional_topic_map(audit_dir: Path, per_prompt_texts: list[str]) -> dict[str, str]:
    """Map each per_prompt text to its topic using positional indexing.

    Mirrors the Prompts page logic: iterates pss_rows (or products_and_services_rows)
    in order, assigning per_prompt entries positionally (not by text matching).
    """
    result: dict[str, str] = {}
    try:
        ob = json.loads((audit_dir / "onboarding_context.json").read_text(encoding="utf-8", errors="replace"))
        rows = ob.get("pss_rows") or ob.get("products_and_services_rows") or []
        idx = 0
        for row in rows:
            topic = str(row.get("product_or_service") or "").strip()
            for _ in row.get("prompts") or []:
                if idx >= len(per_prompt_texts):
                    break
                pt = per_prompt_texts[idx]
                if pt and topic:
                    result[pt.lower()] = topic
                idx += 1
    except Exception:
        pass
    return result


def _extract_reddit_posts(
    audit_dir: Path,
    prompt_topic_map: dict[str, str],
    brand_tokens: list[str],
    competitor_tokens: list[str],
) -> list[dict[str, Any]]:
    """Return Reddit citations grouped by URL with platforms/topics/brand_mentioned."""
    probe_path = audit_dir / "prompt_performance_live_probe.json"
    if not probe_path.is_file():
        return []
    try:
        raw = json.loads(probe_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []

    data: dict[str, Any] = raw.get("live_probe", raw) if isinstance(raw, dict) else {}
    brand_tokens = merge_brand_tokens(
        brand_tokens,
        [str(token) for token in data.get("brand_match_tokens") or []],
    )

    # url → aggregated entry
    by_url: dict[str, dict[str, Any]] = {}

    for row in data.get("per_prompt", []):
        prompt_text = str(row.get("prompt") or "").strip()
        topic = prompt_topic_map.get(prompt_text.lower(), "")

        for plat in ("gemini", "openai", "claude", "google_aio"):
            response_text = str(row.get(f"{plat}_response") or "")
            for cit in row.get(f"citations_{plat}", []):
                url = str(cit.get("url") or "").strip()
                domain = str(cit.get("domain") or "").strip()
                if not url or not _is_reddit_domain(domain):
                    continue

                # Re-evaluate saved citations so surrounding response context
                # can repair older probe artifacts with a stored false value.
                ctx = infer_citation_brand_context(
                    response_text,
                    url,
                    domain,
                    brand_tokens,
                    competitor_tokens,
                    str(cit.get("title") or ""),
                )
                cit_brand = bool(cit.get("brand_cited")) or ctx["brand_cited"]

                if url not in by_url:
                    raw_title = cit.get("title")
                    clean_title = raw_title if raw_title and raw_title not in {"reddit.com", "www.reddit.com", "redd.it"} else None
                    by_url[url] = {
                        "url": url,
                        "domain": domain,
                        "title": clean_title,
                        "platforms": set(),
                        "prompts_by_topic": {},
                        "citation_details": [],
                        "citation_count": 0,
                        "brand_mentioned_count": 0,
                    }

                entry = by_url[url]
                entry["platforms"].add(plat)
                entry["citation_count"] += 1
                if cit_brand:
                    entry["brand_mentioned_count"] += 1
                if prompt_text:
                    t = topic or "Other"
                    entry["prompts_by_topic"].setdefault(t, set()).add(prompt_text)
                    citation_text, citation_text_is_exact = _citation_text(response_text, url, domain)
                    detail_key = (prompt_text, plat)
                    if not any(
                        (detail.get("prompt"), detail.get("platform")) == detail_key
                        for detail in entry["citation_details"]
                    ):
                        entry["citation_details"].append(
                            {
                                "prompt": prompt_text,
                                "topic": t,
                                "platform": plat,
                                "citation_text": citation_text,
                                "citation_text_is_exact": citation_text_is_exact,
                            }
                        )

    # Also collect from top_cited_urls
    for e in data.get("top_cited_urls", []):
        url = str(e.get("url") or "").strip()
        domain = str(e.get("domain") or "").strip()
        if not url or url in by_url or not _is_reddit_domain(domain):
            continue
        by_url[url] = {
            "url": url,
            "domain": domain,
            "title": e.get("title"),
            "platforms": set(),
            "prompts_by_topic": {},
            "citation_details": [],
            "citation_count": int(e.get("frequency") or 0),
            "brand_mentioned_count": 1 if e.get("brand_mentioned") else 0,
        }

    results = []
    for entry in by_url.values():
        entry["platforms"] = sorted(entry["platforms"])
        entry["topics_referencing"] = [
            {"topic": t, "prompts": sorted(ps)}
            for t, ps in entry["prompts_by_topic"].items()
        ]
        entry["brand_mentioned"] = entry.pop("brand_mentioned_count", 0) > 0
        del entry["prompts_by_topic"]
        results.append(entry)

    return results


def _parse_reddit_url(url: str) -> dict[str, str | None]:
    """Extract metadata from a Reddit URL without any API calls.

    Returns: {subreddit, post_id, reddit_title, canonical_url}

    URL patterns handled:
      https://www.reddit.com/r/{sub}/comments/{id}/{slug}/
      https://redd.it/{id}
    """
    result: dict[str, str | None] = {
        "subreddit": None,
        "post_id": None,
        "reddit_title": None,
        "canonical_url": url,
    }
    try:
        parsed = urllib.parse.urlparse(url)
        parts = [p for p in parsed.path.strip("/").split("/") if p]

        # Pattern: /r/{sub}/comments/{id}/{slug}
        if len(parts) >= 3 and parts[0] == "r" and "comments" in parts:
            ci = parts.index("comments")
            result["subreddit"] = parts[1]
            if ci + 1 < len(parts):
                result["post_id"] = parts[ci + 1]
            # Title slug is after the post_id
            if ci + 2 < len(parts):
                slug = parts[ci + 2]
                if not re.match(r"^[a-z0-9]{4,8}$", slug):
                    result["reddit_title"] = slug.replace("_", " ").replace("-", " ").title()
            # Build canonical URL
            if result["post_id"]:
                result["canonical_url"] = (
                    f"https://www.reddit.com/r/{parts[1]}/comments/{result['post_id']}/"
                    + (f"{parts[ci+2]}/" if ci + 2 < len(parts) else "")
                )

        # Pattern: redd.it/{id}
        elif len(parts) == 1 and "redd.it" in parsed.netloc:
            result["post_id"] = parts[0]
            result["canonical_url"] = f"https://redd.it/{parts[0]}"

    except Exception:
        pass

    return result




def _enrich_from_url(url: str) -> dict[str, Any]:
    """Extract all available metadata from the Reddit URL itself (no API calls)."""
    meta = _parse_reddit_url(url)
    result: dict[str, Any] = {}
    if meta.get("reddit_title"):
        result["reddit_title"] = meta["reddit_title"]
    if meta.get("subreddit"):
        result["subreddit"] = meta["subreddit"]
    if meta.get("post_id"):
        result["post_id"] = meta["post_id"]
    if meta.get("canonical_url"):
        result["post_url"] = meta["canonical_url"]
    result["enriched"] = bool(result.get("reddit_title") or result.get("subreddit"))
    return result


def _audit_dir_or_404(audit_id: str) -> Path:
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    return audit_dir


@router.get("/{audit_id}/reddit-insights")
def get_reddit_insights(audit_id: str) -> dict[str, Any]:
    """Extract Reddit citations and enrich with post metadata."""
    audit_dir = _audit_dir_or_404(audit_id)

    # Load brand/competitor context for citation-level analysis
    brand_name = ""
    brand_site_url = ""
    competitor_urls: list[str] = []
    competitor_brands: list[str] = []
    try:
        ob = json.loads((audit_dir / "onboarding_context.json").read_text(encoding="utf-8", errors="replace"))
        brand_name = str(ob.get("brand_name_used") or "").strip()
        brand_site_url = str(
            ob.get("brand_website_used") or ob.get("brand_url") or ob.get("brand_site_url") or ""
        ).strip()
        competitors = ob.get("competitors_detail") or ob.get("competitor_context") or []
        for c in competitors:
            if c.get("competitor_website"):
                competitor_urls.append(str(c["competitor_website"]))
            if c.get("competitor_brand"):
                competitor_brands.append(str(c["competitor_brand"]))
    except Exception:
        pass

    brand_tokens = build_brand_tokens(brand_name, brand_site_url)
    comp_tokens = build_competitor_tokens(competitor_urls, competitor_brands)

    # Build positional topic map
    probe_path = audit_dir / "prompt_performance_live_probe.json"
    per_prompt_texts: list[str] = []
    if probe_path.is_file():
        try:
            _raw = json.loads(probe_path.read_text(encoding="utf-8", errors="replace"))
            _live = _raw.get("live_probe", _raw) if isinstance(_raw, dict) else {}
            if not brand_name:
                brand_name = str(_live.get("brand_name") or "").strip()
            if not brand_site_url:
                brand_site_url = str(_live.get("brand_site_url") or "").strip()
            brand_tokens = merge_brand_tokens(
                build_brand_tokens(brand_name, brand_site_url),
                [str(token) for token in _live.get("brand_match_tokens") or []],
            )
            per_prompt_texts = [str(r.get("prompt") or "") for r in _live.get("per_prompt", [])]
        except Exception:
            pass

    prompt_topic_map = _build_positional_topic_map(audit_dir, per_prompt_texts)
    raw_posts = _extract_reddit_posts(audit_dir, prompt_topic_map, brand_tokens, comp_tokens)

    if not raw_posts:
        return {"posts": [], "total": 0, "brand_name": brand_name}

    posts: list[dict[str, Any]] = []
    for item in raw_posts:
        details = _enrich_from_url(item["url"])
        posts.append({**item, **details})

    posts.sort(key=lambda p: p.get("citation_count") or 0, reverse=True)
    return {"posts": posts, "total": len(posts), "brand_name": brand_name}

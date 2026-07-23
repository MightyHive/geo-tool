"""YouTube insights — extract YouTube citations and enrich via YouTube Data API v3."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from api import geo_services as geo
from api.reddit_insights import _citation_text
from citation_context import (
    build_brand_tokens,
    build_competitor_tokens,
    infer_citation_brand_context,
    merge_brand_tokens,
)

router = APIRouter(prefix="/api/audits", tags=["youtube-insights"])

_POS_WORDS = frozenset(
    "love great amazing excellent best recommend awesome fantastic helpful brilliant"
    " good worth positive top quality".split()
)
_NEG_WORDS = frozenset(
    "hate bad worst terrible horrible avoid disappointing poor broken awful negative"
    " overpriced fake misleading".split()
)


def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from various URL formats."""
    try:
        parsed = urllib.parse.urlparse(url)
        if "youtu.be" in parsed.netloc:
            return parsed.path.lstrip("/").split("?")[0] or None
        qs = urllib.parse.parse_qs(parsed.query)
        v = qs.get("v", [None])[0]
        if v:
            return v
        m = re.search(r"/(?:embed|v|shorts)/([A-Za-z0-9_-]{11})", parsed.path)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _parse_duration(iso: str) -> str:
    """Convert ISO 8601 duration (PT4M13S) to human-readable (4:13)."""
    if not iso:
        return ""
    m = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
        iso.upper(),
    )
    if not m:
        return iso
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    secs = int(m.group(3) or 0)
    if h:
        return f"{h}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def _duration_seconds(iso: str) -> int:
    if not iso:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso.upper())
    if not m:
        return 0
    return (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0))


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


def _extract_youtube_urls(
    audit_dir: Path,
    prompt_topic_map: dict[str, str],
    brand_tokens: list[str],
    competitor_tokens: list[str],
) -> list[dict[str, Any]]:
    """Return YouTube citations grouped by URL with all referencing prompts/platforms."""
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

    def _is_youtube(domain: str) -> bool:
        d = domain.lower().removeprefix("www.")
        return d in {"youtube.com", "youtu.be", "m.youtube.com"} or d.endswith(".youtube.com")

    for row in data.get("per_prompt", []):
        prompt_text = str(row.get("prompt") or "").strip()
        topic = prompt_topic_map.get(prompt_text.lower(), "")

        for plat in ("gemini", "openai", "claude", "google_aio"):
            response_text = str(row.get(f"{plat}_response") or "")
            for cit in row.get(f"citations_{plat}", []):
                url = str(cit.get("url") or "").strip()
                domain = str(cit.get("domain") or "").strip()
                if not url or not _is_youtube(domain):
                    continue

                # Re-evaluate saved citations so improved context/title rules
                # also repair older probe artifacts with a stored false value.
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
                    vid_id = _extract_video_id(url)
                    # Discard pre-stored junk titles that are just bare domain names
                    raw_title = cit.get("title")
                    clean_title = raw_title if raw_title and raw_title not in {"youtube.com", "youtu.be", "www.youtube.com"} else None
                    by_url[url] = {
                        "url": url,
                        "domain": domain,
                        "video_id": vid_id,
                        "title": clean_title,
                        "thumbnail_url": cit.get("thumbnail_url"),
                        "views": cit.get("views"),
                        "platforms": set(),
                        "prompts_by_topic": {},  # topic → set of prompts
                        "citation_details": [],
                        "citation_count": 0,
                        "brand_mentioned_count": 0,
                    }

                entry = by_url[url]
                entry["platforms"].add(plat)
                entry["citation_count"] = entry["citation_count"] + 1
                if cit_brand:
                    entry["brand_mentioned_count"] = entry["brand_mentioned_count"] + 1
                if prompt_text:
                    t = topic or "Other"
                    if t not in entry["prompts_by_topic"]:
                        entry["prompts_by_topic"][t] = set()
                    entry["prompts_by_topic"][t].add(prompt_text)
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

    # Also collect from top_cited_urls (no prompt context)
    for e in data.get("top_cited_urls", []):
        url = str(e.get("url") or "").strip()
        domain = str(e.get("domain") or "").strip()
        if not url or url in by_url or not _is_youtube(domain):
            continue
        vid_id = _extract_video_id(url)
        by_url[url] = {
            "url": url,
            "domain": domain,
            "video_id": vid_id,
            "title": e.get("title"),
            "thumbnail_url": e.get("thumbnail_url"),
            "views": e.get("views"),
            "platforms": set(),
            "prompts_by_topic": {},
            "citation_details": [],
            "citation_count": int(e.get("frequency") or 0),
            "brand_mentioned_count": 1 if e.get("brand_mentioned") else 0,
        }

    # Serialise sets → lists
    results = []
    for entry in by_url.values():
        entry["platforms"] = sorted(entry["platforms"])
        citing_prompts = {
            prompt
            for prompts in entry["prompts_by_topic"].values()
            for prompt in prompts
        }
        entry["citing_prompt_count"] = len(citing_prompts)
        entry["topics_referencing"] = [
            {"topic": t, "prompts": sorted(ps)}
            for t, ps in entry["prompts_by_topic"].items()
        ]
        entry["brand_mentioned"] = entry.pop("brand_mentioned_count", 0) > 0
        del entry["prompts_by_topic"]
        results.append(entry)

    return results


def _sentiment_from_description(text: str, brand_name: str) -> str:
    if not text or not brand_name:
        return "neutral"
    lower = text.lower()
    brand_lower = brand_name.lower()
    sentences = re.split(r"[.!?\n]", lower)
    pos = neg = 0
    for sentence in sentences:
        if brand_lower not in sentence:
            continue
        words = re.findall(r"\b\w+\b", sentence)
        pos += sum(1 for w in words if w in _POS_WORDS)
        neg += sum(1 for w in words if w in _NEG_WORDS)
    if pos == 0 and neg == 0:
        return "neutral"
    return "positive" if pos > neg else ("negative" if neg > pos else "mixed")


def _youtube_service_origin() -> str:
    """Origin sent as Referer/Origin for HTTP-referrer-restricted API keys."""
    return (
        os.environ.get("WEB_PUBLIC_ORIGIN")
        or os.environ.get("DEPLOY_PUBLIC_ORIGIN")
        or "https://geo-audit-dev-4sawlje3ya-ew.a.run.app"
    )


def _fetch_video_details(
    video_ids: list[str],
    brand_name: str,
    api_key: str,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Batch-fetch via YouTube Data API v3 videos.list (snippet + statistics + contentDetails).

    Sends a Referer header so API keys with HTTP-referrer restrictions don't block the request.
    Returns ``(results, api_error)`` where ``api_error`` is set on HTTP/network failure.
    """
    if not video_ids or not api_key:
        return {}, None

    import logging
    log = logging.getLogger(__name__)

    service_origin = _youtube_service_origin()
    api_error: str | None = None

    results: dict[str, dict[str, Any]] = {}
    with httpx.Client(
        headers={
            "Referer": service_origin,
            "Origin": service_origin,
        },
        timeout=10.0,
    ) as client:
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            params = {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(batch),
                "key": api_key,
            }
            try:
                resp = client.get("https://www.googleapis.com/youtube/v3/videos", params=params)
                if resp.status_code != 200:
                    detail = resp.text[:300]
                    try:
                        detail = str(
                            resp.json().get("error", {}).get("message") or detail
                        )
                    except Exception:
                        pass
                    api_error = f"YouTube API {resp.status_code}: {detail}"
                    log.warning("%s (Referer=%s)", api_error, service_origin)
                    continue
                for item in resp.json().get("items", []):
                    vid_id = item.get("id", "")
                    snippet = item.get("snippet", {})
                    stats = item.get("statistics", {})
                    content = item.get("contentDetails", {})
                    title = snippet.get("title", "")
                    description = snippet.get("description", "")
                    thumbnails = snippet.get("thumbnails", {})
                    thumbnail = (
                        thumbnails.get("maxres", {}).get("url")
                        or thumbnails.get("standard", {}).get("url")
                        or thumbnails.get("high", {}).get("url")
                        or thumbnails.get("medium", {}).get("url")
                        or thumbnails.get("default", {}).get("url")
                    )
                    iso_duration = content.get("duration", "")
                    results[vid_id] = {
                        "yt_title": title,
                        "channel_title": snippet.get("channelTitle", ""),
                        "published_at": snippet.get("publishedAt", ""),
                        "thumbnail_url": thumbnail,
                        "view_count": int(stats.get("viewCount") or 0),
                        "like_count": int(stats.get("likeCount") or 0),
                        "comment_count": int(stats.get("commentCount") or 0),
                        "duration": _parse_duration(iso_duration),
                        "duration_seconds": _duration_seconds(iso_duration),
                        "brand_sentiment": _sentiment_from_description(
                            f"{title} {description}", brand_name
                        ),
                    }
            except Exception as e:
                api_error = f"YouTube API request failed: {e}"
                log.warning("%s (Referer=%s)", api_error, service_origin)
                continue
    return results, api_error


def _audit_dir_or_404(audit_id: str) -> Path:
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    return audit_dir


@router.get("/{audit_id}/youtube-insights")
def get_youtube_insights(audit_id: str) -> dict[str, Any]:
    """Extract YouTube citations and enrich with video statistics."""
    audit_dir = _audit_dir_or_404(audit_id)

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
    reviewed_prompt_count = len({prompt.strip() for prompt in per_prompt_texts if prompt.strip()})
    raw_urls = _extract_youtube_urls(audit_dir, prompt_topic_map, brand_tokens, comp_tokens)
    api_key = (
        os.environ.get("YOUTUBE_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    ).strip()

    if not raw_urls:
        return {
            "videos": [],
            "total": 0,
            "reviewed_prompt_count": reviewed_prompt_count,
            "brand_name": brand_name,
            # Reflect key presence even when there are no citations yet — otherwise the UI
            # shows a false "YouTube API key not configured" banner on empty audits.
            "api_available": bool(api_key),
        }

    video_ids = [item["video_id"] for item in raw_urls if item.get("video_id")]

    enriched_map: dict[str, dict[str, Any]] = {}
    api_error: str | None = None
    if api_key and video_ids:
        enriched_map, api_error = _fetch_video_details(
            list(dict.fromkeys(video_ids)), brand_name, api_key
        )
    elif not api_key:
        api_error = "YOUTUBE_API_KEY (or GOOGLE_API_KEY) is not set"

    videos: list[dict[str, Any]] = []
    for item in raw_urls:
        vid_id = item.get("video_id")
        details = enriched_map.get(vid_id, {}) if vid_id else {}
        title = str(details.get("yt_title") or item.get("title") or "")
        title_context = infer_citation_brand_context(
            "",
            str(item.get("url") or ""),
            str(item.get("domain") or ""),
            brand_tokens,
            comp_tokens,
            title,
        )
        videos.append(
            {
                **item,
                **details,
                "brand_mentioned": bool(item.get("brand_mentioned"))
                or title_context["brand_cited"],
                "citation_percentage": (
                    round(
                        100
                        * int(item.get("citing_prompt_count") or 0)
                        / reviewed_prompt_count,
                        1,
                    )
                    if reviewed_prompt_count
                    else None
                ),
                "enriched": bool(details),
            }
        )

    videos.sort(key=lambda v: v.get("view_count") or v.get("views") or 0, reverse=True)
    enriched_count = sum(1 for v in videos if v.get("enriched"))
    return {
        "videos": videos,
        "total": len(videos),
        "reviewed_prompt_count": reviewed_prompt_count,
        "brand_name": brand_name,
        "api_available": bool(api_key),
        "api_error": api_error,
        "enriched_count": enriched_count,
    }

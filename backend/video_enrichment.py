"""
Video platform enrichment for citations.

Fetches view counts and thumbnails for YouTube and TikTok URLs found in
LLM probe citations, using:

- YouTube Data API v3 (``YOUTUBE_API_KEY`` or ``GOOGLE_API_KEY``)
- TikTok oEmbed (public, no auth required — thumbnail + title only)

Gracefully no-ops when keys are missing or requests fail.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import parse_qs, urlparse

from competitor_suggest import _get_config, _https_ssl_context


# ── Key helpers ───────────────────────────────────────────────────────────────

def _youtube_api_key() -> str:
    return (
        _get_config("YOUTUBE_API_KEY")
        or _get_config("GOOGLE_API_KEY")
        or _get_config("GOOGLE_GENAI_API_KEY")
        or ""
    ).strip()


# ── YouTube ───────────────────────────────────────────────────────────────────

_YT_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*v=|embed/|v/|shorts/)|youtu\.be/)([A-Za-z0-9_\-]{11})"
)


def _youtube_video_id(url: str) -> str | None:
    m = _YT_VIDEO_ID_RE.search(url or "")
    return m.group(1) if m else None


def _youtube_enrichment(video_id: str, api_key: str) -> dict[str, Any]:
    """Call YouTube Data API v3 for snippet + statistics."""
    url = (
        f"https://www.googleapis.com/youtube/v3/videos"
        f"?part=snippet,statistics&id={video_id}&key={api_key}"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=_https_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}
    items = data.get("items") or []
    if not items:
        return {}
    item = items[0]
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    thumbs = snippet.get("thumbnails") or {}
    thumb = (
        (thumbs.get("medium") or thumbs.get("default") or thumbs.get("high") or {})
        .get("url") or ""
    )
    views_raw = stats.get("viewCount")
    views = int(views_raw) if views_raw and str(views_raw).isdigit() else None
    return {
        "title": str(snippet.get("title") or "").strip(),
        "thumbnail_url": thumb,
        "views": views,
        "platform": "youtube",
    }


# ── TikTok ────────────────────────────────────────────────────────────────────

def _is_tiktok_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return "tiktok.com" in host


def _tiktok_enrichment(url: str) -> dict[str, Any]:
    """Use TikTok oEmbed (public endpoint) to get title + thumbnail."""
    oembed_url = f"https://www.tiktok.com/oembed?url={urllib.request.quote(url, safe='')}"
    req = urllib.request.Request(oembed_url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=_https_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}
    return {
        "title": str(data.get("title") or "").strip(),
        "thumbnail_url": str(data.get("thumbnail_url") or "").strip(),
        "views": None,  # oEmbed does not expose view counts
        "platform": "tiktok",
    }


# ── Public interface ──────────────────────────────────────────────────────────

def enrich_citation(citation: dict[str, Any]) -> dict[str, Any]:
    """
    Add ``title``, ``thumbnail_url``, ``views``, and ``platform`` to a citation dict
    if the URL is a YouTube or TikTok resource. Returns the citation (mutated in place).
    """
    url = str(citation.get("url") or "")
    if not url:
        return citation

    # Already enriched
    if citation.get("platform"):
        return citation

    yt_id = _youtube_video_id(url)
    if yt_id:
        key = _youtube_api_key()
        if key:
            info = _youtube_enrichment(yt_id, key)
            if info:
                citation.update(info)
        else:
            citation["platform"] = "youtube"
        return citation

    if _is_tiktok_url(url):
        info = _tiktok_enrichment(url)
        if info:
            citation.update(info)
        else:
            citation["platform"] = "tiktok"
        return citation

    return citation


def _enrich_video_fields(target: dict[str, Any], url: str) -> None:
    """Enrich a dict in-place with video metadata if the URL is YouTube/TikTok."""
    stub: dict[str, Any] = {"url": url}
    enrich_citation(stub)
    for key in ("platform", "thumbnail_url", "title", "views"):
        if stub.get(key) is not None and not target.get(key):
            target[key] = stub[key]


def enrich_citations_in_probe(live: dict[str, Any]) -> dict[str, Any]:
    """
    Walk all citation lists in a live probe result and enrich any YouTube / TikTok
    URLs with view counts and thumbnails in place.
    """
    per = live.get("per_prompt") or []
    for row in per:
        if not isinstance(row, dict):
            continue
        for platform in ("gemini", "openai", "claude"):
            citations = row.get(f"citations_{platform}") or []
            for c in citations:
                if isinstance(c, dict):
                    enrich_citation(c)

    for site in live.get("top_cited_sites") or []:
        if isinstance(site, dict):
            _enrich_video_fields(site, site.get("example_url") or f"https://{site.get('domain', '')}")

    for entry in live.get("top_cited_urls") or []:
        if isinstance(entry, dict):
            _enrich_video_fields(entry, entry.get("url") or f"https://{entry.get('domain', '')}")

    return live


def enrich_aio_probe(aio: dict[str, Any]) -> dict[str, Any]:
    """Same enrichment pass for the AIO probe result (different structure)."""
    for row in aio.get("per_prompt") or []:
        if not isinstance(row, dict):
            continue
        for c in row.get("citations") or []:
            if isinstance(c, dict):
                enrich_citation(c)

    for site in aio.get("top_cited_sites") or []:
        if not isinstance(site, dict) or site.get("platform"):
            continue
        url = site.get("example_url") or f"https://{site.get('domain', '')}"
        stub = {"url": url}
        enrich_citation(stub)
        if stub.get("platform"):
            site["platform"] = stub["platform"]
        if stub.get("thumbnail_url"):
            site["thumbnail_url"] = stub["thumbnail_url"]
        if stub.get("title") and not site.get("title"):
            site["title"] = stub["title"]
        if stub.get("views") is not None and site.get("views") is None:
            site["views"] = stub["views"]

    return aio

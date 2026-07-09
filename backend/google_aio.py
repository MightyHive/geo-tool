"""
Google AI Overview probe using Gemini with Google Search grounding.

Calls Gemini with the ``google_search`` tool enabled so responses are grounded
in live Google Search results. The ``groundingMetadata`` in the response contains
the web sources Gemini cited — functionally equivalent to Google AI Overview citations.

Requires ``GEMINI_API_KEY`` (or Vertex ADC) with the Gemini API enabled.
No SerpAPI or Custom Search key needed.
"""

from __future__ import annotations

import concurrent.futures
import http.client
import json
import ssl
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse as _urlparse

from competitor_suggest import (
    _default_model_google_ai,
    _gemini_api_key,
    _get_config,
    _https_ssl_context,
    _truthy_env,
)
from geo_market import resolve_primary_market

# Grounded search requires a model that supports the google_search tool.
# gemini-2.0-flash and gemini-2.5-flash support it; fall back to the default
# model if those aren't accessible. Vertex grounding has slightly different syntax
# so we only support API-key path here for now.
_GROUNDED_MODELS_PREFERENCE = [
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
]

_AIO_SYSTEM_INSTRUCTION_BASE = (
    "You are a helpful consumer-facing assistant with access to Google Search. "
    "You MUST use Google Search to look up current information before answering. "
    "Answer the user's question directly and concisely. "
    "Your answer MUST reference specific websites, brands, or sources found via search. "
    "Aim for about 150-300 words."
)


def _aio_system_instruction(market_country: str = "", market_country_code: str = "") -> str:
    """Build the AIO system instruction, injecting market context from the wizard when available."""
    mc, mid = resolve_primary_market(market_country, market_country_code)
    base = _AIO_SYSTEM_INSTRUCTION_BASE
    if not mc and not mid:
        return base
    geo = mc or mid
    return (
        base
        + f" The user is based in {geo}. Prioritise sources, brands, retailers, products,"
        " prices, and recommendations that are relevant to that market."
        " Use local spelling, currency, and brand names appropriate for that country."
    )


def _grounded_model() -> str:
    configured = (_get_config("GEMINI_GROUNDED_MODEL") or "").strip()
    if configured:
        return configured
    # Try to use a model known to support google_search tool; fall back to
    # the standard default if not explicitly configured.
    return _GROUNDED_MODELS_PREFERENCE[0]


def _gemini_grounded_request(
    api_key: str,
    model: str,
    user_text: str,
    system_instruction: str = "",
) -> dict[str, Any]:
    """Raw API call with google_search tool enabled. Returns the full response dict."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        f":generateContent?key={api_key}"
    )
    sys_text = system_instruction or _AIO_SYSTEM_INSTRUCTION_BASE
    body = json.dumps(
        {
            "contents": [
                {"role": "user", "parts": [{"text": (user_text or "").strip()[:12000]}]}
            ],
            "systemInstruction": {
                "parts": [{"text": sys_text}]
            },
            "tools": [{"google_search": {}}],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 1200,
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90, context=_https_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_text_from_response(payload: dict[str, Any]) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        return " ".join(str(p.get("text") or "") for p in parts).strip()
    except (KeyError, IndexError, TypeError):
        return ""


_GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"


def _resolve_redirect_url(url: str, timeout: int = 6) -> str:
    """Follow a Vertex AI grounding redirect URL to extract the actual source URL.

    Gemini's grounding API wraps all cited URLs behind
    ``https://vertexaisearch.cloud.google.com/grounding-api-redirect/<token>``
    redirects. A single HEAD request reveals the real destination in the
    ``Location`` response header.  Returns the original URL unchanged if
    resolution fails or the URL is not a grounding redirect.
    """
    if _GROUNDING_REDIRECT_HOST not in url:
        return url
    try:
        parsed = _urlparse(url)
        conn = http.client.HTTPSConnection(parsed.netloc, timeout=timeout)
        path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        conn.request("HEAD", path, headers={"User-Agent": "Mozilla/5.0"})
        resp = conn.getresponse()
        if resp.status in (301, 302, 303, 307, 308):
            location = resp.getheader("Location") or ""
            if location:
                conn.close()
                return location
        conn.close()
    except Exception:
        pass
    return url


def _domain_from_title(title: str) -> str:
    """Extract a best-effort display domain from a page title.

    Gemini grounding titles are often formatted as:
      "Page Title - Site Name"  or  "Page Title | Site Name"
    We grab the trailing site segment and normalise it to a lowercase slug.
    Returns an empty string when nothing useful can be extracted.
    """
    if not title:
        return ""
    for sep in (" - ", " | ", " – ", " — "):
        if sep in title:
            site = title.rsplit(sep, 1)[-1].strip()
            if site:
                # Lowercase, keep only letters/digits/dots/hyphens
                import re as _re
                clean = _re.sub(r"[^a-z0-9.\-]", "", site.lower())
                if clean:
                    return clean
    return ""


def _resolve_citations_redirects(
    citations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve all Vertex AI redirect URLs in a citation list in parallel.

    Updates ``url`` and ``domain`` in-place for any citation whose URL points to
    ``vertexaisearch.cloud.google.com``.

    If resolution succeeds, the real URL and domain are stored.
    If resolution fails (e.g. network timeout on Cloud Run), the citation is
    **kept** rather than dropped — we fall back to extracting a display domain
    from the ``title`` field so the citation is still visible to the user.
    The original redirect URL remains as ``url`` and stays clickable.
    """
    redirect_indices = [
        i
        for i, c in enumerate(citations)
        if _GROUNDING_REDIRECT_HOST in (c.get("url") or "")
    ]
    if not redirect_indices:
        return citations

    def _resolve_one(i: int) -> tuple[int, str]:
        return i, _resolve_redirect_url(citations[i]["url"])

    max_workers = min(12, len(redirect_indices))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(_resolve_one, redirect_indices))

    for i, resolved_url in results:
        if resolved_url != citations[i]["url"]:
            # Successfully resolved — use the real URL and domain.
            citations[i]["url"] = resolved_url
            try:
                domain = (
                    (_urlparse(resolved_url).hostname or "").lower().removeprefix("www.")
                )
                citations[i]["domain"] = domain
            except Exception:
                pass
        else:
            # Resolution failed — fall back to a title-derived display domain.
            title_domain = _domain_from_title(citations[i].get("title") or "")
            if title_domain:
                citations[i]["domain"] = title_domain
            # Keep url as the (still-clickable) redirect URL.
            # Mark so frontends can render a note if desired.
            citations[i]["unresolved_redirect"] = True

    # Never drop citations — even unresolved redirect URLs are useful because:
    # (a) the title field identifies the source, and (b) the URL is still
    # clickable and the browser will follow the redirect to the actual page.
    return citations


def _extract_grounding_citations(payload: dict[str, Any]) -> list[dict[str, str]]:
    """
    Extract ``groundingChunks`` from the response metadata.

    Each chunk has ``{"web": {"uri": "...", "title": "..."}}`` structure.
    Returns a deduplicated list of ``{"url": ..., "domain": ..., "title": ...}``.
    Redirect URLs (``vertexaisearch.cloud.google.com``) are resolved in parallel
    to their actual destination before deduplication.
    """
    try:
        meta = payload["candidates"][0].get("groundingMetadata") or {}
    except (KeyError, IndexError, TypeError):
        return []

    chunks = meta.get("groundingChunks") or []
    raw: list[dict[str, str]] = []
    for chunk in chunks:
        web = chunk.get("web") or {}
        uri = str(web.get("uri") or "").strip()
        title = str(web.get("title") or "").strip()
        if not uri:
            continue
        try:
            domain = (_urlparse(uri).hostname or "").lower().removeprefix("www.")
        except Exception:
            domain = ""
        if not domain:
            continue
        raw.append({"url": uri, "domain": domain, "title": title})

    # Resolve any Vertex AI redirect wrappers → actual URLs.
    resolved = _resolve_citations_redirects(raw)

    # Deduplicate by domain (keep first occurrence per domain).
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for c in resolved:
        if c["domain"] in seen:
            continue
        seen.add(c["domain"])
        out.append(c)
    return out


def gemini_grounded_answer(
    user_prompt: str,
    *,
    api_key: str | None = None,
    market_country: str = "",
    market_country_code: str = "",
) -> dict[str, Any]:
    """
    Run a single prompt through Gemini with Google Search grounding.

    Returns::

        {
          "response": str,          # Gemini's grounded text answer
          "citations": [            # grounded sources from Google Search
            {"url": str, "domain": str, "title": str}, ...
          ],
          "error": str | None,
        }
    """
    key = (api_key or _gemini_api_key() or "").strip()
    if not key:
        return {
            "response": "",
            "citations": [],
            "error": "GEMINI_API_KEY not configured — Google AIO probes unavailable.",
        }
    model = _grounded_model()
    sys_instr = _aio_system_instruction(market_country=market_country, market_country_code=market_country_code)
    try:
        payload = _gemini_grounded_request(
            api_key=key, model=model, user_text=user_prompt, system_instruction=sys_instr
        )
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:800]
        return {"response": "", "citations": [], "error": f"Gemini HTTP {e.code}: {detail}"}
    except Exception as e:
        return {"response": "", "citations": [], "error": str(e)[:400]}

    return {
        "response": _extract_text_from_response(payload),
        "citations": _extract_grounding_citations(payload),
        "error": None,
    }


def run_aio_probes(
    prompts: list[str],
    *,
    brand_site_url: str = "",
    max_prompts: int = 25,
    market_country: str = "",
    market_country_code: str = "",
) -> dict[str, Any]:
    """
    Run Google AIO probes for a list of prompts.

    Returns the full AIO probe result::

        {
          "per_prompt": [
            {
              "index": int,
              "prompt": str,
              "response": str,
              "citations": [{"url", "domain", "title"}, ...],
              "error": str | None,
            }, ...
          ],
          "top_cited_sites": [
            {"domain", "count", "example_url", "title", "platforms": ["google_aio"]}, ...
          ],
          "available": True,
          "error": None,
        }
    """
    key = _gemini_api_key()
    if not key:
        return {
            "per_prompt": [],
            "top_cited_sites": [],
            "available": False,
            "error": "GEMINI_API_KEY not configured — Google AIO probes unavailable.",
        }

    used = [p.strip() for p in prompts if p and str(p).strip()][:max(1, min(max_prompts, 80))]

    brand_domain = ""
    if brand_site_url:
        try:
            brand_domain = (_urlparse(brand_site_url).hostname or "").lower().removeprefix("www.")
        except Exception:
            pass

    per_prompt: list[dict[str, Any]] = []
    domain_counts: dict[str, dict[str, Any]] = {}

    for i, prompt in enumerate(used, start=1):
        result = gemini_grounded_answer(
            prompt,
            api_key=key,
            market_country=market_country,
            market_country_code=market_country_code,
        )
        citations = [c for c in result.get("citations") or [] if c.get("domain") != brand_domain]
        per_prompt.append(
            {
                "index": i,
                "prompt": prompt,
                "response": result.get("response") or "",
                "citations": citations,
                "error": result.get("error"),
            }
        )
        for c in citations:
            d = c["domain"]
            if d not in domain_counts:
                domain_counts[d] = {
                    "domain": d,
                    "count": 0,
                    "example_url": c["url"],
                    "title": c.get("title") or "",
                    "platforms": ["google_aio"],
                }
            domain_counts[d]["count"] += 1

    top_sites = sorted(domain_counts.values(), key=lambda x: -x["count"])[:20]

    return {
        "per_prompt": per_prompt,
        "top_cited_sites": top_sites,
        "available": True,
        "error": None,
    }

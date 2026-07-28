"""
AI search **prompt recommendations**: likely user queries from setup context, **live Gemini + OpenAI answers**
per query, **mention-based %** for brand vs competitors in each reply, and **on-site content actions** for weak prompts
(using **live probe** aggregates only).

Uses the same Gemini auth as :mod:`competitor_suggest` (``GEMINI_API_KEY`` / Vertex).
OpenAI live answers use ``OPENAI_API_KEY`` (environment or ``secrets.toml`` via :func:`competitor_suggest._get_config`).

**Live probes** call real APIs (usage billed to your keys); mention counts are heuristic substring matches.
"""

from __future__ import annotations

import concurrent.futures
import html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from contextlib import nullcontext
from typing import Any
from urllib.parse import quote as _url_quote
from urllib.parse import urlparse

from geo_market import resolve_primary_market
from onboarding_suggestions import infer_categories

# Reuse Gemini transport from competitor_suggest (same credentials / model env).
from competitor_suggest import (
    _default_model_google_ai,
    _default_model_vertex,
    _gemini_api_key,
    _generate_via_api_key,
    _generate_via_vertex,
    _get_config,
    _https_ssl_context,
    _strip_json_fence,
    _truthy_env,
)
from google_aio import (
    _resolve_citations_redirects as _resolve_grounding_redirects,
    gemini_grounded_answer as _aio_grounded_answer,
)

log = logging.getLogger(__name__)

# All platforms handled by live probes (order determines display order in UI).
_LIVE_PLATFORMS: tuple[str, ...] = ("gemini", "openai", "claude", "google_aio")

# Gemini chat + Google AIO share GEMINI_API_KEY — serialize those two under one lock.
_GEMINI_FAMILY = frozenset({"gemini", "google_aio"})


def _parallel_platform_probes_enabled() -> bool:
    raw = (os.getenv("PROMPT_PROBE_PARALLEL") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


# GA4 country → phrase shoppers literally type (reduces US-default bias in live probes).
_GEO_ISO2_LOCATOR_PHRASE: dict[str, str] = {
    "GB": "in the UK",
    "UK": "in the UK",
    "IE": "in Ireland",
    "US": "in the US",
    "CA": "in Canada",
    "AU": "in Australia",
    "NZ": "in New Zealand",
    "DE": "in Germany",
    "AT": "in Austria",
    "CH": "in Switzerland",
    "FR": "in France",
    "BE": "in Belgium",
    "LU": "in Luxembourg",
    "NL": "in the Netherlands",
    "ES": "in Spain",
    "PT": "in Portugal",
    "IT": "in Italy",
    "PL": "in Poland",
    "SE": "in Sweden",
    "NO": "in Norway",
    "DK": "in Denmark",
    "FI": "in Finland",
    "AE": "in the UAE",
    "SA": "in Saudi Arabia",
    "IN": "in India",
    "SG": "in Singapore",
    "JP": "in Japan",
    "KR": "in South Korea",
    "PH": "in the Philippines",
    "MY": "in Malaysia",
    "TH": "in Thailand",
    "ID": "in Indonesia",
    "VN": "in Vietnam",
    "ZA": "in South Africa",
    "BR": "in Brazil",
    "MX": "in Mexico",
    "AR": "in Argentina",
}

_GEO_COUNTRY_NAME_LOCATOR_PHRASE: dict[str, str] = {
    "united kingdom": "in the UK",
    "great britain": "in the UK",
    "ireland": "in Ireland",
    "united states": "in the US",
    "united states of america": "in the US",
    "canada": "in Canada",
    "australia": "in Australia",
    "new zealand": "in New Zealand",
    "germany": "in Germany",
    "austria": "in Austria",
    "switzerland": "in Switzerland",
    "france": "in France",
    "belgium": "in Belgium",
    "luxembourg": "in Luxembourg",
    "united arab emirates": "in the UAE",
    "the netherlands": "in the Netherlands",
    "netherlands": "in the Netherlands",
    "spain": "in Spain",
    "portugal": "in Portugal",
    "italy": "in Italy",
    "poland": "in Poland",
    "sweden": "in Sweden",
    "norway": "in Norway",
    "denmark": "in Denmark",
    "finland": "in Finland",
    "saudi arabia": "in Saudi Arabia",
    "india": "in India",
    "singapore": "in Singapore",
    "japan": "in Japan",
    "south korea": "in South Korea",
    "korea, republic of": "in South Korea",
    "philippines": "in the Philippines",
    "malaysia": "in Malaysia",
    "thailand": "in Thailand",
    "indonesia": "in Indonesia",
    "vietnam": "in Vietnam",
    "south africa": "in South Africa",
    "brazil": "in Brazil",
    "mexico": "in Mexico",
    "argentina": "in Argentina",
}


def geo_locator_phrase_for_market(country: str, country_id: str) -> str:
    """
    English geographic locator to embed in **user-style** prompts (e.g. ``in the UK``, ``in Germany``).
    Uses ISO 3166-1 alpha-2 when present, else normalised country name.
    """
    cid = (country_id or "").strip().upper()
    if cid and cid in _GEO_ISO2_LOCATOR_PHRASE:
        return _GEO_ISO2_LOCATOR_PHRASE[cid]
    name = (country or "").strip()
    if not name:
        return ""
    key = name.lower()
    if key in _GEO_COUNTRY_NAME_LOCATOR_PHRASE:
        return _GEO_COUNTRY_NAME_LOCATOR_PHRASE[key]
    return f"in {name}"


def ensure_prompt_contains_geo_locator(text: str, phrase: str) -> str:
    """If ``phrase`` is set and missing from ``text`` (case-insensitive), insert it before a final ``?``. ``!`` or ``.``, else append."""
    t = (text or "").strip()
    p = (phrase or "").strip()
    if not t or not p:
        return t
    if re.search(re.escape(p), t, flags=re.IGNORECASE):
        return t
    ts = t.rstrip()
    for punct in ("?", "!", "."):
        if ts.endswith(punct):
            core = ts[:-1].rstrip()
            return f"{core} {p}{punct}"
    return f"{ts} {p}"


def infer_category_labels_from_top_pages(
    top_pages: list[dict[str, Any]],
    *,
    max_categories: int = 20,
    selected_industry: str = "",
) -> list[str]:
    """Short labels for offerings, from :func:`onboarding_suggestions.infer_categories`."""
    rows = infer_categories(top_pages, max_items=max_categories, selected_industry=selected_industry)
    return [str(c.get("label") or "").strip() for c in rows if c.get("label")]


def _gemini_generate(*, system_instruction: str, user_text: str) -> str:
    api_key = _gemini_api_key()
    use_vertex = _truthy_env("GEMINI_USE_VERTEX_AI")
    project = (_get_config("GOOGLE_CLOUD_PROJECT") or "").strip()
    location = (_get_config("GOOGLE_CLOUD_LOCATION") or "europe-west1").strip()

    if api_key:
        model = _default_model_google_ai()
        return _generate_via_api_key(
            api_key=api_key,
            model=model,
            system_instruction=system_instruction,
            user_text=user_text,
        )
    if use_vertex and project:
        model = _default_model_vertex()
        return _generate_via_vertex(
            project=project,
            location=location,
            model=model,
            system_instruction=system_instruction,
            user_text=user_text,
        )
    raise ValueError(
        "Configure Gemini: **GEMINI_API_KEY** or **GOOGLE_API_KEY**, or **GEMINI_USE_VERTEX_AI=1** with "
        "**GOOGLE_CLOUD_PROJECT** and ADC."
    )


def suggest_ai_platform_prompts(
    category_labels: list[str],
    *,
    brand_name: str,
    site_url: str = "",
    industry: str = "",
    max_prompts: int = 10,
    market_country: str = "",
    market_country_code: str = "",
) -> list[str]:
    """
    Ask Gemini for natural-language queries users might type into ChatGPT / Perplexity / Gemini
    where the brand could plausibly appear in answers.
    """
    cats = [c.strip() for c in category_labels if c and str(c).strip()]
    brand = (brand_name or "").strip()
    if not brand:
        raise ValueError("Brand name is required for prompt suggestions.")
    if not cats:
        raise ValueError("Provide at least one category or offering (from GA4 top pages).")

    mc, mid = resolve_primary_market(market_country, market_country_code)
    phrase = geo_locator_phrase_for_market(mc, mid)
    market_rules = ""
    if phrase:
        phrase_js = json.dumps(phrase)
        geo_ctx = (f"{mc}" + (f" (`{mid}`)" if mid else "")) if mc else (f"ISO `{mid}`" if mid else "primary market")
        market_rules = (
            f"Primary market: {geo_ctx}. "
            f"Every string in `prompts` MUST contain the contiguous phrase {phrase_js} exactly (match spacing and casing; "
            "case-insensitive match is acceptable). Place it naturally, usually before the final question mark—"
            f'example: "Where can I buy brake pads for my Ford Focus {phrase}?". '
            "Do not substitute a different country or region. Do not rely on implied geography."
        )

    system = (
        "You help with generative-engine marketing. Reply with a single JSON object only, no markdown fences. "
        f'Schema: {{"prompts": ["plain user query", ...]}}. '
        f"Exactly {max_prompts} distinct prompts. Each prompt should be something a real shopper or DIY user "
        "would type into an AI assistant (not keyword-stuffed). Prompts should be likely to surface brands in this "
        "space as recommendations, comparisons, or buying advice."
    )
    if market_rules:
        system += " " + market_rules
    elif mc or mid:
        system += (
            f" Primary audience geography: **{mc}**" + (f" (`{mid}`)" if mid else "") + ". "
            "Use retailers, chains, spelling, and buying context appropriate to that market; do not default to US-only examples."
        )
    else:
        system += (
            " No explicit primary country is configured—avoid US-default store names, chains, and dollar pricing unless "
            "the site URL or categories clearly imply the US; prefer neutral or globally plausible examples."
        )

    user_payload: dict[str, Any] = {
        "brand": brand,
        "site": site_url or None,
        "industry": (industry or "").strip() or None,
        "categories_or_offerings": cats,
        "primary_market_country": mc or None,
        "primary_market_country_code": mid or None,
        "required_geo_locator_phrase": phrase or None,
        "task": (
            "Given these categories, list the 10 most likely prompts a user would enter into an AI platform "
            "that would surface this brand as a plausible result or citation. "
            + (
                f"When required_geo_locator_phrase is set, each prompt MUST include that exact substring verbatim."
                if phrase
                else ""
            )
        ),
    }
    user = json.dumps(user_payload, ensure_ascii=False)

    raw = _gemini_generate(system_instruction=system, user_text=user)
    try:
        obj = json.loads(_strip_json_fence(str(raw)))
    except json.JSONDecodeError as e:
        raise ValueError(f"Model did not return valid JSON: {raw[:600]!r}") from e

    prompts_raw = obj.get("prompts")
    if not isinstance(prompts_raw, list):
        raise ValueError('Expected JSON with a "prompts" array.')

    out: list[str] = []
    seen: set[str] = set()
    for p in prompts_raw:
        if isinstance(p, dict) and "text" in p:
            p = p.get("text")
        if not isinstance(p, str):
            continue
        s = re.sub(r"\s+", " ", p.strip())
        if phrase:
            s = ensure_prompt_contains_geo_locator(s, phrase)
        if len(s) < 8:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= max_prompts:
            break

    if not out:
        raise ValueError("Gemini returned no usable prompts.")
    return out


def _host_label(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        u = "https://" + u
    return (urlparse(u).hostname or "").replace("www.", "") or u


def suggest_content_for_weak_prompts(
    prompts: list[str],
    *,
    live_aggregate: dict[str, Any],
    brand_name: str,
    brand_site_url: str,
    competitor_urls: list[str],
    category_labels: list[str] | None = None,
    max_weak_prompts: int = 5,
    market_country: str = "",
    market_country_code: str = "",
) -> dict[str, Any]:
    """
    Review prompt-performance output (prompts + **live probe** mention totals) and identify queries where the primary
    site may trail competitors, then propose **on-site content** (pages, sections, formats).

    Returns JSON with ``weak_prompts`` (list of objects) and ``priority_summary`` (string).
    """
    brand = (brand_name or "").strip()
    if not brand:
        raise ValueError("Brand name is required.")
    ps = [p.strip() for p in prompts if p and str(p).strip()]
    if not ps:
        raise ValueError("Prompt list is required.")
    if not isinstance(live_aggregate, dict) or not live_aggregate:
        raise ValueError("Live probe aggregate results are required—run live probes first.")

    mc, mid = resolve_primary_market(market_country, market_country_code)
    phrase = geo_locator_phrase_for_market(mc, mid)
    if phrase:
        ps = [ensure_prompt_contains_geo_locator(p, phrase) for p in ps]

    primary_host = _host_label(brand_site_url)
    comp_hosts: list[str] = []
    for u in competitor_urls or []:
        h = _host_label(str(u))
        if h and h.lower() != (primary_host or "").lower():
            comp_hosts.append(h)
    comp_hosts = list(dict.fromkeys(comp_hosts))[:8]
    cats = [c.strip() for c in (category_labels or []) if c and str(c).strip()]

    system = (
        "You are a senior SEO + GEO content strategist. Reply with one JSON object only, no markdown fences.\n"
        "Schema:\n"
        "{\n"
        f'  "weak_prompts": [\n'
        "    {\n"
        '      "prompt": "<one of the supplied user prompts>",\n'
        '      "why_primary_underperforms": "<1-3 sentences: vs competitors in AI-style answers>",\n'
        '      "content_actions": [\n'
        "        {\n"
        '          "action_title": "<short name>",\n'
        '          "content_format": "<e.g. comparison hub, buying guide, FAQ, how-to, category explainer>",\n'
        '          "what_to_publish": "<2-4 sentences: concrete on-site asset to create>",\n'
        '          "outline_bullets": ["<H2/H3 idea>", "..."],\n'
        '          "differentiation_angle": "<how this should cite or feature the primary brand>",\n'
        '          "snippet_or_title_suggestions": ["<meta title or H1 idea>", "..."]\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ],\n"
        '  "priority_summary": "<80-200 words: what to ship first and why>"\n'
        "}\n"
        f"Include at most {max_weak_prompts} entries in weak_prompts—only prompts where the primary brand "
        "is plausibly **weaker than competitors** for that intent. Use the supplied **live_probe_aggregate** "
        "(substring mention-hit totals across Gemini + OpenAI replies) together with the prompt list; "
        "you are not re-querying the web. "
        "Each weak prompt should have 1-3 content_actions. "
        f'Primary brand: "{brand}". Site: "{brand_site_url or primary_host}". '
        f"Competitor hosts: {json.dumps(comp_hosts)}."
    )
    if phrase:
        system += (
            f" Supplied user_prompts are scoped to {json.dumps(phrase)} (primary market)—"
            "when you echo a prompt in weak_prompts[].prompt, copy it exactly including that phrase. "
            "Assume generative answers would target that market; do not default to US-centric framing."
        )
    elif mc or mid:
        system += (
            f" Primary audience geography: **{mc}**" + (f" ({mid})" if mid else "") + "—tailor examples accordingly."
        )
    else:
        system += (
            " No explicit primary geography is set—avoid US-default chains, spelling, and currency examples unless "
            "the site or categories clearly imply the US; prefer neutral or globally plausible framing."
        )
    user = json.dumps(
        {
            "user_prompts": ps[:25],
            "required_geo_locator_phrase": phrase or None,
            "live_probe_aggregate": live_aggregate,
            "category_context": cats or None,
        },
        ensure_ascii=False,
    )

    raw = _gemini_generate(system_instruction=system, user_text=user)
    try:
        obj = json.loads(_strip_json_fence(str(raw)))
    except json.JSONDecodeError as e:
        raise ValueError(f"Model did not return valid JSON: {raw[:1200]!r}") from e

    weak = obj.get("weak_prompts")
    if not isinstance(weak, list):
        raise ValueError('Expected JSON with a "weak_prompts" array.')

    cleaned: list[dict[str, Any]] = []
    for item in weak[:max_weak_prompts]:
        if not isinstance(item, dict):
            continue
        p = str(item.get("prompt") or "").strip()
        if not p:
            continue
        why = str(
            item.get("why_primary_underperforms")
            or item.get("performance_gap")
            or item.get("why_weak")
            or ""
        ).strip()
        cleaned.append(
            {
                "prompt": p,
                "why_primary_underperforms": why,
                "content_actions": _normalize_content_actions(item.get("content_actions")),
            }
        )

    if not cleaned:
        raise ValueError("Gemini returned no weak-prompt rows with usable content.")

    summary = str(obj.get("priority_summary") or "").strip()
    if not summary:
        summary = "Prioritise pages that answer high-intent comparison and buying questions where peers dominate."

    return {
        "weak_prompts": cleaned,
        "priority_summary": summary,
        "disclaimer": "Recommendations use your **live probe** mention totals and prompts—not a crawl of live SERPs.",
    }


def _normalize_content_actions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for a in raw[:4]:
        if not isinstance(a, dict):
            continue
        title = str(a.get("action_title") or "").strip()
        if not title:
            continue
        bullets = a.get("outline_bullets")
        if not isinstance(bullets, list):
            bullets = []
        bullets = [str(b).strip() for b in bullets if str(b).strip()][:12]
        snippets = a.get("snippet_or_title_suggestions")
        if not isinstance(snippets, list):
            snippets = []
        snippets = [str(s).strip() for s in snippets if str(s).strip()][:6]
        out.append(
            {
                "action_title": title,
                "content_format": str(a.get("content_format") or "").strip(),
                "what_to_publish": str(a.get("what_to_publish") or "").strip(),
                "outline_bullets": bullets,
                "differentiation_angle": str(a.get("differentiation_angle") or "").strip(),
                "snippet_or_title_suggestions": snippets,
            }
        )
    return out


LIVE_ASSISTANT_SYSTEM = (
    "You are a helpful consumer-facing assistant. Answer the user's question directly. "
    "When it helps the shopper, name specific retailers, brands, or websites they could consider—"
    "including their website URLs where you know them—and smaller specialists if relevant. "
    "Aim for about 150–400 words unless a shorter reply clearly suffices."
)


def live_assistant_system_instruction(
    *,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "",
    language_name: str = "",
) -> str:
    """System prompt for live Gemini/OpenAI probes, with optional primary-market + language context."""
    base = LIVE_ASSISTANT_SYSTEM
    mc, mid = resolve_primary_market(market_country, market_country_code)
    phrase = geo_locator_phrase_for_market(mc, mid)
    lang = (language or "").strip().lower()
    lang_label = (language_name or "").strip()
    if lang and lang != "en":
        if not lang_label:
            try:
                from prompt_locales import language_name as _lang_name

                lang_label = _lang_name(lang)
            except Exception:
                lang_label = lang
        base = (
            base
            + f" Answer entirely in {lang_label} ({lang}). "
            "Use natural wording for that language; keep brand and product names as commonly written locally."
        )
    if phrase:
        pj = json.dumps(phrase)
        return (
            base
            + " The user's message explicitly includes "
            + pj
            + ", so they want answers for that geography only—treat that as binding. "
            "Prefer retailers, brands, product ranges, spelling, currency cues, and chains that serve that market; "
            "do not default to US-only suggestions unless they clearly apply there too."
        )
    if not mc and not mid:
        return (
            base
            + " If the user message does not name a country, avoid assuming US-only retailers or spelling; "
            "prefer globally plausible or region-neutral answers unless context implies otherwise."
        )
    geo = mc + (f" ({mid})" if mid else "")
    return (
        base
        + " The shopper is primarily interested in options relevant to **"
        + geo
        + "** (configured primary market). Prefer retailers, brands, and wording "
        "that fit that market (spelling, currency tone, local chains where appropriate)."
    )


def _openai_api_key() -> str:
    return (
        (_get_config("OPENAI_API_KEY") or _get_config("OPEN_AI_API_KEY") or _get_config("OPENAI_KEY")).strip()
    )


def _openai_chat_model() -> str:
    return (_get_config("OPENAI_CHAT_MODEL") or "gpt-4o-mini").strip()


def _openai_search_model() -> str:
    """Model used for citation-enabled probes.

    ``gpt-4o-search-preview`` always performs a web search and returns
    structured ``url_citation`` annotations, closely matching the behaviour of
    ChatGPT with Browse enabled.  Falls back to the standard chat model if a
    custom override is configured (``OPENAI_SEARCH_MODEL``).
    """
    return (_get_config("OPENAI_SEARCH_MODEL") or "gpt-4o-search-preview").strip()


def _anthropic_api_key() -> str:
    return (_get_config("ANTHROPIC_API_KEY") or _get_config("CLAUDE_API_KEY") or "").strip()


def _claude_model() -> str:
    return (_get_config("ANTHROPIC_MODEL") or "claude-haiku-4-5-20251001").strip()


def claude_answer_user_prompt(
    user_prompt: str,
    *,
    api_key: str | None = None,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "",
    language_name: str = "",
) -> str:
    key = (api_key or _anthropic_api_key()).strip()
    if not key:
        raise ValueError(
            "Set **ANTHROPIC_API_KEY** in the environment or `.streamlit/secrets.toml` to run Claude live probes."
        )
    model = _claude_model()
    sys_instr = live_assistant_system_instruction(
        market_country=market_country,
        market_country_code=market_country_code,
        language=language,
        language_name=language_name,
    )
    body = json.dumps(
        {
            "model": model,
            "max_tokens": 1800,
            "system": sys_instr,
            "messages": [
                {"role": "user", "content": (user_prompt or "").strip()[:12000]},
            ],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120, context=_https_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:1200]
        raise ValueError(f"Anthropic HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise ValueError(f"Anthropic request failed: {e}") from e
    try:
        content = payload.get("content") or []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text") or "").strip()
        raise ValueError(f"No text block in Anthropic response: {payload!r}")
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"Unexpected Anthropic response: {payload!r}") from e


def openai_chat_answer(
    user_prompt: str,
    *,
    api_key: str | None = None,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "",
    language_name: str = "",
) -> str:
    key = (api_key or _openai_api_key()).strip()
    if not key:
        raise ValueError(
            "Set **OPENAI_API_KEY** in the environment or `.streamlit/secrets.toml` to run OpenAI live probes."
        )
    model = _openai_chat_model()
    sys_instr = live_assistant_system_instruction(
        market_country=market_country,
        market_country_code=market_country_code,
        language=language,
        language_name=language_name,
    )
    body = json.dumps(
        {
            "model": model,
            "temperature": 0.4,
            "max_tokens": 1800,
            "messages": [
                {"role": "system", "content": sys_instr},
                {"role": "user", "content": (user_prompt or "").strip()[:12000]},
            ],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120, context=_https_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:1200]
        raise ValueError(f"OpenAI HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise ValueError(f"OpenAI request failed: {e}") from e
    try:
        return str(payload["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"Unexpected OpenAI response: {payload!r}") from e


def openai_answer_with_citations(
    user_prompt: str,
    *,
    api_key: str | None = None,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "",
    language_name: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """
    Call the OpenAI Responses API with the ``web_search_preview`` tool enabled.

    Returns ``(response_text, citations)`` where citations are structured
    ``url_citation`` annotations returned directly by the API — far more reliable
    than regex extraction on plain text.  Falls back to ``openai_chat_answer``
    (no citations) if the Responses API endpoint is unavailable or returns an error.
    """
    key = (api_key or _openai_api_key()).strip()
    if not key:
        raise ValueError(
            "Set **OPENAI_API_KEY** in the environment to run OpenAI live probes."
        )
    # gpt-4o-search-preview always performs a web search and returns structured
    # url_citation annotations — much closer to consumer ChatGPT with Browse than
    # gpt-4o-mini with an optional web_search_preview tool.
    model = _openai_search_model()
    sys_instr = live_assistant_system_instruction(
        market_country=market_country,
        market_country_code=market_country_code,
        language=language,
        language_name=language_name,
    )
    body = json.dumps(
        {
            "model": model,
            "tools": [{"type": "web_search_preview"}],
            "max_output_tokens": 1800,
            "input": [
                {"role": "system", "content": sys_instr},
                {"role": "user", "content": (user_prompt or "").strip()[:12000]},
            ],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120, context=_https_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:600]
        if e.code in (400, 404, 422):
            # Responses API not available for this model/key — fall back silently
            text = openai_chat_answer(
                user_prompt,
                api_key=api_key,
                market_country=market_country,
                market_country_code=market_country_code,
                language=language,
                language_name=language_name,
            )
            return text, []
        raise ValueError(f"OpenAI HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise ValueError(f"OpenAI request failed: {e}") from e

    # --- Parse Responses API output ------------------------------------------
    text = ""
    citations: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if block.get("type") != "output_text":
                continue
            text = str(block.get("text") or "")
            for ann in block.get("annotations") or []:
                if ann.get("type") != "url_citation":
                    continue
                url = str(ann.get("url") or "").strip()
                title = str(ann.get("title") or "").strip()
                if not url:
                    continue
                _, domain = _normalise_citation_url(url)
                if not domain:
                    continue
                url_key = url.lower().rstrip("/")
                if url_key in seen_keys:
                    continue
                seen_keys.add(url_key)
                content_type, channel_type = _classify_citation(domain)
                citations.append(
                    {
                        "url": url,
                        "domain": domain,
                        "title": title,
                        "content_type": content_type,
                        "channel_type": channel_type,
                    }
                )
    return text.strip(), citations


import re as _re

_GEMINI_ARTIFACT_PATTERNS = [
    # Self-correction blocks (e.g. "*Self-Correction on URLs:*")
    _re.compile(r"\*Self[-\s]?Correction[^*]*\*.*$", _re.DOTALL | _re.IGNORECASE),
    # Internal revision notes: lines starting with "Let me revise…", "I should…", etc.
    _re.compile(r"\n\s*(?:Let me (?:revise|reconsider|re-?write)|I should (?:use|note|avoid)|Revision:|Correcting:|Actually,?\s+let me|Wait,?\s+I).*$", _re.DOTALL | _re.IGNORECASE),
    # Stray trailing parenthesis/bracket lines (artifact of truncated template)
    _re.compile(r"\n\s*[)}\]]+\s*$"),
]


def _strip_gemini_artifacts(text: str) -> str:
    """Remove common Gemini self-correction and internal-monologue artefacts."""
    for pattern in _GEMINI_ARTIFACT_PATTERNS:
        text = pattern.sub("", text)
    return text.strip()


def gemini_answer_with_citations(
    user_prompt: str,
    *,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "",
    language_name: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """
    Call Gemini with ``google_search`` grounding enabled so we get real web citations
    rather than text-only training-data answers.

    Returns ``(response_text, citations)`` sourced from ``groundingMetadata.groundingChunks``.
    Falls back to ``gemini_answer_user_prompt`` (no structured citations) when running on
    Vertex AI or when the grounding call itself fails.
    """
    api_key = _gemini_api_key()
    if not api_key:
        # Vertex path — grounding requires a different setup; skip for now
        text = gemini_answer_user_prompt(
            user_prompt,
            market_country=market_country,
            market_country_code=market_country_code,
            language=language,
            language_name=language_name,
        )
        return text, []

    model = _default_model_google_ai()
    sys_instr = live_assistant_system_instruction(
        market_country=market_country,
        market_country_code=market_country_code,
        language=language,
        language_name=language_name,
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{_url_quote(model, safe='')}:generateContent?key={api_key}"
    )
    body = json.dumps(
        {
            "systemInstruction": {"parts": [{"text": sys_instr}]},
            "contents": [
                {"role": "user", "parts": [{"text": (user_prompt or "").strip()[:12000]}]}
            ],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120, context=_https_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        # On any failure fall back to plain (non-grounded) answer
        text = gemini_answer_user_prompt(
            user_prompt,
            market_country=market_country,
            market_country_code=market_country_code,
        )
        return text, []

    # --- Extract response text -----------------------------------------------
    cands = payload.get("candidates") or []
    if not cands:
        return "", []
    content = cands[0].get("content") or {}
    parts = content.get("parts") or []
    text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict)).strip()
    text = _strip_gemini_artifacts(text)

    # --- Extract grounding citations ------------------------------------------
    # Collect raw citations from groundingChunks first, then resolve any
    # vertexaisearch.cloud.google.com redirect wrappers to the real URLs.
    meta = cands[0].get("groundingMetadata") or {}
    raw_grounding: list[dict[str, Any]] = []
    for chunk in (meta.get("groundingChunks") or []):
        web = chunk.get("web") or {}
        uri = str(web.get("uri") or "").strip()
        title = str(web.get("title") or "").strip()
        if not uri:
            continue
        _, domain = _normalise_citation_url(uri)
        if not domain:
            continue
        raw_grounding.append({"url": uri, "domain": domain, "title": title})

    # Resolve Vertex AI redirect wrappers → actual URLs (parallel HTTP HEAD).
    resolved_grounding = _resolve_grounding_redirects(raw_grounding)

    citations: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for item in resolved_grounding:
        uri = item["url"]
        domain = item["domain"]
        title = item.get("title") or ""
        url_key = uri.lower().rstrip("/")
        if url_key in seen_keys:
            continue
        seen_keys.add(url_key)
        content_type, channel_type = _classify_citation(domain)
        citations.append(
            {
                "url": uri,
                "domain": domain,
                "title": title,
                "content_type": content_type,
                "channel_type": channel_type,
            }
        )
    return text, citations


def _merge_citations(
    api_citations: list[dict[str, Any]],
    response_text: str,
    brand_site_url: str,
    brand_tokens: list[str] | None = None,
    competitor_tokens: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Keep only legitimate platform source citations.

    Structured API citations (grounding chunks / url_citation annotations) are the
    source of truth — these are what the platform referenced to answer.

    Do **not** append URLs/domains merely mentioned in the answer text. Those are
    typically recommendations ("try rival.com"), not cited sources, and would
    flood Citations with every recommended competitor.

    Fallback: when the platform returns no structured citations (e.g. Claude),
    extract full ``https?://`` URLs only — never bare domains.
    """
    merged: list[dict[str, Any]] = []
    for c in api_citations:
        if not isinstance(c, dict):
            continue
        item = dict(c)
        item.setdefault("origin", "api")
        merged.append(item)

    if not merged and response_text:
        for c in extract_citations_from_reply(
            response_text,
            brand_site_url,
            include_bare_domains=False,
        ):
            item = dict(c)
            item["origin"] = "text"
            merged.append(item)

    if brand_tokens is not None and competitor_tokens is not None and response_text:
        from citation_context import enrich_citations_with_context
        enrich_citations_with_context(merged, response_text, brand_tokens, competitor_tokens)

    return merged


def gemini_answer_user_prompt(
    user_prompt: str,
    *,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "",
    language_name: str = "",
) -> str:
    """Assistant-style completion (plain text, not JSON)."""
    up = (user_prompt or "").strip()[:12000]
    if not up:
        raise ValueError("Empty prompt.")
    sys_instr = live_assistant_system_instruction(
        market_country=market_country,
        market_country_code=market_country_code,
        language=language,
        language_name=language_name,
    )
    return _strip_gemini_artifacts(_gemini_generate(system_instruction=sys_instr, user_text=up))


def _competitor_match_tokens(
    competitor_urls: list[str],
    competitor_brands: list[str] | None = None,
    *,
    primary_brand: str = "",
    reply_detected_brands: list[str] | None = None,
) -> list[str]:
    """Host-derived tokens plus optional wizard competitor brand names (for mention scan)."""
    tokens: list[str] = []
    seen: set[str] = set()
    pb = (primary_brand or "").strip().lower()
    urls = [str(u).strip() for u in (competitor_urls or []) if str(u).strip()]
    brands = list(competitor_brands or [])
    while len(brands) < len(urls):
        brands.append("")
    brands = brands[: len(urls)]
    for raw, bnam in zip(urls, brands, strict=False):
        h = _host_label(str(raw))
        if h:
            hl = h.lower()
            if hl not in seen and len(hl) >= 3:
                seen.add(hl)
                tokens.append(h)
            base = h.split(".")[0]
            if base and len(base) >= 3 and base.lower() not in seen:
                seen.add(base.lower())
                tokens.append(base)
        bn = (str(bnam) or "").strip()
        if len(bn) >= 2 and bn.lower() != pb and bn.lower() not in seen:
            seen.add(bn.lower())
            tokens.append(bn)
    for extra in reply_detected_brands or []:
        bn = str(extra or "").strip()
        if len(bn) >= 2 and bn.lower() != pb and bn.lower() not in seen:
            seen.add(bn.lower())
            tokens.append(bn)
    return tokens


def count_mentions_ci(text: str, needle: str) -> int:
    if not text or not needle or len(needle.strip()) < 2:
        return 0
    # Entity mentions must be token-bounded. Plain substring matching made short
    # false candidates such as "for" match inside "formula" and "format".
    escaped = re.escape(needle.strip())
    return len(re.findall(rf"(?<!\w){escaped}(?!\w)", text, flags=re.IGNORECASE))


_PATH_SEGMENT_BLOCKLIST = frozenset(
    {
        "en",
        "us",
        "uk",
        "gb",
        "eu",
        "shop",
        "store",
        "products",
        "product",
        "category",
        "categories",
        "collections",
        "collection",
        "pages",
        "page",
        "search",
        "blog",
        "news",
        "about",
        "contact",
        "help",
        "account",
        "cart",
        "checkout",
        "home",
        "index",
        "html",
        "www",
        "skin",
        "face",
        "body",
        "care",
        "cream",
        "serum",
        "lotion",
        "cleanser",
        "moisturizer",
        "sunscreen",
        "treatment",
        "routine",
        "guide",
        "best",
        "review",
        "reviews",
    }
)


def _normalize_brand_key(value: str) -> str:
    return re.sub(r"[\s\-.'']+", "", (value or "").lower())


def _brand_flexible_pattern(brand: str) -> re.Pattern[str] | None:
    """Match the brand with flexible hyphen/space/apostrophe between words."""
    brand = (brand or "").strip()
    if len(brand) < 2:
        return None
    parts = [p for p in re.split(r"[\s\-]+", brand) if p]
    if len(parts) >= 2:
        body = r"[\s\-]+".join(re.escape(p) for p in parts)
    else:
        body = re.escape(brand)
    return re.compile(body, re.IGNORECASE)


def count_flexible_brand_mentions(text: str, brand: str) -> int:
    pat = _brand_flexible_pattern(brand)
    if not pat or not text:
        return 0
    return len(pat.findall(text))


def collect_brand_detected_spellings(text: str, brand: str) -> list[str]:
    """Literal spellings used in replies that match the brand (e.g. La Roche-Posay vs La Roche Posay)."""
    pat = _brand_flexible_pattern(brand)
    if not pat or not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in pat.findall(text):
        raw = str(match).strip()
        key = raw.lower()
        if raw and key not in seen:
            seen.add(key)
            out.append(raw)
    return out


def product_line_candidates_from_paths(paths: list[str]) -> list[str]:
    """Heuristic product-line tokens from URL path segments (e.g. /toleriane/...)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in paths or []:
        path = str(raw or "").split("?")[0].strip()
        if not path:
            continue
        for seg in path.strip("/").split("/"):
            seg = seg.strip().lower()
            if len(seg) < 4 or not seg.isalpha() or seg in _PATH_SEGMENT_BLOCKLIST:
                continue
            label = seg[0].upper() + seg[1:]
            key = label.lower()
            if key not in seen:
                seen.add(key)
                out.append(label)
    return out


def discover_product_line_aliases(
    reply_blob: str,
    brand_name: str,
    path_candidates: list[str] | None = None,
) -> list[str]:
    """Product-line aliases present in replies (path hints only — avoids generic word false positives)."""
    blob = (reply_blob or "").lower()
    if not blob:
        return []
    brand_key = _normalize_brand_key(brand_name)
    brand_parts = {
        p.lower()
        for p in re.split(r"[\s\-]+", (brand_name or "").strip())
        if len(p) >= 3
    }
    seen: set[str] = set()
    out: list[str] = []

    def add(label: str) -> None:
        token = (label or "").strip()
        if len(token) < 4:
            return
        key = token.lower()
        if key in seen or key in brand_parts or _normalize_brand_key(token) == brand_key:
            return
        if key not in blob:
            return
        seen.add(key)
        out.append(token[0].upper() + token[1:] if token.islower() else token)

    for candidate in path_candidates or []:
        add(str(candidate))

    return out


def brand_match_tokens(
    brand_name: str,
    brand_site_url: str = "",
    *,
    detected_spellings: list[str] | None = None,
    product_line_aliases: list[str] | None = None,
) -> list[str]:
    """Distinct match tokens for brand visibility and highlighting (longest first)."""
    brand = (brand_name or "").strip()
    tokens: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        token = (value or "").strip()
        if len(token) < 2:
            return
        key = token.lower()
        if key in seen:
            return
        seen.add(key)
        tokens.append(token)

    if brand:
        add(brand)
        if " " in brand and "-" not in brand:
            add(brand.replace(" ", "-"))
        elif "-" in brand and " " not in brand:
            add(brand.replace("-", " "))
    for spelling in detected_spellings or []:
        add(str(spelling))
    host = _host_label(brand_site_url)
    if host and len(host) >= 3:
        add(host)
        base = host.split(".")[0]
        if base and len(base) >= 3:
            add(base)
    for alias in product_line_aliases or []:
        add(str(alias))
    tokens.sort(key=len, reverse=True)
    return tokens


def text_mentions_brand(
    text: str,
    brand_name: str,
    brand_match_tokens_list: list[str] | None = None,
) -> bool:
    """True when text contains the brand (flexible spelling) or a known match token."""
    if not text:
        return False
    brand = (brand_name or "").strip()
    if brand and count_flexible_brand_mentions(text, brand) > 0:
        return True
    tl = text.lower()
    for token in brand_match_tokens_list or []:
        tok = str(token or "").strip()
        if len(tok) >= 2 and tok.lower() in tl:
            return True
    return False


_ENTITY_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "best", "but", "buy", "by",
        "can", "choose", "consider", "do", "does", "for", "from", "get", "good",
        "here", "how", "if", "in", "is", "it", "its", "known", "look", "more",
        "most", "of", "on", "or", "our", "recommended", "review", "see", "shop",
        "source", "that", "the", "their", "these", "they", "this", "those", "to",
        "top", "try", "use", "using", "view", "was", "we", "website", "what",
        "when", "where", "which", "with", "you", "your",
    }
)


def _entity_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _host_brand_stem(host: str) -> str:
    """Return the registrable domain label used to join site and brand aliases."""
    parts = (host or "").lower().removeprefix("www.").split(".")
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return parts[0] if parts else ""
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "net", "org"}:
        return parts[-3]
    return parts[-2]


def is_plausible_competitor_entity(value: str) -> bool:
    """Conservative entity gate for reply-derived competitor names."""
    name = re.sub(r"\s+", " ", (value or "").strip(" *_#:-–—"))
    if len(name) < 2 or len(name) > 80 or not re.search(r"[A-Za-zÀ-ÿ]", name):
        return False
    if name.rstrip().endswith(("+", "&", "/")):
        return False
    if re.match(r"^https?://", name, flags=re.IGNORECASE):
        return False
    words = [re.sub(r"^[^\wÀ-ÿ]+|[^\w+&'’À-ÿ.-]+$", "", w) for w in name.split()]
    words = [w for w in words if w]
    if not words:
        return False
    lowered = [w.lower().rstrip(".") for w in words]
    if len(words) == 1 and lowered[0] in _ENTITY_STOPWORDS:
        return False
    if all(word in _ENTITY_STOPWORDS for word in lowered):
        return False
    return True


def _leading_brand_from_item(item: str) -> str:
    """Extract a conservative brand label from one recommendation/list item."""
    value = re.sub(r"^\*+|\*+$", "", (item or "").strip(" *_#"))
    value = re.split(r"\s+(?:[-–—]|:)\s+|\s*\(", value, maxsplit=1)[0].strip()
    words = value.split()
    if not words:
        return ""

    first = re.sub(r"[^\w+&'’À-ÿ-]", "", words[0])
    first_key = first.lower().rstrip(".")
    if (first_key in _ENTITY_STOPWORDS and first_key != "the") or first_key in {
        "apply", "avoid", "check", "ensure", "follow", "pat", "rinse", "start", "wash",
    }:
        return ""

    take = 1
    if first_key == "the" and len(words) >= 3:
        take = 3
    elif first_key in {"la", "le", "dr"} and len(words) >= 2:
        take = 2
    elif first_key in {"paula's", "paula’s", "burt's", "burt’s", "hada", "pestle"} and len(words) >= 2:
        take = 2
    elif first_key == "nip" and len(words) >= 3 and words[1] == "+":
        take = 3

    candidate = " ".join(words[:take]).strip(" *_#:-–—")
    return candidate if is_plausible_competitor_entity(candidate) else ""


def linked_brand_entities(text: str) -> list[dict[str, str]]:
    """Extract brand/domain pairs when a markdown link identifies the entity."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r"\[([^\]]{2,120})\]\((https?://[^)\s]+)\)", text or ""):
        label = re.sub(r"[*_`#]", "", match.group(1)).strip()
        website = match.group(2).rstrip(".,;:")
        host = _host_label(website).lower().removeprefix("www.")
        host_base = _host_brand_stem(host)
        if not host_base:
            continue
        line_start = (text or "").rfind("\n", 0, match.start()) + 1
        line_prefix = (text or "")[line_start:match.start()]
        bold_context = re.findall(r"\*\*([^*]{2,80})\*\*", line_prefix)
        context_candidate = bold_context[-1].strip(" *_#:-–—") if bold_context else ""
        words = label.split()
        product_word_index = next(
            (
                index for index, word in enumerate(words)
                if word.lower().strip(".,:()") in _PATH_SEGMENT_BLOCKLIST
            ),
            -1,
        )
        if product_word_index > 0:
            label = " ".join(words[:product_word_index])
            words = label.split()
        candidate = (
            label.strip(" *_#:-–—")
            if 1 <= len(words) <= 4 and is_plausible_competitor_entity(label)
            else _leading_brand_from_item(label)
        )
        label_matches_host = bool(candidate) and _entity_key(candidate) == _entity_key(host_base)
        if (
            context_candidate
            and len(context_candidate.split()) <= 5
            and is_plausible_competitor_entity(context_candidate)
        ):
            candidate = context_candidate
            candidate_from_context = True
        else:
            candidate_from_context = False
        # A link label is a brand alias only when it corresponds to the linked
        # site's domain. Generic anchors such as "battery" or "view product"
        # must not become competitor entities.
        if (
            not candidate
            or (
                not label_matches_host
                and not candidate_from_context
            )
        ):
            candidate = host_base
        key = _entity_key(host_base)
        if key and key not in seen:
            seen.add(key)
            out.append(
                {
                    "brand_name": candidate,
                    "website_url": website,
                    "_link_priority": "1" if candidate_from_context else "2",
                }
            )
    return out


def linked_brand_candidates(text: str) -> list[str]:
    """Extract entity names backed by markdown hyperlinks in a response."""
    return [item["brand_name"] for item in linked_brand_entities(text)]


def recommendation_brand_candidates(text: str) -> list[str]:
    """Extract strong brand entities from links and recommendation lists."""
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        candidate = (candidate or "").strip()
        key = _entity_key(candidate)
        if candidate and key and key not in seen and is_plausible_competitor_entity(candidate):
            seen.add(key)
            candidates.append(candidate)

    for candidate in linked_brand_candidates(text):
        add(candidate)

    in_recommendations = False
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if "recommendation" in lower and not re.match(r"^(?:\d+[.)]|[-*•])\s+", line):
            in_recommendations = True
            continue
        numbered = re.match(r"^(?:\*{0,2})(\d+)[.)]\s+(.*)$", line)
        bullet = re.match(r"^[-*•]\s+(.*)$", line)
        if not numbered and not bullet:
            if in_recommendations and line and (
                line.startswith("#") or (line.startswith("**") and line.endswith("**"))
            ):
                in_recommendations = False
            continue
        if not in_recommendations:
            continue
        item = numbered.group(2) if numbered else bullet.group(1)
        add(_leading_brand_from_item(item))
    return candidates


def mention_scores_for_text(
    text: str,
    *,
    brand_name: str,
    brand_site_url: str,
    competitor_urls: list[str],
    competitor_brands: list[str] | None = None,
    reply_detected_brands: list[str] | None = None,
    brand_detected_spellings: list[str] | None = None,
    product_line_aliases: list[str] | None = None,
) -> dict[str, Any]:
    brand = (brand_name or "").strip()
    host = _host_label(brand_site_url)
    tl = (text or "").lower()
    host_bonus = 1 if (host and host.lower() in tl) else 0
    brand_name_hits = count_flexible_brand_mentions(text, brand) if brand else 0
    product_line_hits = 0
    product_detail: dict[str, int] = {}
    for alias in product_line_aliases or []:
        a = str(alias or "").strip()
        if len(a) < 3:
            continue
        c = count_mentions_ci(text, a)
        if c:
            product_line_hits += c
            product_detail[a.lower()] = product_detail.get(a.lower(), 0) + c
    comps = _competitor_match_tokens(
        competitor_urls,
        competitor_brands,
        primary_brand=brand,
        reply_detected_brands=reply_detected_brands,
    )
    detail: dict[str, int] = {}
    comp_sum = 0
    for t in comps:
        c = count_mentions_ci(text, t)
        if c:
            detail[t.lower()] = detail.get(t.lower(), 0) + c
            comp_sum += c
    brand_signal = brand_name_hits + product_line_hits + host_bonus
    return {
        "brand_name_hits": brand_name_hits,
        "product_line_hits": product_line_hits,
        "product_line_detail": product_detail,
        "primary_host_bonus": host_bonus,
        "brand_signal": brand_signal,
        "competitors_combined_hits": comp_sum,
        "competitor_detail": detail,
        "brand_detected_spellings": list(brand_detected_spellings or []),
    }


def mention_brand_competitor_share_pct(scores: dict[str, Any] | None) -> tuple[float, float]:
    """Within one assistant response, approximate share of **mention counts** brand vs all competitors."""
    if not scores:
        return (0.0, 0.0)
    b = float(scores.get("brand_signal") or 0)
    c = float(scores.get("competitors_combined_hits") or 0)
    t = b + c
    if t <= 0:
        return (0.0, 0.0)
    return (100.0 * b / t, 100.0 * c / t)


# ── Citation extraction ───────────────────────────────────────────────────────

# Matches full https?:// URLs and bare domain names with common TLDs.
# The https?:// branch is tried first and captures everything up to whitespace/delimiters.
_CITATION_URL_RE = re.compile(
    r"https?://[^\s<>\"')\]]+"
    r"|"
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)"
    r"+(?:com|org|net|io|ai|app|dev|biz|info|online|shop|store|"
    r"co(?:\.uk|\.nz|\.au|\.ca|\.za|\.in|\.jp|\.id)?|"
    r"uk|ie|de|fr|es|it|nl|be|at|ch|se|dk|no|fi|pl|pt|cz|ro|hu|gr|"
    r"au|nz|ca|za|in|jp|sg|hk|"
    r"gov|edu|health|news|london|club|me|eu)\b",
    re.IGNORECASE,
)

# TLDs that would cause false positives as plain words (e.g. "etc.")
_BARE_DOMAIN_BLOCKLIST = frozenset(
    {"e.g", "i.e", "etc", "vs", "no", "go", "so", "do", "to", "in", "on", "at"}
)

# Domains that should never appear as citations — intermediate redirects or
# internal infrastructure that carry no meaningful source information.
_CITATION_DOMAIN_BLOCKLIST = frozenset(
    {
        # Google infrastructure — Maps search links, auth pages, etc. are not
        # content citations (e.g. google.com/maps/search/...?utm_source=openai)
        "google.com",
        "google.co.uk",
        "google.com.au",
        "google.ca",
        "google.de",
        "google.fr",
        "google.es",
        "google.it",
        "google.nl",
        "maps.google.com",
        "consent.google.com",
        "accounts.google.com",
        # NOTE: vertexaisearch.cloud.google.com is intentionally NOT in this list.
        # Those redirect URLs come from Gemini grounding and are handled separately
        # via _resolve_grounding_redirects (google_aio.py) which keeps them even
        # when resolution fails, since the URL is still clickable and the title
        # field identifies the actual source.
    }
)


def _normalise_citation_url(raw: str) -> tuple[str, str]:
    """Return (url, domain) pair, normalising bare domains to https:// URLs."""
    raw = raw.rstrip(".,;:!?\"'")
    if not raw:
        return ("", "")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", raw):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
        domain = (parsed.hostname or "").lower().removeprefix("www.")
    except Exception:
        domain = ""
    if not domain or len(domain) < 4:
        return ("", "")
    return (raw, domain)


# ── Citation classification ────────────────────────────────────────────────────

_DOMAIN_TYPE_MAP: dict[str, tuple[str, str]] = {
    "youtube.com": ("Video", "YouTube"),
    "youtu.be": ("Video", "YouTube"),
    "tiktok.com": ("Video", "TikTok"),
    "instagram.com": ("Post", "Instagram"),
    "facebook.com": ("Post", "Facebook"),
    "fb.com": ("Post", "Facebook"),
    "twitter.com": ("Post", "X (Twitter)"),
    "x.com": ("Post", "X (Twitter)"),
    "reddit.com": ("Forum", "Reddit"),
    "wikipedia.org": ("Article", "Wikipedia"),
    "linkedin.com": ("Profile", "LinkedIn"),
    "pinterest.com": ("Post", "Pinterest"),
    "amazon.co.uk": ("Product", "eCommerce"),
    "amazon.com": ("Product", "eCommerce"),
    "amazon.de": ("Product", "eCommerce"),
    "ebay.co.uk": ("Product", "eCommerce"),
    "ebay.com": ("Product", "eCommerce"),
    "ebay.de": ("Product", "eCommerce"),
}


def _classify_citation(domain: str) -> tuple[str, str]:
    """Return (content_type, channel_type) for a citation domain."""
    d = domain.lower()
    for key, val in _DOMAIN_TYPE_MAP.items():
        if d == key or d.endswith("." + key):
            return val
    return ("Web page", "Website")


def extract_citations_from_reply(
    text: str,
    brand_site_url: str = "",
    *,
    include_bare_domains: bool = False,
) -> list[dict[str, str]]:
    """
    Extract URLs (and optionally bare domain names) mentioned in an LLM reply.

    Returns a list of ``{"url": ..., "domain": ..., "content_type": ..., "channel_type": ...}``
    dicts, deduplicated by URL, with the brand's own domain excluded.

    Bare domains (``rival.com``) are off by default — they are usually brand
    recommendations in the answer, not platform-referenced sources.
    """
    if not text:
        return []
    brand_domain = ""
    if brand_site_url:
        _, brand_domain = _normalise_citation_url(brand_site_url)

    # Deduplicate by normalised URL key (scheme://domain/path, lowercased, trailing-slash stripped)
    seen_keys: set[str] = set()
    out: list[dict[str, str]] = []
    for match in _CITATION_URL_RE.finditer(text):
        raw = match.group(0)
        is_full_url = bool(re.match(r"^https?://", raw, flags=re.IGNORECASE))
        if not include_bare_domains and not is_full_url:
            continue
        base = raw.split(".")[0].lower()
        if base in _BARE_DOMAIN_BLOCKLIST:
            continue
        url, domain = _normalise_citation_url(raw)
        if not domain:
            continue
        if domain in _CITATION_DOMAIN_BLOCKLIST:
            continue
        if brand_domain and domain == brand_domain:
            continue
        url_key = url.lower().rstrip("/")
        if url_key in seen_keys:
            continue
        seen_keys.add(url_key)
        content_type, channel_type = _classify_citation(domain)
        out.append({
            "url": url,
            "domain": domain,
            "content_type": content_type,
            "channel_type": channel_type,
        })
    return out


def _mention_summary(row: dict[str, Any], platform: str) -> tuple[bool, bool, list[str]]:
    """Return (brand_mentioned, competitor_mentioned, competitor_names) for one row/platform."""
    scores = row.get(f"mention_scores_{platform}") or {}
    brand_mentioned = int(scores.get("brand_signal") or 0) > 0
    comp_hits = int(scores.get("competitors_combined_hits") or 0) > 0
    detail = scores.get("competitor_detail") or {}
    comp_names = [k for k, v in detail.items() if int(v or 0) > 0]
    return brand_mentioned, comp_hits, comp_names


def aggregate_top_cited_urls(
    per_prompt: list[dict[str, Any]],
    *,
    brand_site_url: str = "",
    top_n: int = 50,
) -> list[dict[str, Any]]:
    """
    Aggregate per-URL citation counts across all prompts and platforms.

    Each entry in the returned list includes:
    ``{"url", "domain", "content_type", "channel_type", "frequency", "probe_platforms",
       "brand_mentioned", "competitor_mentioned", "competitor_names"}``.
    """
    url_data: dict[str, dict[str, Any]] = {}
    _, brand_domain = _normalise_citation_url(brand_site_url) if brand_site_url else ("", "")

    for row in per_prompt:
        if not isinstance(row, dict):
            continue
        for platform in _LIVE_PLATFORMS:
            citations: list[dict[str, str]] = row.get(f"citations_{platform}") or []
            brand_m, comp_m, comp_names = _mention_summary(row, platform)
            response_text = str(row.get(f"{platform}_response") or "").strip()
            for c in citations:
                url = c.get("url", "")
                domain = c.get("domain", "")
                if not url or not domain or domain == brand_domain:
                    continue
                url_key = url.lower().rstrip("/")
                if url_key not in url_data:
                    content_type = c.get("content_type") or _classify_citation(domain)[0]
                    channel_type = c.get("channel_type") or _classify_citation(domain)[1]
                    url_data[url_key] = {
                        "url": url,
                        "domain": domain,
                        "title": c.get("title"),
                        "thumbnail_url": c.get("thumbnail_url"),
                        "views": c.get("views"),
                        "platform": c.get("platform"),
                        "content_type": content_type,
                        "channel_type": channel_type,
                        "frequency": 0,
                        "probe_platforms": [],
                        "brand_mentioned": False,
                        "competitor_mentioned": False,
                        "competitor_names": [],
                    }
                entry = url_data[url_key]
                entry["frequency"] += 1
                if platform not in entry["probe_platforms"]:
                    entry["probe_platforms"].append(platform)

                # Use per-citation brand context if stored; otherwise use row-level signal
                if "brand_cited" in c:
                    if c["brand_cited"]:
                        entry["brand_mentioned"] = True
                    if c.get("competitor_cited"):
                        entry["competitor_mentioned"] = True
                else:
                    if brand_m:
                        entry["brand_mentioned"] = True
                    if comp_m:
                        entry["competitor_mentioned"] = True

                for cn in comp_names:
                    if cn not in entry["competitor_names"]:
                        entry["competitor_names"].append(cn)

    return sorted(url_data.values(), key=lambda x: -x["frequency"])[:top_n]


def aggregate_top_cited_sites(
    per_prompt: list[dict[str, Any]],
    *,
    brand_site_url: str = "",
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """
    Aggregate citation counts by domain across all prompts and platforms.

    Each entry includes brand/competitor mention status derived from the
    mention scores of each response that cited this domain.
    """
    domain_data: dict[str, dict[str, Any]] = {}
    _, brand_domain = _normalise_citation_url(brand_site_url) if brand_site_url else ("", "")

    for row in per_prompt:
        if not isinstance(row, dict):
            continue
        for platform in _LIVE_PLATFORMS:
            citations: list[dict[str, str]] = row.get(f"citations_{platform}") or []
            brand_m, comp_m, comp_names = _mention_summary(row, platform)
            for c in citations:
                d = c.get("domain", "")
                if not d or d == brand_domain:
                    continue
                if d not in domain_data:
                    domain_data[d] = {
                        "domain": d,
                        "count": 0,
                        "platforms": [],
                        "example_url": c.get("url", f"https://{d}"),
                        "brand_mentioned": False,
                        "competitor_mentioned": False,
                        "competitor_names": [],
                    }
                entry = domain_data[d]
                entry["count"] += 1
                if platform not in entry["platforms"]:
                    entry["platforms"].append(platform)
                if brand_m:
                    entry["brand_mentioned"] = True
                if comp_m:
                    entry["competitor_mentioned"] = True
                for cn in comp_names:
                    if cn not in entry["competitor_names"]:
                        entry["competitor_names"].append(cn)

    return sorted(domain_data.values(), key=lambda x: -x["count"])[:top_n]


def aggregate_live_sov(
    per_prompt: list[dict[str, Any]],
    *,
    excluded: set[str] | None = None,
) -> dict[str, Any]:
    blocked = excluded or set()

    # Discover which platforms have any data in this probe result
    platform_has_data: set[str] = set()
    for row in per_prompt:
        for platform in _LIVE_PLATFORMS:
            if row.get(f"mention_scores_{platform}") or row.get(f"{platform}_response"):
                platform_has_data.add(platform)

    active = [p for p in _LIVE_PLATFORMS if p not in blocked and p in platform_has_data]
    counts: dict[str, list[int]] = {p: [0, 0] for p in active}

    for row in per_prompt:
        for platform in active:
            m = row.get(f"mention_scores_{platform}") or {}
            counts[platform][0] += int(m.get("brand_signal") or 0)
            counts[platform][1] += int(m.get("competitors_combined_hits") or 0)

    def share(b: float, c: float) -> dict[str, float]:
        t = b + c + 1e-9
        return {"brand_share_pct": 100.0 * b / t, "competitor_share_pct": 100.0 * c / t}

    return {
        p: {"brand_hits": bh, "competitor_hits": ch, **share(bh, ch)}
        for p, (bh, ch) in counts.items()
    }


def highlight_response_html(
    text: str,
    brand_name: str,
    competitor_urls: list[str],
    competitor_brand_names: list[str] | None = None,
    *,
    reply_detected_brands: list[str] | None = None,
    brand_match_tokens_list: list[str] | None = None,
    product_line_aliases: list[str] | None = None,
) -> str:
    """Escape HTML, then wrap brand / competitor string matches in ``<mark>`` (for ``unsafe_allow_html``)."""
    esc = html.escape(text or "")
    if not esc.strip():
        return '<p style="color:#6b7280;">(empty response)</p>'
    brand = (brand_name or "").strip()
    hosts = _competitor_match_tokens(
        competitor_urls,
        competitor_brand_names,
        primary_brand=brand,
        reply_detected_brands=reply_detected_brands,
    )
    brand_pool = {x.lower() for x in brand_match_tokens_list or []}
    if brand:
        brand_pool.add(brand.lower())
    product_pool = {str(x).strip().lower() for x in (product_line_aliases or []) if str(x).strip()}
    parts: list[str] = []
    flex = _brand_flexible_pattern(brand)
    if flex is not None:
        parts.append(flex.pattern)
    for token in brand_match_tokens_list or []:
        tok = str(token or "").strip()
        if len(tok) >= 2 and tok.lower() != brand.lower():
            parts.append(re.escape(tok))
    for h in sorted(set(hosts), key=len, reverse=True):
        if len(h) >= 3:
            parts.append(re.escape(h))
    if not parts:
        return (
            '<div style="white-space:pre-wrap;line-height:1.5;border:1px solid #e5e7eb;'
            f'border-radius:8px;padding:10px;max-height:380px;overflow:auto;">{esc}</div>'
        )

    regex = re.compile("(" + ")|(".join(parts) + ")", re.IGNORECASE)

    def _is_brand_match(raw: str) -> bool:
        if not raw:
            return False
        lowered = raw.lower()
        if brand and _normalize_brand_key(raw) == _normalize_brand_key(brand):
            return True
        if lowered in brand_pool or lowered in product_pool:
            return True
        return False

    def repl(m: re.Match) -> str:
        raw = m.group(0)
        style = (
            "background:#fef08a;font-weight:600;padding:0 0.15em;border-radius:3px"
            if _is_brand_match(raw)
            else "background:#bfdbfe;padding:0 0.15em;border-radius:3px"
        )
        return f'<mark style="{style}">{raw}</mark>'

    body = regex.sub(repl, esc)
    return (
        '<div style="white-space:pre-wrap;line-height:1.5;border:1px solid #e5e7eb;'
        f'border-radius:8px;padding:10px;max-height:420px;overflow:auto;">{body}</div>'
    )


def recompute_live_probe_mention_scores(
    live: dict[str, Any],
    *,
    path_candidates: list[str] | None = None,
) -> dict[str, Any]:
    """Re-score mention visibility using flexible brand matching and reply-derived aliases."""
    if not isinstance(live, dict):
        return live
    brand = str(live.get("brand_name") or "").strip()
    brand_site_url = str(live.get("brand_site_url") or "").strip()
    comp_urls = [str(u).strip() for u in (live.get("competitor_urls") or []) if str(u).strip()]
    cbr = list(live.get("competitor_brands") or [])
    while len(cbr) < len(comp_urls):
        cbr.append("")
    cbr = cbr[: len(comp_urls)]
    saved_detected_names = [
        str(x).strip() for x in (live.get("reply_detected_brand_names") or []) if str(x).strip()
    ]
    per = live.get("per_prompt")
    if not isinstance(per, list):
        return live

    response_texts: list[str] = []
    for row in per:
        if not isinstance(row, dict):
            continue
        runs = row.get("runs") if isinstance(row.get("runs"), dict) else {}
        for platform in _LIVE_PLATFORMS:
            response_texts.append(str(row.get(f"{platform}_response") or ""))
            for run in runs.get(platform, []) if isinstance(runs.get(platform), list) else []:
                if isinstance(run, dict):
                    response_texts.append(str(run.get("response") or ""))
    reply_blob = " ".join(response_texts)

    # A detected competitor must be backed by a website. Merge structured
    # detection and response hyperlinks by domain so brand names and domains
    # are scored as one entity.
    primary_host = _host_label(brand_site_url).lower().removeprefix("www.")
    primary_host_key = _entity_key(_host_brand_stem(primary_host))
    linked_entities: dict[str, dict[str, str]] = {}
    linked_entity_priorities: dict[str, int] = {}

    def add_linked_entity(name: str, website: str, *, priority: int) -> None:
        host = _host_label(website).lower().removeprefix("www.")
        host_base = _host_brand_stem(host)
        host_key = _entity_key(host_base)
        if not host_key or host_key == primary_host_key:
            return
        clean_name = str(name or "").strip()
        if not clean_name or not is_plausible_competitor_entity(clean_name):
            clean_name = host_base
        if host_key not in linked_entities or priority > linked_entity_priorities.get(host_key, 0):
            linked_entities[host_key] = {
                "brand_name": clean_name,
                "website_url": str(website).strip(),
            }
            linked_entity_priorities[host_key] = priority

    for item in live.get("reply_detected_brands") or []:
        if isinstance(item, dict) and str(item.get("website_url") or "").strip():
            add_linked_entity(
                str(item.get("brand_name") or ""),
                str(item.get("website_url") or ""),
                priority=3,
            )
    for response_text in response_texts:
        for item in linked_brand_entities(response_text):
            add_linked_entity(
                item["brand_name"],
                item["website_url"],
                priority=int(item.get("_link_priority") or 1),
            )

    live["reply_detected_brands"] = list(linked_entities.values())
    trusted_keys: set[str] = set()
    for configured_name in cbr:
        trusted_keys.add(_entity_key(str(configured_name)))
    for configured_url in comp_urls:
        host = _host_label(configured_url).lower().removeprefix("www.")
        trusted_keys.update({_entity_key(host), _entity_key(_host_brand_stem(host))})
    for item in linked_entities.values():
        host = _host_label(item["website_url"]).lower().removeprefix("www.")
        trusted_keys.update(
            {
                _entity_key(item["brand_name"]),
                _entity_key(host),
                _entity_key(_host_brand_stem(host)),
            }
        )
    trusted_keys.discard("")
    primary_key = _entity_key(brand)
    reply_detected_names: list[str] = []
    detected_seen: set[str] = set()

    def add_detected_name(name: str) -> None:
        clean_name = str(name or "").strip()
        key = _entity_key(clean_name)
        if (
            not clean_name
            or not key
            or key not in trusted_keys
            or (primary_key and key.startswith(primary_key))
            or clean_name.lower() in detected_seen
        ):
            return
        detected_seen.add(clean_name.lower())
        reply_detected_names.append(clean_name)

    for name in saved_detected_names:
        add_detected_name(name)
    for item in linked_entities.values():
        add_detected_name(item["brand_name"])
        host = _host_label(item["website_url"]).lower().removeprefix("www.")
        add_detected_name(host)
        add_detected_name(_host_brand_stem(host))
    live["reply_detected_brand_names"] = reply_detected_names
    detected_spellings = collect_brand_detected_spellings(reply_blob, brand)
    path_cands = product_line_candidates_from_paths(path_candidates or [])
    product_lines = discover_product_line_aliases(reply_blob, brand, path_cands)
    match_tokens = brand_match_tokens(
        brand,
        brand_site_url,
        detected_spellings=detected_spellings,
        product_line_aliases=product_lines,
    )
    live["brand_detected_spellings"] = detected_spellings
    live["product_line_aliases"] = product_lines
    live["brand_match_tokens"] = match_tokens

    def _score_response_target(
        response: str,
        target: dict[str, Any],
        *,
        citations_key: str,
    ) -> None:
        scores = mention_scores_for_text(
            response,
            brand_name=brand,
            brand_site_url=brand_site_url,
            competitor_urls=comp_urls,
            competitor_brands=cbr,
            reply_detected_brands=reply_detected_names,
            brand_detected_spellings=detected_spellings,
            product_line_aliases=product_lines,
        )
        target["mention_scores"] = scores
        citations = target.get(citations_key)
        if not citations:
            citations = extract_citations_from_reply(
                response,
                brand_site_url,
                include_bare_domains=False,
            )
            target[citations_key] = citations
        if isinstance(citations, list):
            from citation_context import (
                build_brand_tokens,
                build_competitor_tokens,
                enrich_citations_with_context,
            )

            for citation in citations:
                if isinstance(citation, dict):
                    citation.pop("brand_cited", None)
                    citation.pop("competitor_cited", None)
            enrich_citations_with_context(
                [citation for citation in citations if isinstance(citation, dict)],
                response,
                build_brand_tokens(brand, brand_site_url),
                build_competitor_tokens(comp_urls, cbr + reply_detected_names),
            )

    for row in per:
        if not isinstance(row, dict):
            continue
        for platform in _LIVE_PLATFORMS:
            raw_runs = (row.get("runs") or {}).get(platform) or []
            if isinstance(raw_runs, list):
                for run in raw_runs:
                    if not isinstance(run, dict) or run.get("error"):
                        continue
                    run_response = str(run.get("response") or "")
                    if run_response:
                        _score_response_target(run_response, run, citations_key="citations")
            txt = str(row.get(f"{platform}_response") or "")
            if not txt:
                continue
            aggregate_target: dict[str, Any] = {
                "citations": row.get(f"citations_{platform}") or [],
            }
            _score_response_target(txt, aggregate_target, citations_key="citations")
            scores = aggregate_target["mention_scores"]
            row[f"mention_scores_{platform}"] = scores
            bp, cp = mention_brand_competitor_share_pct(scores)
            row[f"{platform}_brand_mention_pct"] = bp
            row[f"{platform}_competitor_mention_pct"] = cp
            row[f"citations_{platform}"] = aggregate_target["citations"]

    from api.probe_platforms import get_excluded_platforms

    excluded = set(get_excluded_platforms())
    saved_excluded = live.get("excluded_platforms")
    if isinstance(saved_excluded, list):
        excluded.update(str(x).strip().lower() for x in saved_excluded if str(x).strip())
    live["aggregate"] = aggregate_live_sov(
        [r for r in per if isinstance(r, dict)],
        excluded=excluded,
    )
    # Recompute citation aggregates after back-filling citations
    live["top_cited_sites"] = aggregate_top_cited_sites(per, brand_site_url=brand_site_url)
    live["top_cited_urls"] = aggregate_top_cited_urls(per, brand_site_url=brand_site_url)
    return live


def _aggregate_prompt_runs(
    prompt_runs: list[dict[str, Any]],
    brand_site_url: str = "",
) -> dict[str, Any]:
    """Merge N run dicts for the same prompt into one combined row.

    Flat fields (response_*, citations_*, mention_scores_*) are set from the
    last successful run so existing code is unaffected.  Per-run detail is
    stored in ``runs`` → {platform: [{run_index, response, citations, mention_scores}]}.

    mention_scores_*.brand_signal is summed across runs so that existing
    aggregation (any run with brand_signal > 0 → mentioned) still works.
    Extra keys ``runs_with_brand`` / ``total_runs`` / ``brand_consistency_pct``
    are added for the new UI.
    """
    n = len(prompt_runs)
    if n == 0:
        return {}
    if n == 1:
        row = dict(prompt_runs[0])
        row.setdefault("runs", {pk: [] for pk in ("gemini", "openai", "claude", "google_aio")})
        return row

    base: dict[str, Any] = {
        "index": prompt_runs[0]["index"],
        "prompt": prompt_runs[0]["prompt"],
        "runs": {},
    }

    for plat in ("gemini", "openai", "claude", "google_aio"):
        plat_runs_list: list[dict[str, Any]] = []
        last_response = ""
        all_citations: dict[str, dict[str, Any]] = {}
        total_brand_signal = 0
        last_scores: dict[str, Any] = {}
        merged_comp_detail: dict[str, int] = {}

        for run in prompt_runs:
            resp = str(run.get(f"{plat}_response") or "")
            cits: list[dict[str, Any]] = list(run.get(f"citations_{plat}") or [])
            scores: dict[str, Any] = dict(run.get(f"mention_scores_{plat}") or {})
            err = str(run.get(f"error_{plat}") or "")

            if resp:
                last_response = resp
            for c in cits:
                key = str(c.get("url") or "").lower().rstrip("/")
                if key and key not in all_citations:
                    all_citations[key] = c
            bs = int(scores.get("brand_signal") or 0)
            total_brand_signal += bs
            if scores:
                last_scores = scores
            for comp, cnt in (scores.get("competitor_detail") or {}).items():
                merged_comp_detail[comp] = merged_comp_detail.get(comp, 0) + int(cnt or 0)

            plat_runs_list.append({
                "run_index": run.get("run_index", 0),
                "response": resp,
                "citations": cits,
                "mention_scores": scores,
                "error": err,
            })

        base[f"{plat}_response"] = last_response
        base[f"citations_{plat}"] = list(all_citations.values())

        agg_scores = dict(last_scores)
        runs_with_brand = sum(1 for pr in plat_runs_list if int((pr.get("mention_scores") or {}).get("brand_signal") or 0) > 0)
        agg_scores["brand_signal"] = total_brand_signal
        agg_scores["runs_with_brand"] = runs_with_brand
        agg_scores["total_runs"] = n
        agg_scores["brand_consistency_pct"] = round(100 * runs_with_brand / n, 1)
        agg_scores["competitor_detail"] = merged_comp_detail
        agg_scores["competitors_combined_hits"] = sum(merged_comp_detail.values())
        base[f"mention_scores_{plat}"] = agg_scores

        errs = [str(run.get(f"error_{plat}") or "") for run in prompt_runs if run.get(f"error_{plat}")]
        if errs:
            base[f"error_{plat}"] = " | ".join(errs)

        base["runs"][plat] = plat_runs_list

    return base


def run_live_prompt_probes(
    prompts: list[str],
    *,
    brand_name: str,
    brand_site_url: str,
    competitor_urls: list[str],
    competitor_brands: list[str] | None = None,
    num_runs: int = 3,
    max_prompts: int = 10,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "en",
    language_name: str = "English",
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """
    For each user prompt, call **Gemini** and **OpenAI** chat completions as consumer assistants, then
    score **mention-based** share of voice (brand vs competitors) from the returned text.

    ``competitor_brands`` should align by index with ``competitor_urls`` (same length optional; extras ignored).

    Requires OpenAI API key plus Gemini configuration (same as elsewhere in this app).
    """
    okey = _openai_api_key()
    if not okey:
        raise ValueError(
            "Set **OPENAI_API_KEY** (environment or Streamlit secrets) to run live OpenAI probes alongside Gemini."
        )
    ak = _gemini_api_key()
    if not ak and not (
        _truthy_env("GEMINI_USE_VERTEX_AI") and (_get_config("GOOGLE_CLOUD_PROJECT") or "").strip()
    ):
        raise ValueError(
            "Configure Gemini (**GEMINI_API_KEY** / **GOOGLE_API_KEY**, or Vertex + **GOOGLE_CLOUD_PROJECT**) "
            "before live probes."
        )
    ckey = _anthropic_api_key()

    brand = (brand_name or "").strip()
    if not brand:
        raise ValueError("Brand name is required.")
    comp_urls = [str(u).strip() for u in (competitor_urls or []) if str(u).strip()]
    cbr = list(competitor_brands or [])
    while len(cbr) < len(comp_urls):
        cbr.append("")
    cbr = cbr[: len(comp_urls)]
    mc_res, mid_res = resolve_primary_market(market_country, market_country_code)
    phrase = geo_locator_phrase_for_market(mc_res, mid_res)
    lang = (language or "en").strip().lower() or "en"
    lang_label = (language_name or "").strip() or ("English" if lang == "en" else lang)

    from citation_context import build_brand_tokens, build_competitor_tokens
    _brand_tokens = build_brand_tokens(brand, brand_site_url)
    _comp_tokens = build_competitor_tokens(comp_urls, cbr)
    lim = max_prompts if max_prompts and max_prompts > 0 else 50
    used = [p.strip() for p in prompts if p and str(p).strip()][: max(1, min(lim, 80))]
    if phrase:
        used = [ensure_prompt_contains_geo_locator(p, phrase) for p in used]

    from api.probe_platforms import (
        exclude_platform,
        get_excluded_platforms,
        is_fatal_platform_error,
        sanitize_live_probe,
    )

    excluded = get_excluded_platforms()
    disabled_run: set[str] = set()

    def _platform_live(pk: str) -> bool:
        return pk not in excluded and pk not in disabled_run

    _, brand_domain = _normalise_citation_url(brand_site_url) if brand_site_url else ("", "")

    effective_num_runs = max(1, min(int(num_runs or 1), 5))
    enabled_platforms = [
        platform
        for platform in ("gemini", "openai", "claude", "google_aio")
        if (platform != "claude" or ckey) and _platform_live(platform)
    ]
    planned_calls = len(used) * effective_num_runs * len(enabled_platforms)
    completed_calls = 0
    probe_started = time.monotonic()

    def _emit_progress(
        *,
        status: str,
        prompt_index: int = 0,
        run_index: int = 0,
        platform: str = "",
        elapsed_seconds: float | None = None,
        error: str = "",
    ) -> None:
        nonlocal completed_calls
        if status in {"complete", "error"}:
            completed_calls += 1
        payload = {
            "status": status,
            "prompt_index": prompt_index,
            "prompt_total": len(used),
            "run_index": run_index,
            "run_total": effective_num_runs,
            "platform": platform,
            "completed_calls": completed_calls,
            "planned_calls": planned_calls,
            "elapsed_seconds": round(elapsed_seconds, 2) if elapsed_seconds is not None else None,
            "total_elapsed_seconds": round(time.monotonic() - probe_started, 2),
            "error": error[:300],
            "locale_language": lang,
            "locale_market": mid_res or mc_res,
        }
        log.info("AI prompt probe progress: %s", json.dumps(payload, ensure_ascii=False))
        if progress_callback:
            try:
                progress_callback(payload)
            except Exception:
                log.exception("AI prompt probe progress callback failed")

    _emit_progress(status="started")

    _locale_kwargs = {
        "market_country": mc_res,
        "market_country_code": mid_res,
        "language": lang,
        "language_name": lang_label,
    }

    rows: list[dict[str, Any]] = []
    for i, user_q in enumerate(used, start=1):
        prompt_run_results: list[dict[str, Any]] = []

        for run_idx in range(effective_num_runs):
            row: dict[str, Any] = {
                "index": i,
                "run_index": run_idx,
                "prompt": user_q,
                "gemini_response": "",
                "openai_response": "",
                "claude_response": "",
                "google_aio_response": "",
            }
            progress_lock = threading.Lock()
            gemini_family_lock = threading.Lock()
            disable_lock = threading.Lock()

            def _mark_disabled(pk: str) -> None:
                with disable_lock:
                    disabled_run.add(pk)

            def _safe_emit(**kwargs: Any) -> None:
                with progress_lock:
                    _emit_progress(**kwargs)

            def _probe_platform(platform: str) -> None:
                if not _platform_live(platform):
                    return
                family_ctx = (
                    gemini_family_lock
                    if platform in _GEMINI_FAMILY
                    else nullcontext()
                )
                with family_ctx:
                    if not _platform_live(platform):
                        return
                    call_started = time.monotonic()
                    _safe_emit(
                        status="running",
                        prompt_index=i,
                        run_index=run_idx + 1,
                        platform=platform,
                    )
                    try:
                        if platform == "gemini":
                            g_text, g_api_cits = gemini_answer_with_citations(
                                user_q,
                                **_locale_kwargs,
                            )
                            row["gemini_response"] = g_text
                            row["citations_gemini"] = _merge_citations(
                                g_api_cits, g_text, brand_site_url, _brand_tokens, _comp_tokens
                            )
                        elif platform == "openai":
                            o_text, o_api_cits = openai_answer_with_citations(
                                user_q,
                                api_key=okey,
                                **_locale_kwargs,
                            )
                            row["openai_response"] = o_text
                            row["citations_openai"] = _merge_citations(
                                o_api_cits, o_text, brand_site_url, _brand_tokens, _comp_tokens
                            )
                        elif platform == "claude":
                            row["claude_response"] = claude_answer_user_prompt(
                                user_q,
                                api_key=ckey,
                                **_locale_kwargs,
                            )
                            row["citations_claude"] = _merge_citations(
                                [],
                                row["claude_response"],
                                brand_site_url,
                                _brand_tokens,
                                _comp_tokens,
                            )
                        elif platform == "google_aio":
                            aio_result = _aio_grounded_answer(
                                user_q,
                                market_country=mc_res,
                                market_country_code=mid_res,
                            )
                            if aio_result.get("error"):
                                row["error_google_aio"] = aio_result["error"]
                                if is_fatal_platform_error("google_aio", aio_result["error"]):
                                    exclude_platform("google_aio", aio_result["error"])
                                    _mark_disabled("google_aio")
                            else:
                                row["google_aio_response"] = aio_result.get("response") or ""
                                raw_cits = [
                                    c for c in (aio_result.get("citations") or [])
                                    if c.get("domain") and c.get("domain") != brand_domain
                                ]
                                for c in raw_cits:
                                    if not c.get("content_type"):
                                        ct, cht = _classify_citation(c.get("domain", ""))
                                        c["content_type"] = ct
                                        c["channel_type"] = cht
                                row["citations_google_aio"] = _merge_citations(
                                    raw_cits,
                                    row["google_aio_response"],
                                    brand_site_url,
                                    _brand_tokens,
                                    _comp_tokens,
                                )
                    except Exception as e:
                        err = str(e)
                        row[f"error_{platform}"] = err
                        if is_fatal_platform_error(platform, err):
                            exclude_platform(platform, err)
                            _mark_disabled(platform)
                    finally:
                        error = str(row.get(f"error_{platform}") or "")
                        _safe_emit(
                            status="error" if error else "complete",
                            prompt_index=i,
                            run_index=run_idx + 1,
                            platform=platform,
                            elapsed_seconds=time.monotonic() - call_started,
                            error=error,
                        )

            platforms_this_run = [
                pk
                for pk in _LIVE_PLATFORMS
                if (pk != "claude" or ckey) and _platform_live(pk)
            ]
            if _parallel_platform_probes_enabled() and len(platforms_this_run) > 1:
                workers = min(3, len(platforms_this_run))
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = [pool.submit(_probe_platform, pk) for pk in platforms_this_run]
                    for fut in concurrent.futures.as_completed(futs):
                        fut.result()
            else:
                for pk in platforms_this_run:
                    _probe_platform(pk)

            prompt_run_results.append(row)
            # end run_idx loop

        combined_row = _aggregate_prompt_runs(prompt_run_results, brand_site_url)
        rows.append(combined_row)

    _emit_progress(status="responses_complete")

    reply_detected: list[dict[str, str]] = []
    reply_detected_names: list[str] = []
    probe_brand_detect_err: str | None = None
    entity_detection_started = time.monotonic()
    _emit_progress(status="entity_detection_started")
    try:
        from geo_setup_llm import suggest_reply_detected_competitor_brands

        reply_detected = suggest_reply_detected_competitor_brands(
            rows,
            primary_brand=brand,
            primary_site_url=brand_site_url,
            market_country=mc_res,
            market_country_code=mid_res,
        )
        brand_names = [
            str(x.get("brand_name") or "").strip()
            for x in reply_detected
            if str(x.get("brand_name") or "").strip()
        ]
        seen_lo = {b.lower() for b in brand_names}
        for x in reply_detected:
            u = str(x.get("website_url") or "").strip()
            if not u:
                continue
            h = _host_label(u)
            if h and len(h) >= 3 and h.lower() not in seen_lo:
                seen_lo.add(h.lower())
                brand_names.append(h)
        reply_detected_names = brand_names
        if reply_detected:

            def _prompt_appearance_count(brand: str, website: str) -> int:
                tokens: list[str] = []
                b = (brand or "").strip().lower()
                if len(b) >= 2:
                    tokens.append(b)
                u = (website or "").strip()
                if u:
                    try:
                        host = (urlparse(u).hostname or "").lower().replace("www.", "")
                        if len(host) >= 3:
                            tokens.append(host)
                            base = host.split(".")[0]
                            if base and len(base) >= 3:
                                tokens.append(base)
                    except Exception:
                        pass
                if not tokens:
                    return 0
                n = 0
                for prow in rows:
                    blob = " ".join(
                    str(prow.get(f"{pl}_response") or "") for pl in _LIVE_PLATFORMS
                ).lower()
                    if any(t in blob for t in tokens):
                        n += 1
                return n

            reply_detected.sort(
                key=lambda x: (
                    -_prompt_appearance_count(
                        str(x.get("brand_name") or ""),
                        str(x.get("website_url") or ""),
                    ),
                    str(x.get("brand_name") or "").lower(),
                )
            )
    except Exception as e:
        probe_brand_detect_err = str(e)
    finally:
        _emit_progress(
            status="entity_detection_error" if probe_brand_detect_err else "entity_detection_complete",
            elapsed_seconds=time.monotonic() - entity_detection_started,
            error=probe_brand_detect_err or "",
        )

    result = {
        "per_prompt": rows,
        "brand_name": brand,
        "brand_site_url": brand_site_url,
        "competitor_urls": comp_urls,
        "competitor_brands": cbr,
        "reply_detected_brands": reply_detected,
        "reply_detected_brand_names": reply_detected_names,
        "reply_detected_brands_error": probe_brand_detect_err,
        "top_cited_sites": aggregate_top_cited_sites(rows, brand_site_url=brand_site_url),
        "top_cited_urls": aggregate_top_cited_urls(rows, brand_site_url=brand_site_url),
        "primary_market": (
            {
                "country": mc_res,
                "country_id": mid_res,
                "geo_locator_phrase": phrase or None,
                "language": lang,
                "language_name": lang_label,
            }
            if (mc_res or mid_res or lang)
            else None
        ),
    }
    recompute_live_probe_mention_scores(result)
    result["aggregate"] = aggregate_live_sov(rows, excluded=get_excluded_platforms() | disabled_run)
    claude_active = ckey and "claude" not in excluded and "claude" not in disabled_run
    disc = (
        "Live probes call real APIs (usage billed to your keys). Brand mention counts use **flexible spelling** "
        "of your wizard brand name, detected reply spellings, and product-line aliases found in replies. "
        "Competitor counts use wizard competitor fields **plus** brands from a **Gemini pass** over reply excerpts "
        "when that step succeeds—heuristic visibility, not legal truth of endorsement or ranking."
        + (" Claude probes included." if claude_active else " Claude probes skipped (not configured or excluded).")
    )
    if probe_brand_detect_err:
        disc += f" (Reply brand detection failed: {probe_brand_detect_err[:240]})"
    result["disclaimer"] = disc
    if excluded or disabled_run:
        result["excluded_platforms"] = sorted(excluded | disabled_run)
    return sanitize_live_probe(result)

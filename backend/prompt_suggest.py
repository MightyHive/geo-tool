"""
AI search **prompt recommendations**: likely user queries from setup context, **live Gemini + OpenAI answers**
per query, **mention-based %** for brand vs competitors in each reply, and **on-site content actions** for weak prompts
(using **live probe** aggregates only).

Uses the same Gemini auth as :mod:`competitor_suggest` (``GEMINI_API_KEY`` / Vertex).
OpenAI live answers use ``OPENAI_API_KEY`` (environment or ``secrets.toml`` via :func:`competitor_suggest._get_config`).

**Live probes** call real APIs (usage billed to your keys); mention counts are heuristic substring matches.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from typing import Any
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
    "When it helps the shopper, name specific retailers, brands, or websites they could consider—including "
    "smaller specialists if relevant. Aim for about 150–400 words unless a shorter reply clearly suffices."
)


def live_assistant_system_instruction(
    *,
    market_country: str = "",
    market_country_code: str = "",
) -> str:
    """System prompt for live Gemini/OpenAI probes, with optional primary-market context (wizard / GA4 / env)."""
    base = LIVE_ASSISTANT_SYSTEM
    mc, mid = resolve_primary_market(market_country, market_country_code)
    phrase = geo_locator_phrase_for_market(mc, mid)
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


def gemini_answer_user_prompt(
    user_prompt: str,
    *,
    market_country: str = "",
    market_country_code: str = "",
) -> str:
    """Assistant-style completion (plain text, not JSON)."""
    up = (user_prompt or "").strip()[:12000]
    if not up:
        raise ValueError("Empty prompt.")
    sys_instr = live_assistant_system_instruction(
        market_country=market_country,
        market_country_code=market_country_code,
    )
    return _gemini_generate(system_instruction=sys_instr, user_text=up).strip()


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
    return len(re.findall(re.escape(needle.strip()), text, flags=re.IGNORECASE))


def mention_scores_for_text(
    text: str,
    *,
    brand_name: str,
    brand_site_url: str,
    competitor_urls: list[str],
    competitor_brands: list[str] | None = None,
    reply_detected_brands: list[str] | None = None,
) -> dict[str, Any]:
    brand = (brand_name or "").strip()
    host = _host_label(brand_site_url)
    tl = (text or "").lower()
    host_bonus = 1 if (host and host.lower() in tl) else 0
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
    return {
        "brand_name_hits": count_mentions_ci(text, brand) if brand else 0,
        "primary_host_bonus": host_bonus,
        "brand_signal": (count_mentions_ci(text, brand) if brand else 0) + host_bonus,
        "competitors_combined_hits": comp_sum,
        "competitor_detail": detail,
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


def aggregate_live_sov(
    per_prompt: list[dict[str, Any]],
    *,
    excluded: set[str] | None = None,
) -> dict[str, Any]:
    blocked = excluded or set()
    gb = gc = ob = oc = cb = cc = 0
    for row in per_prompt:
        if "gemini" not in blocked:
            mg = row.get("mention_scores_gemini") or {}
            gb += int(mg.get("brand_signal") or 0)
            gc += int(mg.get("competitors_combined_hits") or 0)
        if "openai" not in blocked:
            mo = row.get("mention_scores_openai") or {}
            ob += int(mo.get("brand_signal") or 0)
            oc += int(mo.get("competitors_combined_hits") or 0)
        if "claude" not in blocked:
            mc = row.get("mention_scores_claude") or {}
            cb += int(mc.get("brand_signal") or 0)
            cc += int(mc.get("competitors_combined_hits") or 0)

    def share(b: float, c: float) -> dict[str, float]:
        t = b + c + 1e-9
        return {"brand_share_pct": 100.0 * b / t, "competitor_share_pct": 100.0 * c / t}

    out: dict[str, Any] = {}
    if "gemini" not in blocked:
        out["gemini"] = {"brand_hits": gb, "competitor_hits": gc, **share(gb, gc)}
    if "openai" not in blocked:
        out["openai"] = {"brand_hits": ob, "competitor_hits": oc, **share(ob, oc)}
    if "claude" not in blocked:
        out["claude"] = {"brand_hits": cb, "competitor_hits": cc, **share(cb, cc)}
    return out


def highlight_response_html(
    text: str,
    brand_name: str,
    competitor_urls: list[str],
    competitor_brand_names: list[str] | None = None,
    *,
    reply_detected_brands: list[str] | None = None,
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
    parts: list[str] = []
    if len(brand) >= 2:
        parts.append(re.escape(brand))
    for h in sorted(set(hosts), key=len, reverse=True):
        if len(h) >= 3:
            parts.append(re.escape(h))
    if not parts:
        return (
            '<div style="white-space:pre-wrap;line-height:1.5;border:1px solid #e5e7eb;'
            f'border-radius:8px;padding:10px;max-height:380px;overflow:auto;">{esc}</div>'
        )

    regex = re.compile("(" + ")|(".join(parts) + ")", re.IGNORECASE)

    def repl(m: re.Match) -> str:
        raw = m.group(0)
        is_brand = bool(brand) and raw.lower() == brand.lower()
        style = (
            "background:#fef08a;font-weight:600;padding:0 0.15em;border-radius:3px"
            if is_brand
            else "background:#bfdbfe;padding:0 0.15em;border-radius:3px"
        )
        return f'<mark style="{style}">{raw}</mark>'

    body = regex.sub(repl, esc)
    return (
        '<div style="white-space:pre-wrap;line-height:1.5;border:1px solid #e5e7eb;'
        f'border-radius:8px;padding:10px;max-height:420px;overflow:auto;">{body}</div>'
    )


def run_live_prompt_probes(
    prompts: list[str],
    *,
    brand_name: str,
    brand_site_url: str,
    competitor_urls: list[str],
    competitor_brands: list[str] | None = None,
    max_prompts: int = 10,
    market_country: str = "",
    market_country_code: str = "",
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

    rows: list[dict[str, Any]] = []
    for i, user_q in enumerate(used, start=1):
        row: dict[str, Any] = {
            "index": i,
            "prompt": user_q,
            "gemini_response": "",
            "openai_response": "",
            "claude_response": "",
        }
        if _platform_live("gemini"):
            try:
                row["gemini_response"] = gemini_answer_user_prompt(
                    user_q,
                    market_country=mc_res,
                    market_country_code=mid_res,
                )
            except Exception as e:
                err = str(e)
                if is_fatal_platform_error("gemini", err):
                    exclude_platform("gemini", err)
                    disabled_run.add("gemini")
                else:
                    row["error_gemini"] = err
        if _platform_live("openai"):
            try:
                row["openai_response"] = openai_chat_answer(
                    user_q,
                    api_key=okey,
                    market_country=mc_res,
                    market_country_code=mid_res,
                )
            except Exception as e:
                err = str(e)
                if is_fatal_platform_error("openai", err):
                    exclude_platform("openai", err)
                    disabled_run.add("openai")
                else:
                    row["error_openai"] = err
        if ckey and _platform_live("claude"):
            try:
                row["claude_response"] = claude_answer_user_prompt(
                    user_q,
                    api_key=ckey,
                    market_country=mc_res,
                    market_country_code=mid_res,
                )
            except Exception as e:
                err = str(e)
                if is_fatal_platform_error("claude", err):
                    exclude_platform("claude", err)
                    disabled_run.add("claude")
                else:
                    row["error_claude"] = err

        gtxt = row.get("gemini_response") or ""
        otxt = row.get("openai_response") or ""
        ctxt = row.get("claude_response") or ""
        if gtxt:
            row["mention_scores_gemini"] = mention_scores_for_text(
                gtxt,
                brand_name=brand,
                brand_site_url=brand_site_url,
                competitor_urls=comp_urls,
                competitor_brands=cbr,
            )
            row["gemini_brand_mention_pct"], row["gemini_competitor_mention_pct"] = mention_brand_competitor_share_pct(
                row["mention_scores_gemini"]
            )
        if otxt:
            row["mention_scores_openai"] = mention_scores_for_text(
                otxt,
                brand_name=brand,
                brand_site_url=brand_site_url,
                competitor_urls=comp_urls,
                competitor_brands=cbr,
            )
            row["openai_brand_mention_pct"], row["openai_competitor_mention_pct"] = mention_brand_competitor_share_pct(
                row["mention_scores_openai"]
            )
        if ctxt:
            row["mention_scores_claude"] = mention_scores_for_text(
                ctxt,
                brand_name=brand,
                brand_site_url=brand_site_url,
                competitor_urls=comp_urls,
                competitor_brands=cbr,
            )
            row["claude_brand_mention_pct"], row["claude_competitor_mention_pct"] = mention_brand_competitor_share_pct(
                row["mention_scores_claude"]
            )
        rows.append(row)

    reply_detected: list[dict[str, str]] = []
    reply_detected_names: list[str] = []
    probe_brand_detect_err: str | None = None
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
                    blob = f"{prow.get('gemini_response') or ''} {prow.get('openai_response') or ''} {prow.get('claude_response') or ''}".lower()
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

    if reply_detected_names:
        for row in rows:
            gtxt = row.get("gemini_response") or ""
            otxt = row.get("openai_response") or ""
            ctxt = row.get("claude_response") or ""
            if gtxt:
                row["mention_scores_gemini"] = mention_scores_for_text(
                    gtxt,
                    brand_name=brand,
                    brand_site_url=brand_site_url,
                    competitor_urls=comp_urls,
                    competitor_brands=cbr,
                    reply_detected_brands=reply_detected_names,
                )
                row["gemini_brand_mention_pct"], row["gemini_competitor_mention_pct"] = mention_brand_competitor_share_pct(
                    row["mention_scores_gemini"]
                )
            if otxt:
                row["mention_scores_openai"] = mention_scores_for_text(
                    otxt,
                    brand_name=brand,
                    brand_site_url=brand_site_url,
                    competitor_urls=comp_urls,
                    competitor_brands=cbr,
                    reply_detected_brands=reply_detected_names,
                )
                row["openai_brand_mention_pct"], row["openai_competitor_mention_pct"] = mention_brand_competitor_share_pct(
                    row["mention_scores_openai"]
                )
            if ctxt:
                row["mention_scores_claude"] = mention_scores_for_text(
                    ctxt,
                    brand_name=brand,
                    brand_site_url=brand_site_url,
                    competitor_urls=comp_urls,
                    competitor_brands=cbr,
                    reply_detected_brands=reply_detected_names,
                )
                row["claude_brand_mention_pct"], row["claude_competitor_mention_pct"] = mention_brand_competitor_share_pct(
                    row["mention_scores_claude"]
                )

    claude_active = ckey and "claude" not in excluded and "claude" not in disabled_run
    disc = (
        "Live probes call real APIs (usage billed to your keys). Competitor mention counts use **substring matches** "
        "on your wizard competitor fields **plus** brands named by a **Gemini pass** over reply excerpts when that "
        "step succeeds—heuristic visibility, not legal truth of endorsement or ranking."
        + (" Claude probes included." if claude_active else " Claude probes skipped (not configured or excluded).")
    )
    if probe_brand_detect_err:
        disc += f" (Reply brand detection failed: {probe_brand_detect_err[:240]})"

    result = {
        "per_prompt": rows,
        "aggregate": aggregate_live_sov(rows, excluded=get_excluded_platforms() | disabled_run),
        "brand_name": brand,
        "brand_site_url": brand_site_url,
        "competitor_urls": comp_urls,
        "competitor_brands": cbr,
        "reply_detected_brands": reply_detected,
        "reply_detected_brand_names": reply_detected_names,
        "reply_detected_brands_error": probe_brand_detect_err,
        "primary_market": (
            {
                "country": mc_res,
                "country_id": mid_res,
                "geo_locator_phrase": phrase or None,
            }
            if (mc_res or mid_res)
            else None
        ),
        "disclaimer": disc,
    }
    if disabled_run:
        result["excluded_platforms"] = sorted(get_excluded_platforms())
    return sanitize_live_probe(result)

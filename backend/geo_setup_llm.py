"""
Structured Gemini calls for **setup wizard**: products & services (+ prompts) and competitors.

Uses ``google.genai`` with ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``, or Vertex when configured
(``GEMINI_USE_VERTEX_AI``, ``GOOGLE_CLOUD_PROJECT``) — same precedence idea as ``competitor_suggest``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from ai_impact.model_artifact import validate_category

GEMINI_MODEL_DEFAULT = (os.environ.get("GEMINI_SETUP_MODEL") or "gemini-3.5-flash").strip()
MAX_OUTPUT_TOKENS = 8192
TEMPERATURE = 0.9
TOP_P = 0.95
SITE_CATEGORY_CLASSIFIER = "gemini-site-category-v1"
SITE_CATEGORY_CLASSIFIER_WIZARD = "gemini-site-category-v1-wizard-suggest-products"

# Wizard defaults: five lines × five prompts (matches structured schema).
DEFAULT_MAX_PRODUCTS = 5
DEFAULT_PROMPTS_PER_PRODUCT = 5


class ProductsAndServicesWithPrompts(BaseModel):
    product_or_service: str = Field(description="Short product or service label")
    prompts: list[str] = Field(
        description="Exactly five user-style questions for an AI assistant",
        min_length=5,
        max_length=5,
    )


class ProductsAndServicesSuggestion(BaseModel):
    category: str = Field(
        description=(
            "Exactly one production model category: publisher, advertiser-retail, "
            "or advertiser-services"
        )
    )
    products: list[ProductsAndServicesWithPrompts] = Field(
        description="Main product or service lines with exploratory AI prompts"
    )


class CompetitorWithWebsite(BaseModel):
    competitor_brand: str = Field(description="Brand name of competitor")
    competitor_website: str = Field(description="HTTPS homepage or site URL for the competitor")


class OtherBrandFromReplies(BaseModel):
    brand_name: str = Field(description="Distinct commercial brand or company mentioned in the replies")
    website_url: str = Field(
        default="",
        description="Canonical HTTPS homepage if reasonably known, else empty string",
    )


DEFAULT_PERSONAS = 5
DEFAULT_PROMPTS_PER_PERSONA = 5
MAX_PERSONAS = 8


class PersonaSuggestion(BaseModel):
    name: str = Field(description="Short persona label, e.g. 'First-time home buyer'")
    description: str = Field(
        description="One or two sentences: who they are, what they need, and what triggers a purchase"
    )


class PersonaPrompt(BaseModel):
    prompt: str = Field(description="A question this persona would ask an AI assistant")
    product_or_service: str = Field(
        description="The supplied product_or_service this prompt is most about, copied verbatim"
    )


class PersonaWithPrompts(BaseModel):
    name: str = Field(description="Persona name, copied verbatim from the supplied list")
    prompts: list[PersonaPrompt] = Field(
        description="Exactly five prompts this persona would ask",
        min_length=1,
        max_length=8,
    )


class SiteModelCategory(BaseModel):
    category: str = Field(
        description=(
            "Exactly one production model category: publisher, advertiser-retail, "
            "or advertiser-services"
        )
    )


def _market_context_lines(market_country: str, market_country_code: str) -> str:
    c = (market_country or "").strip()
    code = (market_country_code or "").strip().upper()
    if c and code:
        return f"The target market for all suggestions is **{c}** (ISO: **{code}**). Wording and examples must fit that market."
    if c:
        return f"The target market for all suggestions is **{c}**."
    if code:
        return f"The target market for all suggestions is the country/region with ISO code **{code}**."
    return "If the site clearly serves a single country, bias prompts and examples toward that country; otherwise keep prompts geographically neutral."


def _language_context_lines(language: str, language_name: str) -> str:
    code = (language or "en").strip().lower() or "en"
    name = (language_name or "").strip() or ("English" if code == "en" else code)
    return (
        f"Write every generated consumer prompt in natural {name} ({code}). "
        "Keep the product_or_service values exactly as supplied; only the prompts are localized."
    )


def products_and_services_user_prompt(
    website_url: str,
    *,
    market_country: str = "",
    market_country_code: str = "",
    max_products: int = DEFAULT_MAX_PRODUCTS,
    prompts_per_product: int = DEFAULT_PROMPTS_PER_PRODUCT,
) -> str:
    mp = max(1, min(int(max_products) or DEFAULT_MAX_PRODUCTS, 12))
    pp = max(1, min(int(prompts_per_product) or DEFAULT_PROMPTS_PER_PRODUCT, 8))
    mkt = _market_context_lines(market_country, market_country_code)
    return f"""
You are the sales lead for {website_url}.
You want to summarise the main products or services that {website_url} offers,
and classify the site's primary business model for an AI-traffic impact model.

{mkt}

Return a JSON object with:
1. ``category`` — exactly one of:
   - publisher: primarily publishes editorial or informational content for an audience,
     commonly monetised through advertising, subscriptions, sponsorship, or affiliate traffic.
     No ecommerce or selling of goods/services as the primary model.
   - advertiser-retail: primarily sells physical products or retail inventory to consumers or
     businesses, including ecommerce and omnichannel retailers.
   - advertiser-services: primarily sells services, software, financial products, travel,
     bookings, appointments, banking, agencies, utilities, or other non-retail offerings.
   Classify the site's primary business model, not an incidental blog, shop, or content section.
2. ``products`` — an array of **exactly {mp}** products or services that {website_url} offers
   (prioritise the most important lines for revenue or traffic). For publishers, use the main
   content verticals or topics instead of ecommerce offerings.
   For **each** line, suggest **exactly {pp}** prompts that a consumer would likely ask an AI
   assistant about that product, service, or topic.
Focus on general prompts that are exploratory and not specific to a single brand name (the assistant may still mention brands).
Ensure prompts are concrete and market-appropriate per the target market above.

Example products and services for a brand that sells train tickets:
- Train tickets
- Travel insurance
- Package holidays

Example prompts for a brand that sells train tickets in the UK:
- Where can I buy cheap train tickets in the UK?
- What is the best website for package holidays in the UK?
- What is the cheapest travel insurance website in the UK?
""".strip()


def build_competitor_prompt(
    website_url: str,
    products_and_services: list[str],
    *,
    market_country: str = "",
    market_country_code: str = "",
) -> str:
    lines = "\n".join(f"- {p}" for p in products_and_services if str(p).strip())
    if not lines.strip():
        lines = "(none identified — infer from the site URL and category.)"
    mkt = _market_context_lines(market_country, market_country_code)
    return f"""
You are the sales lead for {website_url}.
You want to identify the main competitor brands to {website_url}.

{mkt}

Suggest up to 10 competitors to {website_url} that operate in the **same market** and product space as the brand,
given the following products and services already identified for {website_url}:

{lines}

Return HTTPS homepage URLs where possible. Prefer competitors a shopper in this market would genuinely compare;
avoid unrelated global brands unless they directly compete on these lines.
""".strip()


def build_genai_client() -> genai.Client:
    """Prefer API key (Google AI Studio); else Vertex with ADC.

    Resolves keys the same way as :mod:`competitor_suggest` (``os.environ`` and nested tables in
    repo ``.streamlit/secrets.toml``) so nested TOML keys work.
    """
    from competitor_suggest import _gemini_api_key, _get_config, _truthy_env as _cfg_truthy

    api_key = _gemini_api_key().strip()
    if api_key:
        return genai.Client(api_key=api_key)
    if _cfg_truthy("GEMINI_USE_VERTEX_AI"):
        project = (_get_config("GOOGLE_CLOUD_PROJECT") or "").strip()
        location = (_get_config("GOOGLE_CLOUD_LOCATION") or "europe-west1").strip()
        if project:
            return genai.Client(vertexai=True, project=project, location=location)
    raise ValueError(
        "Configure Gemini for setup suggestions: set **GEMINI_API_KEY** or **GOOGLE_API_KEY** "
        "(environment or top-level / ``[llm]`` / ``[gemini]`` in ``.streamlit/secrets.toml``), "
        "or **GEMINI_USE_VERTEX_AI=1** with **GOOGLE_CLOUD_PROJECT** and ADC."
    )


def _generation_config(*, response_schema: Any) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=TEMPERATURE,
        top_p=TOP_P,
        response_mime_type="application/json",
        response_schema=response_schema,
    )


def _parse_json_list(raw: str) -> list[Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Empty model response.")
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")
    return data


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Empty model response.")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    return data


def site_model_category_user_prompt(
    website_url: str,
    *,
    brand_name: str = "",
    industry: str = "",
    products_and_services: list[str] | None = None,
) -> str:
    offerings = [
        str(value).strip()
        for value in (products_and_services or [])
        if str(value).strip()
    ]
    offerings_text = "\n".join(f"- {value}" for value in offerings) or "(not supplied)"
    return f"""
Classify the primary business model of this website for an AI-traffic impact model.

Website: {website_url.strip()}
Brand: {(brand_name or "").strip() or "(not supplied)"}
Industry: {(industry or "").strip() or "(not supplied)"}
Products/services selected during setup:
{offerings_text}

Return exactly one category:
- publisher: primarily publishes editorial or informational content for an audience, commonly
  monetised through advertising, subscriptions, sponsorship, or affiliate traffic.
- advertiser-retail: primarily sells physical products or retail inventory to consumers or
  businesses, including ecommerce and omnichannel retailers.
- advertiser-services: primarily sells services, software, financial products, travel,
  bookings, utilities, or other non-retail offerings.

Classify the site's primary business model, not an incidental blog, shop, or content section.
The ``category`` value must exactly match one of the three labels above.
""".strip()


def classify_site_model_category(
    website_url: str,
    *,
    brand_name: str = "",
    industry: str = "",
    products_and_services: list[str] | None = None,
    model_id: str | None = None,
) -> dict[str, str]:
    """Return a validated model category and Gemini classifier provenance."""
    client = build_genai_client()
    model = (model_id or GEMINI_MODEL_DEFAULT).strip()
    if not model:
        raise ValueError("Gemini site-category model ID is empty")
    prompt = site_model_category_user_prompt(
        website_url,
        brand_name=brand_name,
        industry=industry,
        products_and_services=products_and_services,
    )
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=_generation_config(response_schema=SiteModelCategory),
    )
    parsed = SiteModelCategory.model_validate(_parse_json_object(response.text or ""))
    category = validate_category(parsed.category)
    return {
        "category": category,
        "classifier": SITE_CATEGORY_CLASSIFIER,
        "provider": "google-gemini",
        "model": model,
    }


def suggest_products_and_services(
    website_url: str,
    *,
    market_country: str = "",
    market_country_code: str = "",
    model_id: str | None = None,
    max_products: int = DEFAULT_MAX_PRODUCTS,
    prompts_per_product: int = DEFAULT_PROMPTS_PER_PRODUCT,
) -> dict[str, Any]:
    """Return ``{"rows": [...], "category": "<validated model_category>"}``.

    Each row has ``product_or_service`` and ``prompts`` (fixed length per ``prompts_per_product``).
    """
    client = build_genai_client()
    model = (model_id or GEMINI_MODEL_DEFAULT).strip()
    cfg = _generation_config(response_schema=ProductsAndServicesSuggestion)
    mp = max(1, min(int(max_products) or DEFAULT_MAX_PRODUCTS, 12))
    pp = max(1, min(int(prompts_per_product) or DEFAULT_PROMPTS_PER_PRODUCT, 8))
    up = products_and_services_user_prompt(
        website_url.strip(),
        market_country=market_country,
        market_country_code=market_country_code,
        max_products=mp,
        prompts_per_product=pp,
    )
    resp = client.models.generate_content(model=model, contents=up, config=cfg)
    parsed = ProductsAndServicesSuggestion.model_validate(_parse_json_object(resp.text or ""))
    category = validate_category(parsed.category)
    items = [p.model_dump() for p in parsed.products[:mp]]
    out: list[dict[str, Any]] = []
    for r in items:
        prs = [str(p).strip() for p in (r.get("prompts") or []) if str(p).strip()]
        if len(prs) > pp:
            prs = prs[:pp]
        while len(prs) < pp:
            prs.append("")
        prs = prs[:pp]
        label = str(r.get("product_or_service") or "").strip()
        if label and any(prs):
            out.append({"product_or_service": label, "prompts": prs})
    return {"rows": out, "category": category}


def prompts_for_product_lines_user_prompt(
    website_url: str,
    product_lines: list[str],
    *,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "en",
    language_name: str = "English",
    prompts_per_product: int = DEFAULT_PROMPTS_PER_PRODUCT,
) -> str:
    pp = max(1, min(int(prompts_per_product) or DEFAULT_PROMPTS_PER_PRODUCT, 8))
    mkt = _market_context_lines(market_country, market_country_code)
    lang = _language_context_lines(language, language_name)
    lines = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(product_lines) if str(p).strip())
    return f"""
You are the sales lead for {website_url}.
The user has already chosen these products or services for {website_url} (do not rename or replace them):

{lines}

{mkt}
{lang}

Return a JSON array with **exactly {len(product_lines)}** objects, in the **same order** as the numbered list above.
Each object must use the **exact** ``product_or_service`` string from that line (copy verbatim).
For each line, suggest **exactly {pp}** prompts that a consumer would likely ask an AI assistant about that product or service.
Focus on general exploratory prompts, not tied to a single brand name.
""".strip()


def suggest_prompts_for_product_lines(
    website_url: str,
    product_lines: list[str],
    *,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "en",
    language_name: str = "English",
    model_id: str | None = None,
    prompts_per_product: int = DEFAULT_PROMPTS_PER_PRODUCT,
) -> list[dict[str, Any]]:
    """
    Generate shopper-style prompts for user-supplied product/service labels (wizard custom lines).
    """
    requested = [str(p).strip() for p in product_lines if str(p).strip()]
    if not requested:
        return []

    client = build_genai_client()
    model = (model_id or GEMINI_MODEL_DEFAULT).strip()
    pp = max(1, min(int(prompts_per_product) or DEFAULT_PROMPTS_PER_PRODUCT, 8))
    cfg = _generation_config(response_schema=list[ProductsAndServicesWithPrompts])
    up = prompts_for_product_lines_user_prompt(
        website_url.strip(),
        requested,
        market_country=market_country,
        market_country_code=market_country_code,
        language=language,
        language_name=language_name,
        prompts_per_product=pp,
    )
    resp = client.models.generate_content(model=model, contents=up, config=cfg)
    items = _parse_json_list(resp.text or "")
    parsed = [ProductsAndServicesWithPrompts.model_validate(x).model_dump() for x in items]

    by_label: dict[str, list[str]] = {}
    for r in parsed:
        label = str(r.get("product_or_service") or "").strip()
        if not label:
            continue
        prs = [str(p).strip() for p in (r.get("prompts") or []) if str(p).strip()]
        if len(prs) > pp:
            prs = prs[:pp]
        if label and prs:
            by_label[label.lower()] = prs

    out: list[dict[str, Any]] = []
    for i, label in enumerate(requested):
        prs = by_label.get(label.lower())
        if not prs and i < len(parsed):
            fallback = parsed[i]
            prs = [str(p).strip() for p in (fallback.get("prompts") or []) if str(p).strip()][:pp]
        if not prs:
            continue
        out.append({"product_or_service": label, "prompts": prs})
    return out


def personas_user_prompt(
    website_url: str,
    *,
    brand_name: str = "",
    industry: str = "",
    product_lines: list[str] | None = None,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "en",
    language_name: str = "English",
    count: int = DEFAULT_PERSONAS,
) -> str:
    n = max(1, min(int(count) or DEFAULT_PERSONAS, MAX_PERSONAS))
    brand = (brand_name or "").strip() or website_url
    mkt = _market_context_lines(market_country, market_country_code)
    lang_code = (language or "en").strip().lower() or "en"
    lang_name = (language_name or "").strip() or ("English" if lang_code == "en" else lang_code)
    industry_line = f"Industry: {industry.strip()}." if (industry or "").strip() else ""
    lines = "\n".join(f"- {p}" for p in (product_lines or []) if str(p).strip())
    products_block = f"Products or services in scope:\n{lines}" if lines else ""
    return f"""
You are a senior marketing strategist for {brand} ({website_url}).
{industry_line}
{products_block}

{mkt}

Identify **exactly {n}** distinct buyer personas: the groups of people most likely to purchase from {brand}.
Personas must be realistic purchaser segments for this market (not job titles inside {brand}), clearly
different from each other in needs, life stage or buying situation.

Return a JSON array of {n} objects with:
- ``name``: a short, memorable persona label (2-5 words).
- ``description``: one or two sentences describing who they are, what they need and what triggers a purchase.

Write names and descriptions in natural {lang_name} ({lang_code}).
""".strip()


def suggest_personas(
    website_url: str,
    *,
    brand_name: str = "",
    industry: str = "",
    product_lines: list[str] | None = None,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "en",
    language_name: str = "English",
    count: int = DEFAULT_PERSONAS,
    model_id: str | None = None,
) -> list[dict[str, str]]:
    """Gemini: likely purchaser personas for the brand (wizard Personas step)."""
    n = max(1, min(int(count) or DEFAULT_PERSONAS, MAX_PERSONAS))
    client = build_genai_client()
    model = (model_id or GEMINI_MODEL_DEFAULT).strip()
    cfg = _generation_config(response_schema=list[PersonaSuggestion])
    up = personas_user_prompt(
        website_url.strip(),
        brand_name=brand_name,
        industry=industry,
        product_lines=product_lines,
        market_country=market_country,
        market_country_code=market_country_code,
        language=language,
        language_name=language_name,
        count=n,
    )
    resp = client.models.generate_content(model=model, contents=up, config=cfg)
    items = _parse_json_list(resp.text or "")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        persona = PersonaSuggestion.model_validate(item)
        name = persona.name.strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "description": persona.description.strip()})
        if len(out) >= n:
            break
    return out


def persona_prompts_user_prompt(
    website_url: str,
    personas: list[dict[str, str]],
    product_lines: list[str],
    *,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "en",
    language_name: str = "English",
    prompts_per_persona: int = DEFAULT_PROMPTS_PER_PERSONA,
) -> str:
    pp = max(1, min(int(prompts_per_persona) or DEFAULT_PROMPTS_PER_PERSONA, 8))
    mkt = _market_context_lines(market_country, market_country_code)
    lang = _language_context_lines(language, language_name)
    persona_lines = "\n".join(
        f"{i + 1}. {p['name']}: {p.get('description') or ''}".strip()
        for i, p in enumerate(personas)
    )
    product_list = "\n".join(f"- {p}" for p in product_lines)
    return f"""
You are the sales lead for {website_url}.
These are the buyer personas (do not rename them):

{persona_lines}

These are the products or services in scope (``product_or_service`` values must be copied verbatim from this list):

{product_list}

{mkt}
{lang}

Return a JSON array with **exactly {len(personas)}** objects, in the same order as the personas above.
Each object has ``name`` (the exact persona name) and ``prompts``: **exactly {pp}** questions that this persona
would realistically type into an AI assistant (ChatGPT, Gemini, Google AI Overviews) while researching or choosing
a purchase. Write them in the persona's own voice and situation, keep them general and exploratory, and do not
name {website_url} or its brand. For each prompt set ``product_or_service`` to the single listed product or
service it is most about. Prompts must be distinct across all personas.
""".strip()


def _match_product_line(label: str, product_lines: list[str]) -> str:
    raw = (label or "").strip().lower()
    if not product_lines:
        return (label or "").strip()
    for p in product_lines:
        if p.lower() == raw:
            return p
    for p in product_lines:
        pl = p.lower()
        if raw and (raw in pl or pl in raw):
            return p
    return product_lines[0]


def suggest_prompts_for_personas(
    website_url: str,
    personas: list[dict[str, str]],
    product_lines: list[str],
    *,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "en",
    language_name: str = "English",
    prompts_per_persona: int = DEFAULT_PROMPTS_PER_PERSONA,
    model_id: str | None = None,
) -> list[dict[str, Any]]:
    """Gemini: persona-voiced prompts, each mapped to one of the supplied product lines.

    Returns ``[{"name": persona, "prompts": [{"prompt", "product_or_service"}]}]`` in persona order.
    """
    clean_personas = [
        {"name": str(p.get("name") or "").strip(), "description": str(p.get("description") or "").strip()}
        for p in personas
        if str(p.get("name") or "").strip()
    ][:MAX_PERSONAS]
    lines = [str(p).strip() for p in product_lines if str(p).strip()]
    if not clean_personas or not lines:
        return []

    pp = max(1, min(int(prompts_per_persona) or DEFAULT_PROMPTS_PER_PERSONA, 8))
    client = build_genai_client()
    model = (model_id or GEMINI_MODEL_DEFAULT).strip()
    cfg = _generation_config(response_schema=list[PersonaWithPrompts])
    up = persona_prompts_user_prompt(
        website_url.strip(),
        clean_personas,
        lines,
        market_country=market_country,
        market_country_code=market_country_code,
        language=language,
        language_name=language_name,
        prompts_per_persona=pp,
    )
    resp = client.models.generate_content(model=model, contents=up, config=cfg)
    items = _parse_json_list(resp.text or "")
    parsed = [PersonaWithPrompts.model_validate(x) for x in items]
    by_name = {p.name.strip().lower(): p for p in parsed}

    seen_prompts: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, persona in enumerate(clean_personas):
        match = by_name.get(persona["name"].lower()) or (parsed[i] if i < len(parsed) else None)
        if match is None:
            continue
        prompts: list[dict[str, str]] = []
        for item in match.prompts:
            text = item.prompt.strip()
            if not text or text.lower() in seen_prompts:
                continue
            seen_prompts.add(text.lower())
            prompts.append(
                {"prompt": text, "product_or_service": _match_product_line(item.product_or_service, lines)}
            )
            if len(prompts) >= pp:
                break
        if prompts:
            out.append({"name": persona["name"], "prompts": prompts})
    return out


def suggest_competitors(
    website_url: str,
    product_or_service_names: list[str],
    *,
    market_country: str = "",
    market_country_code: str = "",
    model_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return validated rows: ``competitor_brand``, ``competitor_website``."""
    client = build_genai_client()
    model = (model_id or GEMINI_MODEL_DEFAULT).strip()
    cfg = _generation_config(response_schema=list[CompetitorWithWebsite])
    prompt = build_competitor_prompt(
        website_url.strip(),
        list(product_or_service_names),
        market_country=market_country,
        market_country_code=market_country_code,
    )
    resp = client.models.generate_content(model=model, contents=prompt, config=cfg)
    items = _parse_json_list(resp.text or "")
    return [CompetitorWithWebsite.model_validate(x).model_dump() for x in items]


def _bundle_probe_rows_for_brand_detection(rows: list[dict[str, Any]], *, max_chars: int = 48000) -> str:
    chunks: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pq = str(row.get("prompt") or "").strip()[:800]
        replies = []
        for platform, label in (
            ("gemini", "GEMINI"),
            ("openai", "OPENAI"),
            ("google_aio", "GOOGLE_AI_SUMMARIES"),
            ("claude", "CLAUDE"),
        ):
            reply = str(row.get(f"{platform}_response") or "").strip()
            if reply:
                replies.append(f"--- {label}_REPLY (excerpt):\n{reply[:4500]}")
        chunks.append(f"---\nUSER_PROMPT:\n{pq}\n" + "\n".join(replies) + "\n")
    blob = "\n".join(chunks)
    return blob[:max_chars] if len(blob) > max_chars else blob


def reply_detected_brands_user_prompt(
    *,
    primary_brand: str,
    primary_site_url: str,
    market_country: str,
    market_country_code: str,
    bundled_replies: str,
) -> str:
    mkt = _market_context_lines(market_country, market_country_code)
    return f"""
You audit AI assistant replies for a GEO / AI-search visibility study.

Primary brand (do NOT list as a competitor): **{primary_brand}**
Primary site: **{primary_site_url}**

{mkt}

Below are excerpts from AI assistant answers to shopper prompts about overlapping topics.
Identify **other commercial companies or product brands** clearly mentioned as options or alternatives — names a consumer would recognise.

Rules:
- Exclude **{primary_brand}** and trivial substring overlaps of it.
- A result must be a named commercial entity, never a pronoun, preposition, adjective, generic noun, heading word, or sentence-opening word.
- Use hyperlinks as strong entity evidence: markdown link labels and their destination domains often identify the canonical brand. Merge the written name and linked domain into one result.
- Prefer entities supported by a hyperlink/domain, a recommendation heading, or repeated proper-name usage. Do not infer an entity from an isolated lowercase word.
- Exclude retailers and marketplaces used only in "where to buy" links; those are vendors, not competitors.
- Exclude generic words (for example "for", "this", "where", "known", "the airline", "your bank").
- Prefer brands that plausibly compete for the same customers in the stated market.
- Return **distinct** brands; merge duplicates.
- If website unknown, use an empty string for website_url.

ASSISTANT REPLY EXCERPTS:
{bundled_replies}
""".strip()


def suggest_reply_detected_competitor_brands(
    per_prompt_rows: list[dict[str, Any]],
    *,
    primary_brand: str,
    primary_site_url: str,
    market_country: str = "",
    market_country_code: str = "",
    model_id: str | None = None,
) -> list[dict[str, str]]:
    """
    One structured Gemini pass over probe reply excerpts; returns ``brand_name`` / ``website_url`` rows
    used to widen mention-based SOV (substring scan), not legal truth of competition.
    """
    brand = (primary_brand or "").strip()
    if not brand:
        return []
    bundle = _bundle_probe_rows_for_brand_detection(per_prompt_rows)
    if not bundle.strip():
        return []
    client = build_genai_client()
    model = (model_id or GEMINI_MODEL_DEFAULT).strip()
    cfg = _generation_config(response_schema=list[OtherBrandFromReplies])
    up = reply_detected_brands_user_prompt(
        primary_brand=brand,
        primary_site_url=(primary_site_url or "").strip(),
        market_country=market_country,
        market_country_code=market_country_code,
        bundled_replies=bundle,
    )
    resp = client.models.generate_content(model=model, contents=up, config=cfg)
    items = _parse_json_list(resp.text or "")
    validated = [OtherBrandFromReplies.model_validate(x).model_dump() for x in items]
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    pb = brand.lower()
    for x in validated:
        bn = str(x.get("brand_name") or "").strip()
        if len(bn) < 2 or bn.lower() == pb or bn.lower() in seen:
            continue
        if pb and pb in bn.lower() and len(bn) <= len(pb) + 2:
            continue
        seen.add(bn.lower())
        wu = str(x.get("website_url") or "").strip()
        out.append({"brand_name": bn, "website_url": wu})
    return out[:30]


def flatten_product_prompts(rows: list[dict[str, Any]]) -> list[str]:
    """All consumer prompts from product rows, stable order, de-duplicated case-insensitively."""
    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        for p in r.get("prompts") or []:
            s = str(p).strip()
            if not s:
                continue
            k = s.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
    return out


def normalize_competitor_url(raw: str) -> str:
    from geo_urls import normalize_competitor_url as _norm

    return _norm(raw)


def competitor_rows_to_urls(rows: list[dict[str, Any]], *, max_n: int = 5) -> list[str]:
    urls: list[str] = []
    for r in rows[:max_n]:
        u = normalize_competitor_url(str(r.get("competitor_website") or ""))
        if u:
            urls.append(u)
    return urls

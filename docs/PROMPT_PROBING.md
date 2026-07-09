# Prompt Probing Research

How the SEO/GEO tool probes AI platforms with user prompts, extracts citations, and scores brand/competitor visibility.

---

## Overview

The **live prompt probe** runs each configured prompt against four AI platforms simultaneously and scores:

- **Share of Voice (SOV)** — what percentage of prompts mention the brand vs competitors
- **Sentiment** — positive / neutral / negative tone when the brand is mentioned
- **Citations** — URLs and domains referenced in the AI responses

The goal is to replicate, as closely as possible, what a real user would see if they typed the same prompt into each platform's consumer-facing tool.

---

## Platform Methods

### 1. ChatGPT (OpenAI)

| Property | Value |
|---|---|
| API | OpenAI Responses API — `POST /v1/responses` |
| Model | `gpt-4o-search-preview` (overridable via `OPENAI_SEARCH_MODEL`) |
| Search tool | `web_search_preview` |
| Citation format | Structured `url_citation` annotations in the response output |
| Fallback | Falls back to `gpt-4o-mini` chat completion if Responses API unavailable |
| Market context | System prompt via `live_assistant_system_instruction(country, country_code)` |

**Consumer equivalent:** chatgpt.com with Browse enabled (GPT-4o)

**How it works:** The Responses API is called with `web_search_preview` as a tool. When the model performs a search it returns `url_citation` content items alongside the response text. These are parsed directly as structured citations — much more reliable than regex extraction. The model may still fall back to training data for some query types; in those cases only regex-extracted citations are returned (labelled accordingly).

**Known limitation:** `gpt-4o-search-preview` via the Responses API does not always return `url_citation` annotations even when the search tool fires. Investigation ongoing into whether the Chat Completions endpoint (`/v1/chat/completions`) surfaces citations in a different format.

---

### 2. Gemini

| Property | Value |
|---|---|
| API | Gemini `generateContent` API |
| Model | `gemini-2.5-flash` (default; overridable via `GEMINI_CHAT_MODEL`) |
| Search tool | `google_search` tool (grounding) |
| Citation format | `groundingMetadata.groundingChunks[].web.{uri, title}` |
| Fallback | Falls back to plain `generateContent` (no grounding) if the grounded call fails |
| Market context | System prompt via `live_assistant_system_instruction(country, country_code)` |

**Consumer equivalent:** gemini.google.com with Google Search integration

**How it works:** The `google_search` tool is attached to the generation request. When Gemini searches, the response includes `groundingMetadata` with `groundingChunks` linking the response text to source URLs. These are extracted and resolved (see [Citation Resolution](#citation-resolution) below).

**Known limitation:** For well-known advisory queries (e.g. "best skincare brand for acne"), Gemini may answer entirely from training data without triggering a search, resulting in 0 grounding chunks. The `dynamicRetrievalConfig: MODE_ALWAYS` option is not available on the `google_search` tool (only on the older `google_search_retrieval` tool which is not compatible with Gemini 2.x). This is an API-level constraint.

---

### 3. Claude (Anthropic)

| Property | Value |
|---|---|
| API | Anthropic Messages API — `POST /v1/messages` |
| Model | `claude-haiku-4-5` (overridable via `ANTHROPIC_MODEL`) |
| Search tool | None — no web search available via API |
| Citation format | Regex extraction from response text |
| Fallback | N/A |
| Market context | System prompt via `live_assistant_system_instruction(country, country_code)` |

**Consumer equivalent:** claude.ai (which also does not browse by default)

**How it works:** The Claude API does not include a native web search tool. Prompts are sent as a standard chat completion with a market-aware system prompt. Any URLs the model includes inline in its response are extracted by regex and labelled as "from model knowledge" (not live search sources).

**Accuracy note:** This closely matches the consumer claude.ai experience since neither version performs web search by default. Citations are training-data URLs, not independently sourced.

**Future improvement:** Integrate a third-party search tool (Brave Search, Tavily, or Serper) via Claude's `tool_use` API to add real-time citations.

---

### 4. Google AI Summaries (AIO)

| Property | Value |
|---|---|
| API | Gemini `generateContent` API (separate model instance) |
| Model | `gemini-2.5-flash` |
| Search tool | `google_search` grounding |
| Citation format | `groundingMetadata.groundingChunks` → redirect resolution |
| System prompt | Dedicated AIO prompt via `_aio_system_instruction(country, country_code)` |
| Market context | Injected into AIO system prompt from wizard `onboarding_context.json` |

**Consumer equivalent:** google.com — AI Overview panel shown above search results

**How it works:** A separate Gemini call is made with a dedicated system prompt designed to replicate the Google AI Overview tone and behaviour. The system prompt explicitly instructs Gemini to use Google Search and reference specific websites found via search. The grounding metadata is parsed for source citations.

Gemini's grounding API wraps all cited URLs inside `vertexaisearch.cloud.google.com/grounding-api-redirect/<token>` redirects. A parallel batch of HTTP HEAD requests is issued to resolve these to the real destination URLs (6-second timeout per redirect). Citations that fail to resolve retain the redirect URL with an `unresolved_redirect` flag and a display domain derived from the page title.

**Market context:** The `_aio_system_instruction()` function appends market-specific language to the system prompt:
> "The user is based in {country}. Prioritise sources, brands, retailers, products, prices, and recommendations that are relevant to that market. Use local spelling, currency, and brand names appropriate for that country."

---

## Citation Extraction

### Extraction pipeline

1. **Structured API citations** (highest confidence)
   - OpenAI `url_citation` annotations from the Responses API
   - Gemini `groundingChunks` URIs from grounding metadata

2. **Regex extraction** (fallback / supplement)
   - Pattern: `https?://[^\s<>"')\]]+` applied to response text
   - Captures URLs the model writes inline (brand sites, retailer links)
   - These are from training data, not live web search

3. **Merge and deduplicate**
   - `_merge_citations(api_cits, response_text, brand_site_url)` combines both sources
   - Brand's own domain is filtered out
   - Each citation is classified into `content_type` and `channel_type` (Video, Article, eCommerce, etc.)

### Domain blocklist

The following domains are excluded from citations as they are infrastructure/navigation links rather than content sources:

- `google.com`, `google.co.uk`, `google.ca`, `google.de`, etc. (Maps, Consent, Accounts)
- `maps.google.com`, `consent.google.com`, `accounts.google.com`
- `youtube.com` (video platform — enriched separately)
- `twitter.com`, `x.com`, `facebook.com`, `instagram.com`, `linkedin.com`
- `wikipedia.org`, `wikidata.org`, `wikimedia.org`

### Vertex AI redirect resolution

Google AI Overviews cite sources via `vertexaisearch.cloud.google.com` redirect URLs. These are resolved in parallel using `concurrent.futures.ThreadPoolExecutor` with HTTP HEAD requests (6-second timeout). If resolution fails, the citation is kept with `unresolved_redirect: true` and the display domain is derived from the `title` field in the grounding metadata.

---

## Market Localisation

The setup wizard captures the primary market (country name + ISO 3166-1 alpha-2 code). This flows through:

```
onboarding_context.json
  └── geo_market_country / geo_market_country_code
       └── _primary_market_from_context()
            ├── live_assistant_system_instruction()   → Gemini, OpenAI, Claude probes
            └── _aio_system_instruction()             → Google AIO probe
```

When a market is configured, all platform system prompts include explicit localisation instructions:
- Preferred retailers, brands, product ranges for that market
- Local spelling conventions (e.g. "colour" not "color" for UK)
- Currency cues and locally available chains
- For AIO: direct instruction to prioritise local sources

Prompts are also geo-tagged: `ensure_prompt_contains_geo_locator()` appends the market phrase (e.g. "in the UK") to prompts that don't already contain a geographic reference.

---

## Accuracy Benchmark

Test prompt: **"what is the best skincare brand for acne?"** — UK market — 9 July 2026

| Platform | Our tool: citations | Consumer tool: citations | Content accuracy | Notes |
|---|---|---|---|---|
| ChatGPT | 11 (regex, UK retailers) | 0 explicit | Accurate | Both from training data. Our tool adds UK retail links (Boots, Tesco, ASDA, John Lewis) |
| Gemini | 0 | 0 explicit | Partial gap | Consumer Gemini more structured ("best for X" categories). Neither grounded for this query. |
| Claude | 1 (regex) | 0 (no browse) | Accurate | Very close match. Consumer Claude also doesn't browse. |
| Google AIO | 1 (unresolved redirect) | 5 numbered citations | Significant gap | Post-fix: content now UK-localised (Medik8, Acnecide, UK retail). Citation gap from redirect timeouts. |

### Key findings

- **Content quality** is broadly comparable to consumer tools across all platforms
- **Google AIO** has the largest gap: consumer shows UK-specific pricing and brands (Acnecide £7.12, Boots Online Doctor); our tool, post-fix, now references UK brands (Medik8, Acnecide) but resolves fewer citations
- **Citation attribution** remains a challenge: OpenAI and Gemini cite 0 URLs in their consumer responses for this query type, so the comparison baseline is also 0
- **Market context** is the highest-leverage fix: AIO went from generic/US-leaning responses to UK-specific brand and retailer mentions after the market context injection was added

---

## Running a Manual Probe

To test a prompt directly against all platforms:

```bash
cd seo-geo-tool
source .venv/bin/activate

python3 - <<'EOF'
import sys, json
sys.path.insert(0, 'backend')

from prompt_suggest import (
    gemini_answer_with_citations,
    openai_answer_with_citations,
    claude_answer_user_prompt,
    _merge_citations,
)
from google_aio import gemini_grounded_answer

PROMPT = "your prompt here"
MCC, MID = "United Kingdom", "GB"  # or your target market

for label, fn in [
    ("Gemini",      lambda: gemini_answer_with_citations(PROMPT, market_country=MCC, market_country_code=MID)),
    ("OpenAI",      lambda: openai_answer_with_citations(PROMPT, market_country=MCC, market_country_code=MID)),
]:
    text, api_cits = fn()
    cits = _merge_citations(api_cits, text, "")
    print(f"\n=== {label} === {len(cits)} citations")
    for c in cits: print(f"  {c['domain']} ({c.get('content_type','?')})")

text = claude_answer_user_prompt(PROMPT, market_country=MCC, market_country_code=MID)
cits = _merge_citations([], text, "")
print(f"\n=== Claude === {len(cits)} citations (regex)")

result = gemini_grounded_answer(PROMPT, market_country=MCC, market_country_code=MID)
api_cits = result.get("citations") or []
cits = _merge_citations(api_cits, result.get("response", ""), "")
print(f"\n=== Google AIO === {len(cits)} citations")
for c in cits: print(f"  {c['domain']} ({'unresolved redirect' if c.get('unresolved_redirect') else c.get('content_type','?')})")
EOF
```

---

## Key Files

| File | Purpose |
|---|---|
| `backend/prompt_suggest.py` | Live probe orchestration, citation extraction, SOV scoring |
| `backend/google_aio.py` | Google AI Overview probe, Vertex AI redirect resolution |
| `backend/geo_market.py` | Market resolution (`resolve_primary_market`) |
| `api/prompt_performance.py` | API endpoints for running and retrieving probes |
| `api/probe_platforms.py` | Platform enable/disable management |
| `web/src/components/PromptPerformanceSection.tsx` | Probe results UI |
| `web/src/components/CitationsPage.tsx` | Citations breakdown UI |

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | required | Gemini + Google AIO probes |
| `OPENAI_API_KEY` | required | OpenAI / ChatGPT probe |
| `ANTHROPIC_API_KEY` | required | Claude probe |
| `OPENAI_SEARCH_MODEL` | `gpt-4o-search-preview` | Model used for citation-enabled OpenAI probes |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | Model used for non-citation OpenAI calls |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Claude model |
| `GEMINI_GROUNDED_MODEL` | `gemini-2.5-flash` | Model used for AIO probes |

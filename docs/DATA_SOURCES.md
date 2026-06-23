# Data sources

How the tool collects data for audits. Each section lists the **fetching module**, **trigger**, and **output artifact(s)**.

---

## Overview

| Source | Integrated in audit? | Module(s) | Primary artifact |
|--------|---------------------|-----------|------------------|
| Site HTTP crawl | Yes | `backend/crawl-site.py` | `audit_summary.json` |
| Competitor crawl | Yes | `backend/crawl-site.py` | `competitors/`, `comparison.json` |
| Brand visibility | Yes | `backend/brand_visibility_scan.py` | Inside `audit_summary.json` |
| GA4 Data API | Optional | `backend/ga4_fetch.py`, `backend/ga4_data_api.py` | `ga4_traffic.json` |
| LLM live probes | Post-audit | `backend/prompt_suggest.py` | `prompt_performance_live_probe.json` |
| Gemini wizard | Setup | `backend/geo_setup_llm.py` | `onboarding_context.json` |
| Google Search Console | **No** | — | Manual verification only (scoring copy references GSC) |
| BigQuery / bulk GA4 | Research only | `research/*` | CSV in `research/` |

---

## 1. Site HTTP crawl

**Module:** `backend/crawl-site.py`  
**Called from:** `backend/create-report.py` (subprocess per site)

### What it collects

- **robots.txt** — fetched and parsed; AI crawler allow/block rules scored separately
- **llms.txt** — discovery file for AI systems
- **Sitemaps** — URL discovery; market-aware prioritisation via `sitemap_market.py`
- **Sample pages** — HTTP status, titles, meta, canonical, hreflang, body heuristics
- **JSON-LD** — structured data from pages; written to `jsonld/` and summarised in `json-ld.txt`
- **Open Graph / Twitter cards** — image URLs saved under `og_images/`
- **TLS / crawl infra** — certificate mode, redirect chains, response codes

### GA4-informed crawl (optional)

If `ga4_top_pages.json` exists (from a prior GA4 pull), top URLs by `screenPageViews` are prepended to the crawl queue so high-traffic pages are sampled first.

### Key outputs

```
audit_output/<host>_<hash>/
├── audit_summary.json    # Master crawl payload + inputs to scoring
├── robots.txt            # Copy or generated suggestion
├── llms.txt
├── json-ld.txt
├── jsonld/
├── og_images/
└── pages/                # Per-page extracts (embedded in summary JSON)
```

### Configuration

CLI flags on `backend/create-report.py` / `backend/crawl-site.py`: `--max-urls`, `--delay`, `--industry`, market country from wizard.

---

## 2. Competitor crawl

**Module:** `backend/crawl-site.py` (competitor mode)  
**Limit:** Up to 5 competitors per audit (web wizard allows more URLs for suggestions; audit caps at 5)

Each competitor gets a subdirectory:

```
competitors/<competitor-host>/
├── audit_summary.json
└── … (same structure as primary, labelled competitor)
```

**Comparison:** `backend/create-report.py` builds `comparison.json` and `comparison.md` with side-by-side category scores.

---

## 3. Brand visibility (off-site)

**Module:** `backend/brand_visibility_scan.py`  
**Called from:** `backend/crawl-site.py` during primary crawl

### Platforms checked

| Platform | Method |
|----------|--------|
| Wikipedia | Search API / page existence |
| YouTube | Channel/video search heuristics |
| Reddit | Subreddit/post search |
| LinkedIn | Company page signals |

Results are merged into `audit_summary.json` → `brand_visibility` and feed `score_brand_visibility()` in `backend/create-report.py`.

### Optional Reddit sentiment

If `requirements-brand-sentiment.txt` is installed (PyTorch + Transformers), Reddit thread text can be scored with DistilBERT SST-2. This is **optional** and excluded from the Cloud Run image.

---

## 4. Google Analytics 4 (GA4)

**Modules:** `backend/ga4_fetch.py`, `backend/ga4_data_api.py`, `backend/ga4_oauth.py`  
**Trigger:** User connects GA4 in wizard **or** `--ga4-property` on CLI

### Authentication

| Context | Auth |
|---------|------|
| Web wizard | User OAuth → session → temp ADC JSON for subprocess |
| CLI (`research/ga4_channel_export.py`) | OAuth token cache in `research/.ga4_oauth_token.json` |
| Headless / CI | Service account via `GOOGLE_APPLICATION_CREDENTIALS` |

OAuth flow (deployed app): `GET /api/ga4/login` → Google consent → `GET /api/ga4/callback` → `exchange_code()`.

Redirect URI: `{WEB_PUBLIC_ORIGIN}/api/ga4/callback` (Cloud Run: `{SERVICE_URL}/api/ga4/callback`).

### What is pulled

`ga4_fetch.fetch_ga4_traffic()` writes **`ga4_traffic.json`** containing:

| Section | Description |
|---------|-------------|
| `monthly_sessions` | Total vs AI-channel sessions by `yearMonth` |
| `monthly_ai_sessions_by_source` | Stacked bar data by `sessionSource` (AI bucket or heuristic) |
| `ai_channel_gaps` | AI-like referrers not in configured AI channel bucket |
| `conversion_rate` | Property-wide vs AI-segment ecommerce conversion |
| `monthly_ai_revenue_pct` | AI revenue share (if custom channel + revenue data) |
| `misallocated_ai_sources` | Sources that look AI-related but wrong channel |

### Channel dimension logic

- **With AI channel names** (e.g. `"AI"` in wizard): resolves `sessionCustomChannelGroup:<id>` via Metadata API (`ga4_data_api.resolve_session_custom_channel_dimension`).
- **Without AI labels:** uses `sessionDefaultChannelGroup` and heuristic AI source matching (`ga4_data_api.AI_TRAFFIC_SOURCE_SUBSTRINGS`).

Date ranges respect `GA4_START_DATE` / `GA4_END_DATE` with normalisation (`normalize_ga4_api_date`) and cap to last complete calendar month for charts.

### Top pages (optional)

`ga4_oauth.fetch_top_pages_last_90_days()` → `ga4_top_pages.json` for crawl prioritisation.

### On-demand GA4 narrative

`backend/insights_llm.py` can generate `ga4_ai_insights.json` (Gemini summary of traffic patterns) when requested from the UI.

### Research export (not audit pipeline)

`research/ga4_channel_export.py` pulls daily `sessions` + `ecommercePurchases` by **`sessionDefaultChannelGroup`** for econometric work. Independent of audit `ga4_traffic.json`.

---

## 5. LLM live probes (share of voice)

**Module:** `backend/prompt_suggest.py`  
**Orchestration:** `api/prompt_performance.py` → `run_post_audit_prompt_insights()`

### When it runs

After `report.html` is written (post-audit step). Requires API keys:

- `GEMINI_API_KEY` or Vertex (`GEMINI_USE_VERTEX_AI`)
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

### Process

1. Read prompts from `products_and_services.json` / `onboarding_context.json`.
2. `select_prompts_for_probing()` chooses a subset (cost/latency cap).
3. For each prompt × platform, call the model with no browsing (simulated user question).
4. Score mentions of client brand vs competitors (`api/sov_metrics.py` substring heuristics).
5. Write **`prompt_performance_live_probe.json`**.

Fatal platform errors can exclude a platform via `probe_excluded_platforms.json`.

---

## 6. Gemini wizard and setup

**Modules:** `geo_setup_llm.py`, `competitor_suggest.py`, `api/wizard.py`

During setup (before crawl):

| Step | Output |
|------|--------|
| Verify site URL | — |
| Suggest products/services | `products_and_services.json` |
| Suggest competitors | Wizard state → audit argv |
| Suggest probe prompts | Embedded in products JSON |
| `--accept-ai-defaults` (CLI) | `onboarding_context.json` via Gemini |

Transport: Gemini API key or Vertex ADC (`competitor_suggest.py` shared client).

---

## 7. On-demand LLM report sections

These are **not** part of the core crawl; users trigger them from the report UI.

| Feature | Module | Cache file |
|---------|--------|------------|
| Executive summary | `executive_summary_llm.py` | `executive_summary.json` |
| Recommendations | `recommendations_llm.py` | `recommendations.json` |
| GA4 AI insights | `insights_llm.py` | `ga4_ai_insights.json` |
| Probe reply sentiment | `insights_llm.py` | `prompt_performance_sentiment.json` |

All use structured Gemini prompts with audit JSON as context.

---

## 8. Domain suggest (API only)

**Module:** `domain_suggest.py`  
**Data:** `data/public_domains_tranco_head.txt`  
Autocomplete for wizard URL field — no audit artifact.

---

## Environment variables (data-related)

| Variable | Effect |
|----------|--------|
| `GA4_PROPERTY_ID` | Default property for CLI / env |
| `GA4_AI_CHANNEL_NAMES` | Comma-separated custom AI bucket labels |
| `GA4_START_DATE` / `GA4_END_DATE` | GA4 relative or ISO dates |
| `GA4_SESSION_CUSTOM_CHANNEL_DIMENSION` | Force specific Metadata API dimension |
| `GOOGLE_APPLICATION_CREDENTIALS` | Service account or user ADC JSON |
| `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | LLM probes and insights |
| `GEO_DATA_ROOT` | Root for `audit_output/` and `audit_archive/` |

---

## Troubleshooting data pulls

| Symptom | Check |
|---------|-------|
| Empty GA4 section | OAuth connected? Property id set? User has Viewer on property? |
| `PermissionDenied` on GA4 | `analytics.readonly` scope; refresh OAuth token |
| No probe results | API keys in env/secrets; `probe_excluded_platforms.json` |
| Thin crawl | `--max-urls`; add `ga4_top_pages.json`; check robots blocks |
| Competitor missing | URL normalisation; competitor cap (5) |

Log prefix `[GA4]` in subprocess stderr traces GA4 fetch steps (`ga4_data_api.ga4_log`).

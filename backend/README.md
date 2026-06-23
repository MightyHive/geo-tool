# Backend pipeline

Python modules for GEO audit crawling, scoring, report generation, GA4 integration, and LLM-assisted features. The FastAPI layer in `api/` imports these via `PYTHONPATH=backend` (or `api/__init__.py` bootstraps the path locally).

## Layout

| Module | Role |
|--------|------|
| `create-report.py` | Audit orchestrator: subprocess crawl → `score_audit()` → HTML reports |
| `crawl-site.py` | HTTP crawl (robots, llms.txt, sitemaps, pages, JSON-LD, brand scan) |
| `geo_app_env.py` | `BACKEND_ROOT`, `REPO_ROOT`, `ASSETS_ROOT`; dotenv loading |
| `report_copy.py` | Stakeholder-friendly wording for report priorities |
| `ga4_fetch.py` | Builds `ga4_traffic.json` for report appendix |
| `ga4_data_api.py` | GA4 Data API client, channel metadata, pagination |
| `ga4_oauth.py` | Google OAuth for GA4 (web session + CLI token cache) |
| `brand_visibility_scan.py` | Off-site presence (Wikipedia, YouTube, Reddit, LinkedIn) |
| `prompt_suggest.py` | Live multi-LLM probe answers |
| `insights_llm.py` | GA4 narratives and probe-reply sentiment |
| `geo_setup_llm.py` | Wizard Gemini helpers (products, competitors, prompts) |
| `competitor_suggest.py` | Gemini transport for competitor suggestions |
| `domain_suggest.py` | Tranco domain autocomplete and favicon helpers |
| `executive_summary_llm.py` | On-demand Gemini executive summary |
| `recommendations_llm.py` | On-demand Gemini action plan |
| `report_llm_util.py` | Shared LLM report utilities |
| `onboarding_suggestions.py` | Wizard onboarding helpers |
| `geo_market.py` | Primary market country resolution |
| `sitemap_market.py` | Regional sitemap prioritisation |
| `geo_urls.py` | URL normalisation helpers |

## Path constants (`geo_app_env.py`)

- **`BACKEND_ROOT`** — this directory
- **`REPO_ROOT`** — repository root (parent of `backend/`)
- **`ASSETS_ROOT`** — `REPO_ROOT / "assets"` (design CSS, sample robots/llms, static data)

## Running from the CLI

From the repo root with `backend` on `PYTHONPATH`:

```bash
export PYTHONPATH=backend
python backend/create-report.py https://www.example.com --out audit_output
```

Or:

```bash
cd backend && python create-report.py https://www.example.com --out ../audit_output
```

## Related directories

- `api/` — FastAPI HTTP layer; spawns `create-report.py` as a subprocess
- `assets/` — design templates, sample crawl files, static reference data
- `skills/` — markdown rubrics referenced by scoring and LLM copy
- `research/` — offline econometric scripts (not part of production audit UI)

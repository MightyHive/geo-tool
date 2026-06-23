# SEO / GEO Audit Tool

A Generative Engine Optimization (GEO) audit platform that crawls websites, scores AI visibility and technical readiness, compares competitors, pulls GA4 traffic (including AI channel buckets), and runs live LLM probes to measure share of voice. Results are delivered as interactive HTML reports, slide decks, and optional PDF exports.

**Primary interface:** React + TypeScript UI (`web/`) backed by FastAPI (`api/`), deployed to Google Cloud Run.

---

## What the tool does

1. **Crawls** the client site and up to five competitors (robots.txt, llms.txt, sitemaps, sample pages, JSON-LD, Open Graph).
2. **Scores** GEO readiness across three weighted categories (AI Visibility, Technical Setup, Content Quality & Structure).
3. **Optionally connects GA4** via Google OAuth to append AI traffic trends and channel-gap analysis.
4. **Runs live AI probes** (Gemini, OpenAI, Claude) against wizard-defined prompts and computes share of voice.
5. **Generates** `report.html`, `report_slides.html`, and on-demand Gemini executive summaries and recommendations.

---

## Quick start (local development)

### Prerequisites

- Python 3.12+
- Node.js 20+ (for the web UI)
- Google OAuth client (for sign-in and GA4) — see [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example)
- Optional: `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` for LLM features

### Setup

```bash
cd seo-geo-tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp env/.env.development.example env/.env.development
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit secrets.toml with your OAuth client id/secret and cookie_secret

cd web && npm install && cd ..
```

### Run the web app

```bash
./scripts/run_web_dev.sh
```

- UI: http://localhost:5173  
- API: http://127.0.0.1:8000/api/health  

### Run an audit from the CLI (no UI)

```bash
export PYTHONPATH=backend
python backend/create-report.py https://www.example.com \
  --competitor https://competitor.com \
  --out audit_output \
  --ga4-property 123456789
```

Output lands in `audit_output/<hostname>_<hash>/`.

---

## Documentation for developers

| Document | Contents |
|----------|----------|
| [**docs/ARCHITECTURE.md**](docs/ARCHITECTURE.md) | Repository layout, entry points, audit pipeline, deployment |
| [**docs/DATA_SOURCES.md**](docs/DATA_SOURCES.md) | How each data source is fetched (crawl, GA4, LLM probes, brand scan) |
| [**docs/ANALYSIS_AND_SCORING.md**](docs/ANALYSIS_AND_SCORING.md) | Scoring engine, subscores, post-audit analysis, output artifacts |
| [**deploy/README.md**](deploy/README.md) | Cloud Run staging/dev deployment |
| [**skills/**](skills/) | Rubric specs per audit pillar (citability, crawlers, GA4, etc.) |

---

## Repository layout (summary)

```
seo-geo-tool/
├── api/                 # FastAPI backend (audits, auth, GA4 OAuth, wizard, probes)
├── web/                 # React + Vite frontend
├── backend/             # Python audit pipeline (crawl, score, GA4, LLM helpers)
│   ├── create-report.py # Audit orchestrator: crawl → score → HTML
│   ├── crawl-site.py    # HTTP crawl and on-site artifact collection
│   └── geo_app_env.py   # BACKEND_ROOT, REPO_ROOT, ASSETS_ROOT, env loading
├── assets/              # Design CSS, reference templates, static data
│   ├── reference/       # robots.txt + llms.txt skeleton (tracked)
│   └── samples/         # Local demo audits (gitignored)
├── research/            # Offline research scripts (econometrics, bulk GA4 export)
├── skills/              # Markdown rubrics for each scoring pillar
├── audit_output/        # Per-run artifacts (local dev)
├── audit_archive/       # index.json of past runs
├── deploy/              # Dockerfile, Cloud Run manifests
├── env/                 # Per-environment .env files
└── scripts/             # Dev runners and deploy scripts
```

---

## Environment and secrets

| Mechanism | Purpose |
|-----------|---------|
| `APP_ENV` / `GEO_ENV` | `development` \| `staging` \| `production` — loads `env/.env.<env>` |
| `.streamlit/secrets.toml` | OAuth client, cookie secret, optional LLM keys |
| `env/.env.development` | `WEB_PUBLIC_ORIGIN`, optional GA4 defaults |
| `GEO_DATA_ROOT` | Writable data root (GCS mount `/var/geo-data` on Cloud Run) |

See `geo_app_env.py` for dotenv loading order.

---

## Key API routes

| Route | Description |
|-------|-------------|
| `POST /api/audits/run` | Start audit (SSE log stream) |
| `POST /api/audits/run-background` | Start audit (poll `run-status`) |
| `GET /api/audits/{id}/report.html` | Client report |
| `GET /api/ga4/login` | Start GA4 OAuth in wizard |
| `GET /api/wizard/*` | Setup wizard (products, competitors, prompts) |

Full route list: `api/main.py`.

---

## Requirements files

| File | Use |
|------|-----|
| `requirements.txt` | Web API, audit pipeline, local dev, and Cloud Run image |
| `requirements-brand-sentiment.txt` | Optional Reddit DistilBERT sentiment (PyTorch; not in Cloud Run) |
| `research/requirements.txt` | Offline econometric / GA4 export scripts |

---

## Contributing

- Match existing patterns in the module you touch (`backend/create-report.py` for scoring, `api/` for HTTP, `web/` for UI).
- Rubric changes should update the relevant file under `skills/` and the corresponding scorer in `backend/create-report.py`.
- Do not commit `secrets.toml`, `.env.*`, or `audit_output/` client data.

For pipeline and scoring detail, start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

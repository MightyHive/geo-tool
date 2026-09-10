# AI Traffic Impact Dashboard — Infrastructure

Estimates AI-influenced **SEO and Direct sessions** with a versioned hierarchical
Bayesian baseline. Purchases and CVR remain measured GA4 quantities; purchase impact
is not modeled.

This document is the source of truth for the production path. Research prototypes live
under `research/analysis/` (ECP); production code lives under `backend/ai_impact/`,
`api/ai_impact.py`, `jobs/`, and the React UI. Model econometrics:
`docs/AI_IMPACT_MODEL.md` (technical) and `docs/AI_IMPACT_MODEL_PLAIN.md` (plain English).

**UI note:** The **`ai-traffic-dashboard`** section (aliases: `ga4-traffic`, `ai-impact`)
opens with a short “two methods” intro, then two equal-height cards: **AI impact
estimate** (`AiImpactDashboard`, collapsible Config bar — available even when an
estimate already exists so users can re-run) stacked above
**Direct AI traffic** (the GA4 HTML iframe, when `has_report_html` is true).

---

## Current production model

| | |
| --- | --- |
| Artifact / `CURRENT` | `hierarchical-ai-v2-2026-09-05` |
| Panel | 61 web properties (apps excluded); fitted weeks 2023-06-04 → 2026-07-26 |
| Portfolio signal | Exact `All Sessions` denominator; through week starting **2026-08-30** |
| Ramp start | 2023-12-10 |
| Categories | `advertiser-retail` (37), `advertiser-services` (5), `publisher` (19) |
| Training | 4 chains × 1000 warmup × 1000 samples; promoted (0 divergences, \(\hat R=1.00\)) |
| Retrain cadence | Manual monthly baseline refit after refreshing GA4 + real Trends (no Trends extrapolation) |

Signal-only refresh can advance completed weeks without refitting posteriors. Site scoring still uses the frozen category \(\beta^{\text{AI}}_c\) draws until the next promoted baseline.

---

## User inputs

| Input | Auth / source | Notes |
| --- | --- | --- |
| GA4 property | Google OAuth `analytics.readonly` | **Required.** Same `/api/ga4/*` wizard flow; **Re-authenticate** if token expired |
| Conversion event | GA4 event name | Defaults to `purchase`; custom events use `eventName` + `eventCount` |
| Search Console property | Google OAuth `webmasters.readonly` | **Recommended.** `/api/gsc/*` (mirror GA4); estimates can run without it |
| Brand Google Trends | Auto-fetch job or manual CSV | **Required** for scoring. Prefer auto-fetch via `GOOGLE_TRENDS_JOB_NAME`; manual UK weekly Interest-over-time CSV is the fallback. Identify the brand term when multiple terms exist |
| Date range | Default ~2–3 years weekly | Configurable |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  React: /ai-traffic-dashboard                               │
│   - Intro: direct AI traffic vs estimated SEO/Direct impact │
│   - AiImpactDashboard (equal-height card, above iframe)     │
│   - Direct AI traffic iframe (equal-height card)            │
│   - Connect / re-auth GA4; optional GSC; brand Trends       │
│   - Show immediate posterior; poll for site refit           │
└───────────────────────────┬─────────────────────────────────┘
                            │ REST
┌───────────────────────────▼─────────────────────────────────┐
│  FastAPI  /api/ai-impact/*                                  │
│   POST /runs          → GA4 panel + category posterior      │
│   GET  /runs/{id}     → cold/refit state + latest estimate  │
│   GET  /gsc/*         → GSC OAuth (parallel to GA4)         │
└───────────────┬───────────────────┬─────────────────────────┘
                │                   │
                ▼                   ▼
        ┌───────────────┐  ┌────────────────┐
        │ GA4 Data API  │  │ GSC Search API │
        └───────┬───────┘  └───────┬────────┘
                └───────────────────┘
                           ▼
                 GCS / local: ai_impact_runs/{run_id}/
                   panel.csv, cold_start_estimate.json,
                   refit_status.json, estimate.json
```

**GA4 extract:** always use `research/ga4/ga4_channel_export.py` (`run_export`). The API
writes session ADC (`ga4_adc.json`) from the wizard OAuth session and sets
`GOOGLE_APPLICATION_CREDENTIALS` / `GA4_OAUTH_TOKEN_PATH`. On auth failure the run
returns `needs_ga4_reauth` and the UI offers Disconnect + Re-authenticate
(`/api/ga4/login?return_to=…`).

---

## Data contracts

### GA4 weekly (`ga4_weekly.csv`)

Produced by `ga4_channel_export.build_weekly_rows`:

| Column | Required |
| --- | --- |
| `week` | yes (Sunday-start) |
| `ai_sessions`, `seo_sessions`, `direct_sessions`, `ppc_sessions`, `other_traffic_sessions` | yes |
| `sessions`, `purchases` + channel `*_purchases` | yes |

`backend/ai_impact/panel.normalize_ga4_channel_weekly` maps this into estimate columns
(`other_sessions`, combined PPC → `branded_ppc_sessions`). Direct is exported as a
separate GA4 default-channel group because it is a fitted outcome.

### GSC daily → weekly (`gsc_daily.csv`)

| Column | Required |
| --- | --- |
| `date`, `clicks`, `impressions`, `ctr`, `position` | yes |

**Caveat:** Google changed impression measurement in **Sep 2025**. Do not compare
impressions across that break.

### Google Trends (`trends_weekly.csv`)

**Default (auto-fetch):** when `GOOGLE_TRENDS_JOB_NAME` is configured, creating an AI Impact
run without a manual upload enqueues the weekly Cloud Run scraper with the audit’s
`brand_name_used`. Output is stored under
`audit_output/<audit_id>/google_trends/` (`reference_weekly.csv`, `trends_weekly.csv`,
`meta.json`) and copied onto the run dir for the panel.

**Manual fallback:** `POST /api/ai-impact/trends-upload` validates an untouched CSV from the
Google Trends **Interest over time** chart. The upload must:

- use weekly rows and cover `2023-06-01` through approximately today;
- use United Kingdom as the location;
- contain one to five search-interest columns;
- retain the standard Google Trends `Week` header and CSV structure.

Validated columns are normalized to `week`, `trends_1` … `trends_5`. Exactly one is
designated as the brand term. GSC remains chart context and is never a model covariate.

---

## Estimation outputs (`estimate.json`)

Each outcome contains uncapped and capped posterior means with 94% credible intervals,
weekly actual/counterfactual series, sensitivity delta, category, estimate mode, and
model/signal artifact versions. The immediate mode is `category_posterior`; a completed
job replaces it with `site_refit`.

Weeks whose Sunday start is Dec 15 through Jan 7 are excluded, matching training.

## Artifact and scoring lifecycle

1. **Baseline train / promote.** `research/modelling/train_baseline_artifact.py`
   (locally or via `jobs/ai_impact_baseline_refit`) fits SEO and Direct × uncapped/capped
   for the three categories. Promotion requires fit diagnostics and hold-one-site-out
   direction agreement, then writes an immutable bundle and updates `CURRENT`.
2. **Signal refresh (optional).** `jobs/ai_signal_refresh` recomputes complete-week
   portfolio \(s_t\) for the artifact’s 61 training sites using exact `All Sessions`
   totals and reapplies the **frozen** transform. It does not change posteriors.
3. **Audit classify.** Audit start classifies and persists the site category plus
   Gemini provenance.
4. **Cold start.** The API immediately applies frozen category `beta_ai_cat` draws to
   the site’s observed SEO/Direct traffic (`category_posterior`). It never invents a
   site-level AI slope.
5. **Site-inclusive refit.** At eight completed, non-Christmas GA4 + brand-Trends weeks,
   `jobs/ai_impact_refit` is queued as Cloud Run Job
   `geo-audit-ai-impact-refit` (dev) / `geo-audit-staging-ai-impact-refit`
   (staging) in `geo-tool-emea-ds`. Deploy with
   `scripts/deploy_ai_impact_refit_job.sh`. Polling promotes its `site_refit`
   result.

Artifacts are immutable and selected by `CURRENT`/environment version. Runs record both
model and signal versions. Missing, unpromoted, or schema-incompatible artifacts fail
explicitly. Weeks newer than the signal artifact remain unavailable until refresh.

---

## OAuth scopes

| Product | Scope | Callback |
| --- | --- | --- |
| Sign-in | openid email profile | `/api/auth/callback` |
| GA4 | `analytics.readonly` | `/api/ga4/callback` |
| GSC | `webmasters.readonly` | `/api/gsc/callback` |

GA4 login accepts `return_to` (relative path) so AI Impact can re-auth without leaving
the report section.

---

## Repo layout

```
backend/ai_impact/          # estimation library (production)
api/ai_impact.py            # FastAPI router
api/gsc.py                  # GSC OAuth
jobs/
  ga4_channel_export/       # wraps research/ga4/ga4_channel_export.py
  gsc_export/
  ai_signal_refresh/        # optional 61-site complete-week portfolio signal
  ai_impact_baseline_refit/ # remote monthly portfolio baseline fit
  ai_impact_refit/          # asynchronous NumPyro site-inclusive fit
web/src/components/
  AiImpactDashboard.tsx
docs/AI_TRAFFIC_IMPACT_DASHBOARD.md
```

The service image includes `research/ga4/` (see `.dockerignore` exception) so
`run_export` is available in-process and in the GA4 job image. Broader `research/`
stays excluded.

---

## Local testing

```bash
# Estimate on an existing combined panel (no Google calls)
python -m backend.ai_impact.cli \
  --panel research/ga4/exports/combined/ga4_trends_weekly_euro_car_parts_241379560.csv \
  --window-weeks 13

# Or from ga4_channel_export weekly output
python -m backend.ai_impact.cli \
  --panel research/ga4/exports/daily/ga4_channel_weekly_euro_car_parts_241379560.csv \
  --window-weeks 13
```

The production API additionally requires an audit/category, a promoted model artifact,
and a designated brand Trends upload. Full NumPyro fitting belongs in the offline/job
image; the immediate web scorer imports only NumPy/Pandas.

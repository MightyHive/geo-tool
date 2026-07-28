# AI Traffic Impact Dashboard — Infrastructure

Estimates **AI-related session and purchase loss/gain** (direct AI channel + indirect
effects on SEO / overall traffic), and surfaces **session quality** (CVR) so a drop in
sessions can sit beside a rise in conversion.

This document is the source of truth for the production path. Research prototypes live
under `research/analysis/` (ECP); production code lives under `backend/ai_impact/`,
`api/ai_impact.py`, `jobs/`, and the React UI.

**UI note:** AI Impact Estimates is a collapsible control at the top of
**`ga4-traffic`** (AI Traffic Dashboard). The former `ai-impact` route redirects there.

---

## User inputs

| Input | Auth / source | Notes |
| --- | --- | --- |
| GA4 property | Google OAuth `analytics.readonly` | **Required.** Same `/api/ga4/*` wizard flow; **Re-authenticate** if token expired |
| Conversion event | GA4 event name | Defaults to `purchase`; custom events use `eventName` + `eventCount` |
| Search Console property | Google OAuth `webmasters.readonly` | **Recommended.** `/api/gsc/*` (mirror GA4); estimates can run without it |
| Google Trends CSV | Manual upload | **Optional.** Weekly Interest over time export for `2023-01-01` through today, UK, up to five terms |
| Date range | Default ~2–3 years weekly | Configurable |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  React: AiImpactDashboard (top of section ga4-traffic)      │
│   - Connect / re-auth GA4 (wizard OAuth + return_to)        │
│   - Optionally connect GSC and upload Google Trends CSV     │
│   - Run estimate - Show session/purchase ranges + CVR       │
└───────────────────────────┬─────────────────────────────────┘
                            │ REST
┌───────────────────────────▼─────────────────────────────────┐
│  FastAPI  /api/ai-impact/*                                  │
│   POST /runs          → ga4_channel_export + estimate       │
│   GET  /runs/{id}     → status + results JSON               │
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
                   ga4_weekly.csv, gsc_daily.csv,
                   trends_weekly.csv, estimate.json
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
| `ai_sessions`, `seo_sessions`, `ppc_sessions`, `other_traffic_sessions` | yes |
| `sessions`, `purchases` + channel `*_purchases` | yes |

`backend/ai_impact/panel.normalize_ga4_channel_weekly` maps this into estimate columns
(`other_sessions`, combined PPC → `branded_ppc_sessions`; Direct is 0 when not split).

### GSC daily → weekly (`gsc_daily.csv`)

| Column | Required |
| --- | --- |
| `date`, `clicks`, `impressions`, `ctr`, `position` | yes |

**Caveat:** Google changed impression measurement in **Sep 2025**. Do not compare
impressions across that break.

### Manual Google Trends (`trends_weekly.csv`)

`POST /api/ai-impact/trends-upload` validates the untouched CSV downloaded from the
Google Trends **Interest over time** chart. The upload must:

- use weekly rows and cover `2023-01-01` through approximately today;
- use United Kingdom as the location;
- contain one to five search-interest columns;
- retain the standard Google Trends `Week` header and CSV structure.

Validated columns are normalized to `week`, `trends_1` … `trends_5`. These series are
used as demand controls when estimating indirect AI associations.

---

## Estimation outputs (`estimate.json`)

Session/purchase **low · central · high** ranges plus CVR quality narrative
(`sessions_down_quality_up`, etc.). See `backend/ai_impact/estimate.py`.

Training weeks overlapping **Black Friday → Twelfth Night (5 Jan)** are masked
(`backend/ai_impact/holidays.py`).

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
web/src/components/
  AiImpactDashboard.tsx
docs/AI_TRAFFIC_IMPACT_DASHBOARD.md
```

The service image includes `research/ga4/` (see `.dockerignore` exception) so
`run_export` is available in-process and in the GA4 job image. Broader `research/`
stays excluded.

---

## Implementation phases

| Phase | Deliverable |
| --- | --- |
| **0 — Infrastructure** | Docs, estimate library, job scaffolds, API + GSC stubs, UI shell |
| **1 — Extracts** | Working GA4 via `ga4_channel_export`; GSC API |
| **2 — Estimate** | Wire GSC into panel; persist `estimate.json` |
| **3 — UI** | Connect flows, progress, results cards |
| **4 — Harden** | Retries, quotas, impression-break guards |

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

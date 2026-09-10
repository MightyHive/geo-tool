# Cloud Run Jobs

| Job directory | Purpose | Status |
| --- | --- | --- |
| `prompt_probe/` | AI prompt / AIO probes | Deployed (`geo-audit-prompt-probes` / staging) |
| `prompt_sentiment/` | Gemini qualitative sentiment (overall + by_category + by_prompt) | Deployed (`geo-audit-prompt-sentiment`) |
| `content_quality_gemini/` | Gemini E-E-A-T / answerability overlay on sampled pages | Deployed (`geo-audit-content-quality`) |
| `topic_content_generator/` | Gemini 3.5 Flash outlines for low-visibility topics | Deployed (`geo-audit-topic-content` / staging) |
| `audit_crawl/` | Brand + competitor site crawls | Deployed (`geo-audit-site-crawls`) |
| `scheduler_runner/` | Daily prompt rerun + monthly crawl fan-out | Deployed (`geo-audit-scheduler-runner-dev` / `-staging`) |
| `ga4_channel_export/` | Weekly GA4 channels | Wraps `research/ga4/ga4_channel_export.py` |
| `gsc_export/` | Search Console | Placeholder |
| `ai_signal_refresh/` | Optional 61-site complete-week AI portfolio signal | Available; not scheduled |
| `ai_impact_refit/` | Async site-inclusive NumPyro SEO/Direct refit | Deployed (`geo-audit-ai-impact-refit` / staging) — own NumPyro image via `scripts/deploy_ai_impact_refit_job.sh` |
| `ai_impact_baseline_refit/` | Full 61-site portfolio baseline retraining | Deployed as `geo-audit-ai-impact-baseline-refit`; run manually each month |
| *(external)* `geo-audit-google-trends-weekly` | Brand weekly Google Trends (Playwright) | Deployed separately from `cloud-run-google-trends-scraper` → `geo-tool-emea-ds`; API env `GOOGLE_TRENDS_JOB_*` |

See `docs/AI_TRAFFIC_IMPACT_DASHBOARD.md`.

## Automated refresh (scheduler)

Canonical path:

1. Cloud Scheduler (`geo-audit-daily-rerun-{dev,staging}`, `geo-audit-monthly-crawl-{dev,staging}`)
2. Pub/Sub topic `geo-audit-scheduler-topic`
3. Cloud Function `geo-audit-scheduler-launcher` (`functions/scheduler_runner`)
4. Environment-isolated Cloud Run Job `geo-audit-scheduler-runner-{dev,staging}`
5. Downstream prompt-probe / site-crawl Jobs for that environment

Setup / redeploy launcher + schedules:

```bash
bash scripts/setup_automated_refresh_scheduler.sh both pubsub
```

Optional exclusions (comma-separated audit folder IDs):

```bash
DEV_EXCLUDED_AUDITS=www.example.com_abc \
STAGING_EXCLUDED_AUDITS= \
  bash scripts/setup_automated_refresh_scheduler.sh both pubsub
```

Runner Jobs are deployed by `scripts/deploy_cloud_run_dev.sh` and
`scripts/deploy_cloud_run_staging.sh` from the shared application image
(`python -m jobs.scheduler_runner.run_job`). Do **not** deploy a separate
scheduler Dockerfile.

The portfolio baseline is refreshed manually each month. Refresh all 61 GA4
exports and the approved real Google Trends series, upload an immutable input
snapshot, then execute `geo-audit-ai-impact-baseline-refit`. Its input and output
URIs are configured through `AI_IMPACT_TRAINING_INPUT_URI` and
`AI_IMPACT_TRAINING_OUTPUT_URI`. It updates the model-root `CURRENT` pointer only
after all diagnostics and holdout gates pass. `ai_signal_refresh` remains
available as an optional signal-only operation but is not scheduled.

## AI Impact site-refit job

Site-inclusive NumPyro fits run as a **separate Cloud Run Job** with its own
image (JAX/NumPyro), not the web image:

```bash
./scripts/deploy_ai_impact_refit_job.sh dev      # geo-audit-ai-impact-refit
./scripts/deploy_ai_impact_refit_job.sh staging  # geo-audit-staging-ai-impact-refit
```

The API enqueues executions via `run_v2.JobsClient.run_job` when a run has ≥8
eligible completed non-Christmas weeks (GA4 + brand Trends + category).
Required API env (set by `deploy_cloud_run_{dev,staging}.sh`):

- `AI_IMPACT_REFIT_MODE=cloud_run`
- `AI_IMPACT_REFIT_JOB_NAME`
- `AI_IMPACT_REFIT_JOB_PROJECT` / `AI_IMPACT_REFIT_JOB_REGION`
- `AI_IMPACT_REFIT_GCS_ROOT=gs://<bucket>/ai_impact_runs`

Cold-start category scoring stays in-process on the API; only the heavy refit
is offloaded to this job.

After probe finalize, the API enqueues `geo-audit-prompt-sentiment` (DEV) /
`geo-audit-staging-prompt-sentiment` (staging) via `api/sentiment_jobs.py`.
The job writes `prompt_performance_sentiment.json` next to the audit (invalidated
when the live probe file mtime changes). UI reads persisted labels; it does not
recompute Gemini sentiment on every Prompts page load.

Local / no job configured: falls back to a background thread
(`PROMPT_SENTIMENT_FORCE_LOCAL=1` forces this).

## Content-quality Gemini job

After the primary crawl finishes, the audit runner enqueues
`geo-audit-content-quality` (DEV) / `geo-audit-staging-content-quality` (staging)
via `api/content_quality_jobs.py` for the **brand** site.

After a competitor crawl completes, the runner also enqueues **one CQ job per
competitor** under `competitors/<host>/` (same merge rules; does not block crawl).

The job:

1. Samples homepage + top N pages
   - Brand default **20** (`CONTENT_QUALITY_SAMPLE_CAP`, clamped 5–20)
   - Competitor default **same 20 per competitor**
     (`CONTENT_QUALITY_COMPETITOR_SAMPLE_CAP` optional override, clamped 5–20)
2. Re-fetches page text (falls back to crawl excerpts if fetch fails).
3. Asks Gemini to analyse each page **in the source language** and return structured
   E-E-A-T + answerability scores with English findings.
4. Writes `content_quality_gemini.json` (schema v1) in the site audit dir
   (brand root or `competitors/<host>/`).

**Merge rule:** when the cache is present, Gemini **replaces** crawl scores for
E-E-A-T, original information gain, and passage answerability. Crawl heuristics
remain for content formatting, schema/entity markup, and brand visibility.
Missing/invalid Gemini → heuristics-only (report still loads).

Competitor comparison reads each competitor's cache and overlays CQ scores /
verified findings the same way as brand.

Local / no job configured: background thread
(`CONTENT_QUALITY_FORCE_LOCAL=1` forces this).

## Topic content generator job

After all locale probes merge, the API computes response-weighted visibility by
topic and enqueues one outline for the lowest-visibility topic. Other topics are
generated on demand from the report workshop.

The job runs `python -m jobs.topic_content_generator.run_job`, uses
`GEMINI_TOPIC_CONTENT_MODEL=gemini-3.5-flash`, and stores one current atomic
sample per topic alongside versioned topic evidence. Editable section order and
writer instructions are passed through a request context file on the shared
audit mount. `TOPIC_CONTENT_FORCE_LOCAL=1` enables the development fallback.

### Redeploy notes

If you change job env overrides (`CONTENT_QUALITY_BRAND_NAME`,
`CONTENT_QUALITY_SITE_URL`, `CONTENT_QUALITY_SAMPLE_CAP_OVERRIDE`) or nested
`CONTENT_QUALITY_AUDIT_ID` paths for competitor dirs, redeploy the
`content_quality_gemini` Cloud Run Job image and ensure the API service that
enqueues jobs is rolled out together.
# Cloud Run Jobs

| Job directory | Purpose | Status |
| --- | --- | --- |
| `prompt_probe/` | AI prompt / AIO probes | Deployed (`geo-audit-prompt-probes`) |
| `prompt_sentiment/` | Gemini qualitative sentiment (overall + by_category + by_prompt) | Deployed (`geo-audit-prompt-sentiment`) |
| `content_quality_gemini/` | Gemini E-E-A-T / answerability overlay on sampled pages | Deployed (`geo-audit-content-quality`) |
| `audit_crawl/` | Brand + competitor site crawls | Deployed (`geo-audit-site-crawls`) |
| `ga4_channel_export/` | Weekly GA4 channels | Wraps `research/ga4/ga4_channel_export.py` |
| `gsc_export/` | Search Console | Placeholder |

See `docs/AI_TRAFFIC_IMPACT_DASHBOARD.md`.

## Prompt sentiment job

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

### Redeploy notes

If you change job env overrides (`CONTENT_QUALITY_BRAND_NAME`,
`CONTENT_QUALITY_SITE_URL`, `CONTENT_QUALITY_SAMPLE_CAP_OVERRIDE`) or nested
`CONTENT_QUALITY_AUDIT_ID` paths for competitor dirs, redeploy the
`content_quality_gemini` Cloud Run Job image and ensure the API service that
enqueues jobs is rolled out together.
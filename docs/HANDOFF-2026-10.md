# GEO tool: October 2026 changes and open items

Branch: `feat/citation-rate-personas-sitemap-upload` (not yet pushed). Everything
below is deployed to **dev** and **staging** (staging runs the same image as dev).

## Features

### Buyer personas
- **Wizard:** step 6 "Personas" (optional, after Competitors). Gemini suggests 5 purchaser personas; users can edit, add (max 8), remove or skip.
  - `web/src/components/WizardPersonasStep.tsx`
- **Prompt generation:** "Markets & prompts" generates 5 prompts per persona alongside topic prompts. Each persona prompt is mapped to its closest topic and stored in `prompt_personas` (prompt → persona) plus a `Persona: <name>` tag.
  - `WizardGeneratePromptsStep.tsx`, `web/src/lib/personas.ts`
- **Review prompts:** By topic / By persona toggle and a topic vs persona budget counter.
  - `WizardPromptsStep.tsx`, `web/src/lib/promptSelections.ts`
- **API:**
  - `POST /api/wizard/suggest-personas` and `POST /api/wizard/suggest-persona-prompts`, in `api/wizard.py`
  - Gemini helpers in `backend/geo_setup_llm.py`
- **Probing:** persona prompts have their own budget of 25, taken round-robin across personas, on top of 25 topic prompts. So up to 50 prompts are probed, which roughly doubles probe cost.
  - `api/prompt_selection.py`
- **Reporting:**
  - Personas tab (`PersonasVisibilitySection.tsx`, which shares `DimensionVisibilitySection.tsx` with Topics)
  - Persona filter on /prompts
  - Persona overview cards on AI Visibility Overview
- **Not built yet:** persona over-time history. `api/probe_history.py` only stores `topic_summaries`. It would need `persona_summaries` for persona trend charts.

### Citation Rate vs Visibility (replaced SOV vs Visibility scatterplots)
- **Definition:** citation rate = citations to the brand's own domain (including subdomains) ÷ all citations, counted per run.
- **Code:** `web/src/lib/visibilityMetrics.ts` and `api/prompt_performance_metrics.py` (`citation_rate_pct`).
- **Data capture change:** AIO and Claude captures now keep brand-domain citations. Older audits under-report citation rate until they are re-probed.

### Sitemap CSV upload (wizard Pages step)
- **Upload:** CSV with one URL per row; the `URL` header is optional. Invalid or off-domain rows are skipped and listed as warnings.
  - `web/src/lib/sitemapUrls.ts`
- **Server check:** the backend drops off-site URLs too (`filter_crawl_urls_for_site` in `api/geo_services.py`).

### Prompt-run cost estimator
- `docs/prompt-run-cost-estimator.xlsx`: input prompts, platforms, runs and frequency to get a monthly cost.
- **Prices:** checked 13 Oct 2026 from aggregators, not provider pages. Verify before quoting:
  - the OpenAI web-search fee
  - Gemini 3.6 Flash pricing
  - SerpApi pricing

## Speed / reliability fixes
1. **Infinite spinner (root cause):** the prompt-performance store mutated one object in place, so `useSyncExternalStore` never re-rendered. It now hands out immutable versioned snapshots, and stale responses are ignored.
   - `web/src/lib/promptPerformanceStore.ts`
2. **Timeouts:** API calls default to a 120s timeout and history fetches to 45s (`web/src/api/client.ts`, `probeHistoryFetch.ts`).
3. **Single bootstrap request:** `GET /prompt-performance?bootstrap=1` lets the server pick the primary locale (huge audits) or all locales.
   - Server side: `bootstrap_locale_key` in `api/prompt_performance.py`
4. **Metrics rebuilds off the request path:**
   - Stale metrics are served immediately and rebuilt in a background thread, with a 10-minute cooldown.
   - Missing metrics are built once per audit (single-flight).
   - Metrics JSON is now written compact.
   - All in `api/prompt_performance_metrics.py`.
5. **JSON cache:**
   - Revalidates by file mtime and size, so entries live 15 minutes instead of 60 seconds.
   - Concurrent misses for the same file share a single read.
   - Capped at 150 MB of files (`AUDIT_JSON_CACHE_MAX_MB`).
   - In `api/audit_json_cache.py`.
6. **Cloud Run settings:**
   - One warm instance (`CLOUD_RUN_MIN_INSTANCES`, default 1).
   - Concurrency 8 (`CLOUD_RUN_CONCURRENCY`, default 8).
   - If very large audits hit out-of-memory restarts, lower concurrency to 4–6.

## Deploying
- **Dev:** `./scripts/deploy_cloud_run_dev.sh`. This builds from the local working tree, uncommitted changes included.
- **Staging:** `./scripts/deploy_cloud_run_staging.sh` (builds from local), or promote dev's exact image without a rebuild:

  ```bash
  IMG=$(gcloud run services describe geo-audit-dev --region=europe-west1 --project=geo-tool-emea-ds --format='value(status.latestCreatedRevisionName)' | xargs -I{} gcloud run revisions describe {} --region=europe-west1 --project=geo-tool-emea-ds --format='value(status.imageDigest)')
  PROMOTE_IMAGE=$IMG ./scripts/deploy_cloud_run_staging.sh
  ```

  The deploy service account cannot move image tags, so promotion deploys by image digest.

## Open items
- **Push the branch and open the PR:**
  - `git push -u monks feat/citation-rate-personas-sitemap-upload`
  - `gh pr create --repo MightyHive/geo-tool --base main --fill`
- **Sign-in on dev:** returned `redirect_uri_mismatch`. Env config was unchanged; staging likely has the same issue. Add `https://<service-url>/api/auth/callback` (and the `/api/ga4/callback` and `/api/gsc/callback` equivalents) to the OAuth client's Authorized redirect URIs.
- **Persona checks:** the Gemini endpoints were only tested with stubs locally, because the local Gemini key is invalid. Verify a full persona audit on dev or staging.
- **Further speed work, not done:**
  - Generate the executive summary off the request path (or add a Gemini timeout).
  - Cache score-breakdown in the browser across section switches.
  - Cache the audit-detail response.
  - Cache the IAP config and set `GCP_PROJECT_NUMBER`.
  - Redis was deliberately skipped (cost).
- **Client data:** the Standard Chartered files in the repo root and `docs/` are intentionally **not committed** (client data). They exist only locally.

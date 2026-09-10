# Cloud Run staging (geo-tool-emea-ds)

## Architecture

One **Cloud Run service** (`geo-audit-staging`) is the most efficient fit for this app:

| Approach | Why |
|----------|-----|
| **Cloud Run service** (chosen) | Wizard uses **SSE** (`POST /api/audits/run`) with live logs; same pattern as local dev. |
| Cloud Run Job | Would need async job IDs + polling UI; extra moving parts for staging. |

The container includes:

- Built React UI (`web/dist`) served by FastAPI
- FastAPI `/api/*` (wizard, archive, reports)
- `backend/create-report.py` subprocess crawl (runs on Cloud Run, not your laptop)

Persistent data:

- GCS bucket `geo-tool-emea-ds-geo-audit-staging` mounted at `/var/geo-data`
- `GEO_DATA_ROOT=/var/geo-data` → `audit_output/` and `audit_archive/`

Service account: `geo-audit-tool@geo-tool-emea-ds.iam.gserviceaccount.com`

## Automated refresh scheduler

Daily prompt reruns and monthly site crawls use:

```
Cloud Scheduler → Pub/Sub (geo-audit-scheduler-topic)
  → Cloud Function (geo-audit-scheduler-launcher)
  → Cloud Run Job (geo-audit-scheduler-runner-staging)
  → geo-audit-staging-prompt-probes / geo-audit-staging-site-crawls
```

Dev mirrors this with `geo-audit-scheduler-runner-dev` and non-staging job names.

```bash
# After deploy_cloud_run_{dev,staging}.sh has created the runner Jobs:
bash scripts/setup_automated_refresh_scheduler.sh both pubsub
```

Payload fields: `action`, `environment`, `excluded_audits` (empty string by default).

## Deploy

```bash
chmod +x scripts/deploy_cloud_run_staging.sh
./scripts/deploy_cloud_run_staging.sh
```

Optional overrides:

```bash
GCP_PROJECT=geo-tool-emea-ds GCP_REGION=europe-west1 ./scripts/deploy_cloud_run_staging.sh
```

Copy `env/.env.staging.example` → `env/.env.staging` before deploy if you want OAuth/IAP/GA4 vars applied from that file.
For direct `*.run.app` sign-in, set `IAP_ENABLED=false` and optionally
`GOOGLE_OAUTH_DOMAIN`; deploy scripts mount `AUTH_CLIENT_ID`,
`AUTH_CLIENT_SECRET`, and `AUTH_COOKIE_SECRET` from Secret Manager. Register
`/api/auth/callback`, `/api/ga4/callback`, and `/api/gsc/callback` for the
service URL in the Google OAuth client.

## IAM (one-time)

```bash
PROJECT=geo-tool-emea-ds
BUCKET=${PROJECT}-geo-audit-staging
SA=geo-audit-tool@${PROJECT}.iam.gserviceaccount.com

gcloud storage buckets add-iam-policy-binding gs://${BUCKET} \
  --member="serviceAccount:${SA}" \
  --role="roles/storage.objectAdmin"

# Gemini / Vertex (if using Vertex instead of API key)
# gcloud projects add-iam-policy-binding ${PROJECT} \
#   --member="serviceAccount:${SA}" \
#   --role="roles/aiplatform.user"
```

## Image contents

`deploy/Dockerfile` + `.dockerignore` exclude:

- `.venv`, `audit_output/`, legacy Streamlit app, research sandbox, PyTorch stack
- `web/node_modules` (UI built in a Node stage)

## Local vs cloud

| | Local dev | Cloud Run staging |
|--|-----------|-------------------|
| UI | Vite :5173 | Same origin as API |
| API | :8000 | `https://…run.app` |
| Audit run | Subprocess on laptop | Subprocess in container |
| Data | `./audit_output` | GCS volume |

## Manual monthly AI baseline refit

The 61-property portfolio baseline is intentionally not scheduled. The remote
training job is `geo-audit-ai-impact-baseline-refit` in `europe-west1`; it is
separate from `geo-audit-ai-impact-refit`, which only performs per-site refits.

1. Refresh the 61 non-app GA4 session exports through the latest complete
   Sunday–Saturday week.
2. Refresh every approved Google Trends query with real weekly exports. Do not
   extrapolate missing Trends values.
3. Upload an immutable input archive and set the job's
   `AI_IMPACT_TRAINING_INPUT_URI`, `AI_IMPACT_TRAINING_OUTPUT_URI`, and
   `AI_IMPACT_MODEL_VERSION`.
4. Execute the job manually and inspect its run `status.json`, model manifest,
   diagnostics, six holdout checks, file hashes, and `CURRENT` before consuming
   the artifact.

The current promoted baseline is `hierarchical-ai-v2-2026-09-05`. Its model
panel ends 26 July 2026 where available Trends controls end; its corrected
portfolio signal extends through the week starting 30 August 2026.

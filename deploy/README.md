# Cloud Run staging (emea-ds-sandbox)

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

- GCS bucket `emea-ds-sandbox-geo-audit-staging` mounted at `/var/geo-data`
- `GEO_DATA_ROOT=/var/geo-data` → `audit_output/` and `audit_archive/`

Service account: `geo-audit-tool@emea-ds-sandbox.iam.gserviceaccount.com`

## Deploy

```bash
chmod +x scripts/deploy_cloud_run_staging.sh
./scripts/deploy_cloud_run_staging.sh
```

Optional overrides:

```bash
GCP_PROJECT=emea-ds-sandbox GCP_REGION=europe-west1 ./scripts/deploy_cloud_run_staging.sh
```

Copy `env/.env.staging.example` → `env/.env.staging` before deploy if you want OAuth/IAP/GA4 vars applied from that file.

## IAM (one-time)

```bash
PROJECT=emea-ds-sandbox
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

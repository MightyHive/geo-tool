#!/usr/bin/env bash
# Build and deploy GEO audit web + API + audit runner to Cloud Run (staging).
# Project: emea-ds-sandbox | Region: europe-west1 | SA: geo-audit-tool@...
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROJECT="${GCP_PROJECT:-emea-ds-sandbox}"
REGION="${GCP_REGION:-europe-west1}"
SERVICE="${CLOUD_RUN_SERVICE:-geo-audit-staging}"
SA_EMAIL="${CLOUD_RUN_SA:-geo-audit-tool@${PROJECT}.iam.gserviceaccount.com}"
AR_REPO="${ARTIFACT_REGISTRY_REPO:-geo-audit}"
IMAGE_NAME="${IMAGE_NAME:-web}"
IMAGE_TAG="${IMAGE_TAG:-staging}"
BUCKET="${GCS_BUCKET:-${PROJECT}-geo-audit-staging}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${IMAGE_NAME}:${IMAGE_TAG}"

echo "==> Project: ${PROJECT}  Region: ${REGION}  Service: ${SERVICE}"
echo "==> Image:   ${IMAGE}"
echo "==> Bucket:  gs://${BUCKET}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI is required." >&2
  exit 1
fi

gcloud config set project "${PROJECT}" >/dev/null

echo "==> Enabling APIs (idempotent)…"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  --project="${PROJECT}"

if ! gcloud artifacts repositories describe "${AR_REPO}" \
  --location="${REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "==> Creating Artifact Registry repo ${AR_REPO}…"
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="GEO audit tool images"
fi

if ! gcloud storage buckets describe "gs://${BUCKET}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "==> Creating GCS bucket gs://${BUCKET}…"
  gcloud storage buckets create "gs://${BUCKET}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --uniform-bucket-level-access
fi

echo "==> Granting bucket access to ${SA_EMAIL}…"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --project="${PROJECT}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectAdmin" \
  --quiet >/dev/null 2>&1 || true

echo "==> Building and pushing image (Cloud Build)…"
gcloud builds submit \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --config=deploy/cloudbuild.yaml \
  --substitutions="_IMAGE=${IMAGE}" \
  .

ENV_VARS="APP_ENV=staging,GEO_DATA_ROOT=/var/geo-data,CLOUD_RUN_REGION=${REGION}"
SECRETS_FILE="${ROOT}/env/.env.staging"
if [[ -f "${SECRETS_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "${SECRETS_FILE}"
  set +a
fi

echo "==> Deploying Cloud Run service (audit runs in-container, not locally)…"
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${SA_EMAIL}" \
  --execution-environment=gen2 \
  --cpu=2 \
  --memory=4Gi \
  --timeout=3600 \
  --concurrency=2 \
  --min-instances=0 \
  --max-instances=5 \
  --cpu-boost \
  --no-cpu-throttling \
  --port=8080 \
  --allow-unauthenticated \
  --set-env-vars="${ENV_VARS}" \
  --add-volume=name=geo-data,type=cloud-storage,bucket="${BUCKET}" \
  --add-volume-mount=volume=geo-data,mount-path=/var/geo-data

SERVICE_URL="$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --format='value(status.url)')"

echo "==> Service URL: ${SERVICE_URL}"

# Point OAuth / IAP env at the live URL (same host serves UI + /api).
gcloud run services update "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --update-env-vars="WEB_PUBLIC_ORIGIN=${SERVICE_URL},STREAMLIT_PUBLIC_ORIGIN=${SERVICE_URL},AUTH_REDIRECT_URI=${SERVICE_URL}/api/auth/callback"

echo ""
echo "Deployed ${SERVICE} → ${SERVICE_URL}"
echo "Audits write to gs://${BUCKET} (mounted at /var/geo-data)."
echo ""
echo "Next steps:"
echo "  1. Grant ${SA_EMAIL} roles/storage.objectAdmin on gs://${BUCKET} (if not already)."
echo "  2. Set Secret Manager or env vars: GEMINI_API_KEY, OPENAI_API_KEY, OAuth [auth], IAP_* (see env/.env.staging.example)."
echo "  3. Restrict access: remove --allow-unauthenticated and use IAP / IAM invoker, or keep public for smoke tests only."

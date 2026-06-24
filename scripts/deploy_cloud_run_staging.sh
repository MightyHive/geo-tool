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

SA_KEY="${GOOGLE_APPLICATION_CREDENTIALS:-${ROOT}/local-auth/sa-key.json}"
if [[ -f "${SA_KEY}" && -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}" ]]; then
  echo "==> Generating access token from SA key (bypassing system SSL proxy)…"
  _TOKEN=$(python3 - <<PYEOF
from google.oauth2 import service_account
import google.auth.transport.requests
creds = service_account.Credentials.from_service_account_file(
    "${SA_KEY}",
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
creds.refresh(google.auth.transport.requests.Request())
print(creds.token)
PYEOF
  )
  export CLOUDSDK_AUTH_ACCESS_TOKEN="${_TOKEN}"
  export GOOGLE_APPLICATION_CREDENTIALS="${SA_KEY}"
  echo "==> Token injected (expires ~1h). Running deploy now…"
fi

gcloud config set project "${PROJECT}" >/dev/null

echo "==> Checking required APIs…"
if gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  --project="${PROJECT}" >/dev/null 2>&1; then
  echo "    APIs confirmed enabled."
else
  echo "    Note: could not run gcloud services enable — continuing if APIs already exist."
fi

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
BUILD_ID="$(gcloud builds submit \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --config=deploy/cloudbuild.yaml \
  --substitutions="_IMAGE=${IMAGE}" \
  --async \
  --format='value(id)' \
  .)"
echo "==> Cloud Build started: ${BUILD_ID}"
echo "    Logs: https://console.cloud.google.com/cloud-build/builds;region=${REGION}/${BUILD_ID}?project=${PROJECT}"

while true; do
  BUILD_STATUS="$(gcloud builds describe "${BUILD_ID}" \
    --project="${PROJECT}" \
    --region="${REGION}" \
    --format='value(status)')"
  case "${BUILD_STATUS}" in
    SUCCESS)
      echo "==> Cloud Build finished successfully."
      break
      ;;
    FAILURE|CANCELLED|EXPIRED|INTERNAL_ERROR|TIMEOUT)
      echo "==> Cloud Build failed with status: ${BUILD_STATUS}" >&2
      exit 1
      ;;
    *)
      echo "    Build status: ${BUILD_STATUS}…"
      sleep 10
      ;;
  esac
done

ENV_VARS="APP_ENV=staging,GEO_DATA_ROOT=/var/geo-data,CLOUD_RUN_REGION=${REGION}"
SECRETS_FILE="${ROOT}/env/.env.staging"
if [[ -f "${SECRETS_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "${SECRETS_FILE}"
  set +a
fi

SET_SECRETS=""
_add_secret() {
  local env_name="$1" secret_name="$2"
  if gcloud secrets describe "${secret_name}" --project="${PROJECT}" >/dev/null 2>&1; then
    SET_SECRETS="${SET_SECRETS}${env_name}=${secret_name}:latest,"
    echo "==> Will mount Secret Manager secret: ${secret_name} → ${env_name}"
  fi
}
# Same Secret Manager names as geo-audit-dev
_add_secret GA4_OAUTH_CLIENT_ID google-oauth-client-id-geo-tool
_add_secret GA4_OAUTH_CLIENT_SECRET google-oauth-client-secret-geo-tool
_add_secret AUTH_COOKIE_SECRET auth-cookie-secret-geo-tool
_add_secret GEMINI_API_KEY gemini-api-key-geo-tool
_add_secret OPENAI_API_KEY openai-api-key-geo-tool
_add_secret ANTHROPIC_API_KEY anthropic-api-key-geo-tool
SET_SECRETS="${SET_SECRETS%,}"

echo "==> Deploying Cloud Run service (audit runs in-container, not locally)…"
DEPLOY_CMD=(
  gcloud run deploy "${SERVICE}"
  --project="${PROJECT}"
  --region="${REGION}"
  --image="${IMAGE}"
  --service-account="${SA_EMAIL}"
  --execution-environment=gen2
  --cpu=2
  --memory=4Gi
  --timeout=3600
  --concurrency=2
  --min-instances=0
  --max-instances=5
  --cpu-boost
  --no-cpu-throttling
  --port=8080
  --allow-unauthenticated
  --set-env-vars="${ENV_VARS}"
  --add-volume=name=geo-data,type=cloud-storage,bucket="${BUCKET}"
  --add-volume-mount=volume=geo-data,mount-path=/var/geo-data
)
if [[ -n "${SET_SECRETS}" ]]; then
  DEPLOY_CMD+=(--set-secrets="${SET_SECRETS}")
fi
"${DEPLOY_CMD[@]}"

SERVICE_URL="$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --format='value(status.url)')"

echo "==> Service URL: ${SERVICE_URL}"

gcloud run services update "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --update-env-vars="WEB_PUBLIC_ORIGIN=${SERVICE_URL},DEPLOY_PUBLIC_ORIGIN=${SERVICE_URL},AUTH_REDIRECT_URI=${SERVICE_URL}/api/auth/callback,GA4_OAUTH_REDIRECT_URI=${SERVICE_URL}/api/ga4/callback"

echo ""
echo "Deployed ${SERVICE} → ${SERVICE_URL}"
echo "Audits write to gs://${BUCKET} (mounted at /var/geo-data)."

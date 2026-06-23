#!/usr/bin/env bash
# Build and deploy GEO audit web + API + audit runner to Cloud Run (dev).
# Project: emea-ds-sandbox | Region: europe-west1 | SA: geo-audit-tool@...
# Mirrors geo-audit-staging config; adds ANTHROPIC_API_KEY for Claude probes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROJECT="${GCP_PROJECT:-emea-ds-sandbox}"
REGION="${GCP_REGION:-europe-west1}"
SERVICE="${CLOUD_RUN_SERVICE:-geo-audit-dev}"
SA_EMAIL="${CLOUD_RUN_SA:-geo-audit-tool@${PROJECT}.iam.gserviceaccount.com}"
AR_REPO="${ARTIFACT_REGISTRY_REPO:-geo-audit}"
IMAGE_NAME="${IMAGE_NAME:-web}"
IMAGE_TAG="${IMAGE_TAG:-dev}"
BUCKET="${GCS_BUCKET:-${PROJECT}-geo-audit-dev}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${IMAGE_NAME}:${IMAGE_TAG}"

echo "==> Project: ${PROJECT}  Region: ${REGION}  Service: ${SERVICE}"
echo "==> Image:   ${IMAGE}"
echo "==> Bucket:  gs://${BUCKET}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI is required." >&2
  exit 1
fi

# On corporate networks the gcloud auth stack can be blocked by SSL proxy.
# If GOOGLE_APPLICATION_CREDENTIALS is set, generate a token via google-auth
# (which uses requests/certifi and bypasses the system SSL proxy) and inject
# it via CLOUDSDK_AUTH_ACCESS_TOKEN so gcloud skips its own token refresh.
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

echo "==> Checking required APIs (skipped when deploy SA cannot enable services)…"
if gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  --project="${PROJECT}" >/dev/null 2>&1; then
  echo "    APIs confirmed enabled."
else
  echo "    Note: could not run gcloud services enable with the current credentials."
  echo "    This is normal when deploying via the geo-audit-tool service account key."
  echo "    Continuing — required APIs are already enabled in ${PROJECT}."
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
      echo "    See logs: https://console.cloud.google.com/cloud-build/builds;region=${REGION}/${BUILD_ID}?project=${PROJECT}" >&2
      exit 1
      ;;
    *)
      echo "    Build status: ${BUILD_STATUS}…"
      sleep 5
      ;;
  esac
done

ENV_VARS="APP_ENV=dev,GEO_DATA_ROOT=/var/geo-data,CLOUD_RUN_REGION=${REGION}"

# Load dev env overrides if present (API keys, IAP config, etc.)
SECRETS_FILE="${ROOT}/env/.env.dev"
if [[ -f "${SECRETS_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "${SECRETS_FILE}"
  set +a
fi

# Resolve which secrets to mount from Secret Manager.
# ANTHROPIC_API_KEY is expected to be stored as a Secret Manager secret named ANTHROPIC_API_KEY.
# GEMINI_API_KEY and OPENAI_API_KEY are similarly expected.
SET_SECRETS=""
for SECRET_NAME in ANTHROPIC_API_KEY GEMINI_API_KEY OPENAI_API_KEY GOOGLE_API_KEY; do
  if gcloud secrets describe "${SECRET_NAME}" --project="${PROJECT}" >/dev/null 2>&1; then
    SET_SECRETS="${SET_SECRETS}${SECRET_NAME}=${SECRET_NAME}:latest,"
    echo "==> Will mount Secret Manager secret: ${SECRET_NAME}"
  else
    echo "==> Skipping Secret Manager secret (not found): ${SECRET_NAME}"
  fi
done
SET_SECRETS="${SET_SECRETS%,}"  # trim trailing comma

echo "==> Deploying Cloud Run service ${SERVICE}…"
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

# Point OAuth / IAP env at the live URL (same host serves UI + /api).
gcloud run services update "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --update-env-vars="WEB_PUBLIC_ORIGIN=${SERVICE_URL},DEPLOY_PUBLIC_ORIGIN=${SERVICE_URL},AUTH_REDIRECT_URI=${SERVICE_URL}/api/auth/callback"

echo ""
echo "Deployed ${SERVICE} → ${SERVICE_URL}"
echo "Audits write to gs://${BUCKET} (mounted at /var/geo-data)."
echo ""
echo "Next steps:"
echo "  1. Verify ANTHROPIC_API_KEY is in Secret Manager (project ${PROJECT})."
echo "     gcloud secrets list --project=${PROJECT} --filter=name:ANTHROPIC_API_KEY"
echo "  2. If missing, create it:"
echo "     echo -n 'sk-ant-...' | gcloud secrets create ANTHROPIC_API_KEY --data-file=- --project=${PROJECT}"
echo "     gcloud secrets add-iam-policy-binding ANTHROPIC_API_KEY \\"
echo "       --member=serviceAccount:${SA_EMAIL} --role=roles/secretmanager.secretAccessor \\"
echo "       --project=${PROJECT}"
echo "  3. Grant ${SA_EMAIL} roles/storage.objectAdmin on gs://${BUCKET} (if not already)."
echo "  4. For GEMINI_API_KEY / OPENAI_API_KEY: ensure they exist in Secret Manager or update env vars:"
echo "     gcloud run services update ${SERVICE} --region=${REGION} --project=${PROJECT} \\"
echo "       --update-env-vars=GEMINI_API_KEY=<key>,OPENAI_API_KEY=<key>,ANTHROPIC_API_KEY=<key>"

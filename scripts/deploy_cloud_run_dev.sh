#!/usr/bin/env bash
# Build and deploy GEO audit web + API + audit runner to Cloud Run (dev).
# Project: geo-tool-emea-ds | Region: europe-west1 | SA: geo-audit-tool@...
# Mirrors geo-audit-staging config; adds ANTHROPIC_API_KEY for Claude probes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROJECT="${GCP_PROJECT:-geo-tool-emea-ds}"
REGION="${GCP_REGION:-europe-west1}"
SERVICE="${CLOUD_RUN_SERVICE:-geo-audit-dev}"
PROMPT_JOB="${PROMPT_PROBE_JOB_NAME:-geo-audit-prompt-probes}"
CRAWL_JOB="${AUDIT_CRAWL_JOB_NAME:-geo-audit-site-crawls}"
PDF_JOB="${PDF_EXPORT_JOB_NAME:-geo-audit-pdf-exports}"
SENTIMENT_JOB="${PROMPT_SENTIMENT_JOB_NAME:-geo-audit-prompt-sentiment}"
CONTENT_QUALITY_JOB="${CONTENT_QUALITY_JOB_NAME:-geo-audit-content-quality}"
TOPIC_CONTENT_JOB="${TOPIC_CONTENT_JOB_NAME:-geo-audit-topic-content}"
SCHEDULER_JOB="${SCHEDULER_JOB_NAME:-geo-audit-scheduler-runner-dev}"
TRENDS_JOB="${GOOGLE_TRENDS_JOB_NAME:-geo-audit-google-trends-weekly}"
AI_IMPACT_REFIT_JOB="${AI_IMPACT_REFIT_JOB_NAME:-geo-audit-ai-impact-refit}"
SA_EMAIL="${CLOUD_RUN_SA:-geo-audit-tool@${PROJECT}.iam.gserviceaccount.com}"
AR_REPO="${ARTIFACT_REGISTRY_REPO:-geo-audit}"
IMAGE_NAME="${IMAGE_NAME:-web}"
IMAGE_TAG="${IMAGE_TAG:-dev}"
BUCKET="${GCS_BUCKET:-${PROJECT}-geo-audit-dev}"
MODEL_BUCKET="${AI_IMPACT_MODEL_BUCKET:-${PROJECT}-ai-impact-models}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${IMAGE_NAME}:${IMAGE_TAG}"
BUILD_SOURCE="${CLOUD_BUILD_SOURCE:-.}"

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
  "${BUILD_SOURCE}")"
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

ENV_VARS="APP_ENV=dev,GEO_DATA_ROOT=/var/geo-data,GCS_BUCKET=${BUCKET},CLOUD_RUN_REGION=${REGION},PROMPT_PROBE_JOB_NAME=${PROMPT_JOB},PROMPT_PROBE_JOB_REGION=${REGION},PROMPT_PROBE_JOB_PROJECT=${PROJECT},AUDIT_CRAWL_JOB_NAME=${CRAWL_JOB},AUDIT_CRAWL_JOB_REGION=${REGION},AUDIT_CRAWL_JOB_PROJECT=${PROJECT},PDF_EXPORT_JOB_NAME=${PDF_JOB},PDF_EXPORT_JOB_REGION=${REGION},PDF_EXPORT_JOB_PROJECT=${PROJECT},PROMPT_SENTIMENT_JOB_NAME=${SENTIMENT_JOB},PROMPT_SENTIMENT_JOB_REGION=${REGION},PROMPT_SENTIMENT_JOB_PROJECT=${PROJECT},CONTENT_QUALITY_JOB_NAME=${CONTENT_QUALITY_JOB},CONTENT_QUALITY_JOB_REGION=${REGION},CONTENT_QUALITY_JOB_PROJECT=${PROJECT},TOPIC_CONTENT_JOB_NAME=${TOPIC_CONTENT_JOB},TOPIC_CONTENT_JOB_REGION=${REGION},TOPIC_CONTENT_JOB_PROJECT=${PROJECT},GEMINI_TOPIC_CONTENT_MODEL=gemini-3.5-flash,SCHEDULER_JOB_NAME=${SCHEDULER_JOB},SCHEDULER_JOB_REGION=${REGION},SCHEDULER_JOB_PROJECT=${PROJECT},GOOGLE_TRENDS_JOB_NAME=${TRENDS_JOB},GOOGLE_TRENDS_JOB_REGION=${REGION},GOOGLE_TRENDS_JOB_PROJECT=${PROJECT},GOOGLE_TRENDS_GCS_BUCKET=${BUCKET},AI_IMPACT_MODEL_ARTIFACT_ROOT=gs://${MODEL_BUCKET}/models/dev,AI_IMPACT_REFIT_MODE=cloud_run,AI_IMPACT_REFIT_JOB_NAME=${AI_IMPACT_REFIT_JOB},AI_IMPACT_REFIT_JOB_PROJECT=${PROJECT},AI_IMPACT_REFIT_JOB_REGION=${REGION},AI_IMPACT_REFIT_GCS_ROOT=gs://${BUCKET}/ai_impact_runs"

# Load dev env overrides if present (API keys, IAP config, etc.)
SECRETS_FILE="${ROOT}/env/.env.development"
if [[ -f "${ROOT}/env/.env.dev" ]]; then
  SECRETS_FILE="${ROOT}/env/.env.dev"
fi
if [[ -f "${SECRETS_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "${SECRETS_FILE}"
  set +a
fi

# Pass non-secret app authentication settings from the dev env file. OAuth client
# credentials and the cookie key are mounted from Secret Manager below.
_append_env_if_set() {
  local name="$1" value="${!1:-}"
  if [[ -n "${value}" ]]; then
    ENV_VARS="${ENV_VARS},${name}=${value}"
  fi
}
for _name in AUTH_SERVER_METADATA_URL GOOGLE_OAUTH_DOMAIN IAP_HOSTED_DOMAIN IAP_ENABLED IAP_ENFORCE IAP_AUDIENCE IAP_OAUTH_CLIENT_ID; do
  _append_env_if_set "${_name}"
done

# Resolve secrets to mount. Prefer hard-coded geo-tool secret names (deploy SA
# often cannot ``secrets describe``); fall back to probing when names may vary.
SET_SECRETS=""
_add_secret() {
  local env_name="$1" secret_name="$2" require="${3:-0}"
  if [[ "${require}" == "1" ]]; then
    SET_SECRETS="${SET_SECRETS}${env_name}=${secret_name}:latest,"
    echo "==> Will mount Secret Manager secret: ${secret_name} → ${env_name}"
    return
  fi
  if gcloud secrets describe "${secret_name}" --project="${PROJECT}" >/dev/null 2>&1; then
    SET_SECRETS="${SET_SECRETS}${env_name}=${secret_name}:latest,"
    echo "==> Will mount Secret Manager secret: ${secret_name} → ${env_name}"
  else
    echo "==> Skipping Secret Manager secret (not found / no access): ${secret_name}"
  fi
}
# Always remount known geo-audit-dev secrets (do not rely on secrets.describe).
_add_secret GA4_OAUTH_CLIENT_ID google-oauth-client-id-geo-tool 1
_add_secret GA4_OAUTH_CLIENT_SECRET google-oauth-client-secret-geo-tool 1
_add_secret AUTH_CLIENT_ID google-oauth-client-id-geo-tool 1
_add_secret AUTH_CLIENT_SECRET google-oauth-client-secret-geo-tool 1
_add_secret AUTH_COOKIE_SECRET auth-cookie-secret-geo-tool 1
_add_secret GEMINI_API_KEY gemini-api-key-geo-tool 1
_add_secret OPENAI_API_KEY openai-api-key-geo-tool 1
_add_secret ANTHROPIC_API_KEY anthropic-api-key-geo-tool 1
_add_secret YOUTUBE_API_KEY youtube-api-key-geo-tool 1
_add_secret REDDIT_CLIENT_ID REDDIT_CLIENT_ID 0
_add_secret REDDIT_CLIENT_SECRET REDDIT_CLIENT_SECRET 0
_add_secret SENDGRID_API_KEY sendgrid-api-key-geo-tool 0
SET_SECRETS="${SET_SECRETS%,}"  # trim trailing comma

# Prompt probes fan out one Job execution per configured locale (see api/prompt_jobs.py).
# The 12h task-timeout is a safety net for a single locale's platforms — multi-market
# audits no longer run all locales sequentially in one execution.
echo "==> Deploying Cloud Run prompt probe Job ${PROMPT_JOB}…"
gcloud run jobs deploy "${PROMPT_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${SA_EMAIL}" \
  --cpu=2 \
  --memory=4Gi \
  --tasks=1 \
  --parallelism=1 \
  --max-retries=0 \
  --task-timeout=43200s \
  --command=python \
  --args=-m,jobs.prompt_probe.run_job \
  --set-env-vars="${ENV_VARS}" \
  --set-secrets="${SET_SECRETS}" \
  --add-volume=name=geo-data,type=cloud-storage,bucket="${BUCKET}" \
  --add-volume-mount=volume=geo-data,mount-path=/var/geo-data

gcloud run jobs add-iam-policy-binding "${PROMPT_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" \
  --quiet >/dev/null
gcloud run jobs add-iam-policy-binding "${PROMPT_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.jobsExecutorWithOverrides" \
  --quiet >/dev/null

echo "==> Deploying Cloud Run site crawl Job ${CRAWL_JOB}…"
gcloud run jobs deploy "${CRAWL_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${SA_EMAIL}" \
  --cpu=2 \
  --memory=4Gi \
  --tasks=1 \
  --parallelism=1 \
  --max-retries=0 \
  --task-timeout=7200s \
  --command=python \
  --args=-m,jobs.audit_crawl.run_job \
  --set-env-vars="${ENV_VARS}" \
  --set-secrets="${SET_SECRETS}" \
  --add-volume=name=geo-data,type=cloud-storage,bucket="${BUCKET}" \
  --add-volume-mount=volume=geo-data,mount-path=/var/geo-data

gcloud run jobs add-iam-policy-binding "${CRAWL_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" \
  --quiet >/dev/null
gcloud run jobs add-iam-policy-binding "${CRAWL_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.jobsExecutorWithOverrides" \
  --quiet >/dev/null

echo "==> Deploying Cloud Run PDF export Job ${PDF_JOB}…"
gcloud run jobs deploy "${PDF_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${SA_EMAIL}" \
  --cpu=2 \
  --memory=4Gi \
  --tasks=1 \
  --parallelism=1 \
  --max-retries=0 \
  --task-timeout=3600s \
  --command=python \
  --args=-m,jobs.pdf_export.run_job \
  --set-env-vars="${ENV_VARS}" \
  --set-secrets="${SET_SECRETS}" \
  --add-volume=name=geo-data,type=cloud-storage,bucket="${BUCKET}" \
  --add-volume-mount=volume=geo-data,mount-path=/var/geo-data

gcloud run jobs add-iam-policy-binding "${PDF_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" \
  --quiet >/dev/null
gcloud run jobs add-iam-policy-binding "${PDF_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.jobsExecutorWithOverrides" \
  --quiet >/dev/null

echo "==> Deploying Cloud Run prompt sentiment Job ${SENTIMENT_JOB}…"
gcloud run jobs deploy "${SENTIMENT_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${SA_EMAIL}" \
  --cpu=1 \
  --memory=2Gi \
  --tasks=1 \
  --parallelism=1 \
  --max-retries=0 \
  --task-timeout=1800s \
  --command=python \
  --args=-m,jobs.prompt_sentiment.run_job \
  --set-env-vars="${ENV_VARS}" \
  --set-secrets="${SET_SECRETS}" \
  --add-volume=name=geo-data,type=cloud-storage,bucket="${BUCKET}" \
  --add-volume-mount=volume=geo-data,mount-path=/var/geo-data

gcloud run jobs add-iam-policy-binding "${SENTIMENT_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" \
  --quiet >/dev/null
gcloud run jobs add-iam-policy-binding "${SENTIMENT_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.jobsExecutorWithOverrides" \
  --quiet >/dev/null

echo "==> Deploying Cloud Run content-quality Job ${CONTENT_QUALITY_JOB}…"
gcloud run jobs deploy "${CONTENT_QUALITY_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${SA_EMAIL}" \
  --cpu=1 \
  --memory=2Gi \
  --tasks=1 \
  --parallelism=1 \
  --max-retries=0 \
  --task-timeout=1800s \
  --command=python \
  --args=-m,jobs.content_quality_gemini.run_job \
  --set-env-vars="${ENV_VARS}" \
  --set-secrets="${SET_SECRETS}" \
  --add-volume=name=geo-data,type=cloud-storage,bucket="${BUCKET}" \
  --add-volume-mount=volume=geo-data,mount-path=/var/geo-data

gcloud run jobs add-iam-policy-binding "${CONTENT_QUALITY_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" \
  --quiet >/dev/null
gcloud run jobs add-iam-policy-binding "${CONTENT_QUALITY_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.jobsExecutorWithOverrides" \
  --quiet >/dev/null

echo "==> Deploying Cloud Run topic-content Job ${TOPIC_CONTENT_JOB}…"
gcloud run jobs deploy "${TOPIC_CONTENT_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${SA_EMAIL}" \
  --cpu=1 \
  --memory=2Gi \
  --tasks=1 \
  --parallelism=1 \
  --max-retries=1 \
  --task-timeout=1200s \
  --command=python \
  --args=-m,jobs.topic_content_generator.run_job \
  --set-env-vars="${ENV_VARS}" \
  --set-secrets="${SET_SECRETS}" \
  --add-volume=name=geo-data,type=cloud-storage,bucket="${BUCKET}" \
  --add-volume-mount=volume=geo-data,mount-path=/var/geo-data

gcloud run jobs add-iam-policy-binding "${TOPIC_CONTENT_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" \
  --quiet >/dev/null
gcloud run jobs add-iam-policy-binding "${TOPIC_CONTENT_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.jobsExecutorWithOverrides" \
  --quiet >/dev/null

echo "==> Deploying Cloud Run scheduler runner Job ${SCHEDULER_JOB}…"
gcloud run jobs deploy "${SCHEDULER_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${SA_EMAIL}" \
  --cpu=1 \
  --memory=2Gi \
  --tasks=1 \
  --parallelism=1 \
  --max-retries=0 \
  --task-timeout=3600s \
  --command=python \
  --args=-m,jobs.scheduler_runner.run_job \
  --set-env-vars="${ENV_VARS}" \
  --set-secrets="${SET_SECRETS}" \
  --add-volume=name=geo-data,type=cloud-storage,bucket="${BUCKET}" \
  --add-volume-mount=volume=geo-data,mount-path=/var/geo-data

gcloud run jobs add-iam-policy-binding "${SCHEDULER_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" \
  --quiet >/dev/null
gcloud run jobs add-iam-policy-binding "${SCHEDULER_JOB}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.jobsExecutorWithOverrides" \
  --quiet >/dev/null

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
  --iap
  --set-env-vars="${ENV_VARS}"
  --set-secrets="${SET_SECRETS}"
  --add-volume=name=geo-data,type=cloud-storage,bucket="${BUCKET}"
  --add-volume-mount=volume=geo-data,mount-path=/var/geo-data
)

"${DEPLOY_CMD[@]}"

SERVICE_URL="$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --format='value(status.url)')"

echo "==> Service URL: ${SERVICE_URL}"

# Point OAuth / IAP env at the live URL (same host serves UI + /api).
# Also set WEB_PUBLIC_ORIGIN on jobs so completion emails link to this service.
gcloud run services update "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --update-env-vars="WEB_PUBLIC_ORIGIN=${SERVICE_URL},DEPLOY_PUBLIC_ORIGIN=${SERVICE_URL},AUTH_REDIRECT_URI=${SERVICE_URL}/api/auth/callback,GA4_OAUTH_REDIRECT_URI=${SERVICE_URL}/api/ga4/callback,GSC_OAUTH_REDIRECT_URI=${SERVICE_URL}/api/gsc/callback"

for _job in "${PROMPT_JOB}" "${CRAWL_JOB}" "${SENTIMENT_JOB}" "${CONTENT_QUALITY_JOB}" "${TOPIC_CONTENT_JOB}" "${SCHEDULER_JOB}"; do
  gcloud run jobs update "${_job}" \
    --project="${PROJECT}" \
    --region="${REGION}" \
    --update-env-vars="WEB_PUBLIC_ORIGIN=${SERVICE_URL}" \
    --quiet || true
done

echo ""
echo "Deployed ${SERVICE} → ${SERVICE_URL}"
echo "Prompt probes → Cloud Run Job ${PROMPT_JOB} (${REGION})."
echo "Site crawls → Cloud Run Job ${CRAWL_JOB} (${REGION})."
echo "PDF exports → Cloud Run Job ${PDF_JOB} (${REGION})."
echo "Prompt sentiment → Cloud Run Job ${SENTIMENT_JOB} (${REGION})."
echo "Content quality → Cloud Run Job ${CONTENT_QUALITY_JOB} (${REGION})."
echo "Scheduler runner → Cloud Run Job ${SCHEDULER_JOB} (${REGION})."
echo "Google Trends weekly → Cloud Run Job ${TRENDS_JOB} (${REGION}) — deploy separately via"
echo "  cloud-run-google-trends-scraper/deploy_cloud_run_job_weekly_sandbox.sh"
echo "AI Impact site refit → Cloud Run Job ${AI_IMPACT_REFIT_JOB} (${REGION}) — deploy separately via"
echo "  scripts/deploy_ai_impact_refit_job.sh dev"
echo "Audits write to gs://${BUCKET} (mounted at /var/geo-data)."
echo ""
echo "Next steps:"
echo "  1. Verify ANTHROPIC_API_KEY / Gemini / OpenAI secrets mount (project ${PROJECT})."
echo "  2. Open ${SERVICE_URL} → report → AI Impact Estimates."
echo "  3. GA4 OAuth redirect URI should include ${SERVICE_URL}/api/ga4/callback"
echo "  4. Ensure ${AI_IMPACT_REFIT_JOB} is deployed (NumPyro image) before site refits queue."
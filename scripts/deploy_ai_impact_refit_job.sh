#!/usr/bin/env bash
# Build + deploy the hierarchical AI-impact site-refit Cloud Run Job
# (own NumPyro/JAX image) to geo-tool-emea-ds.
#
# Usage (from repo root, gcloud authenticated to geo-tool-emea-ds):
#   ./scripts/deploy_ai_impact_refit_job.sh
#   ./scripts/deploy_ai_impact_refit_job.sh staging
#
# The geo-audit API enqueues this job via run_v2.JobsClient.run_job when a run
# has ≥8 eligible GA4 + brand-Trends weeks. Configure the API with:
#   AI_IMPACT_REFIT_MODE=cloud_run
#   AI_IMPACT_REFIT_JOB_NAME=geo-audit-ai-impact-refit
#   AI_IMPACT_REFIT_JOB_PROJECT=geo-tool-emea-ds
#   AI_IMPACT_REFIT_JOB_REGION=europe-west1
#   AI_IMPACT_REFIT_GCS_ROOT=gs://<bucket>/ai_impact_runs
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_NAME="${1:-dev}"
case "${ENV_NAME}" in
  dev|staging) ;;
  *)
    echo "usage: $0 [dev|staging]" >&2
    exit 1
    ;;
esac

PROJECT="${GCP_PROJECT:-geo-tool-emea-ds}"
REGION="${GCP_REGION:-europe-west1}"
if [[ "${ENV_NAME}" == "staging" ]]; then
  JOB_NAME="${AI_IMPACT_REFIT_JOB_NAME:-geo-audit-staging-ai-impact-refit}"
  BUCKET="${GCS_BUCKET:-${PROJECT}-geo-audit-staging}"
else
  JOB_NAME="${AI_IMPACT_REFIT_JOB_NAME:-geo-audit-ai-impact-refit}"
  BUCKET="${GCS_BUCKET:-${PROJECT}-geo-audit-dev}"
fi

AR_REPO="${ARTIFACT_REGISTRY_REPO:-geo-audit}"
IMAGE_NAME="${AI_IMPACT_REFIT_IMAGE_NAME:-ai-impact-refit}"
IMAGE_TAG="${AI_IMPACT_REFIT_IMAGE_TAG:-${ENV_NAME}-$(date +%Y%m%d-%H%M%S)}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${IMAGE_NAME}:${IMAGE_TAG}"
SA_EMAIL="${CLOUD_RUN_SA:-geo-audit-tool@${PROJECT}.iam.gserviceaccount.com}"
SKIP_BUILD="${SKIP_BUILD:-0}"

# NumPyro site-inclusive fits (4 outcome/spec combinations) need headroom.
TASK_TIMEOUT="${TASK_TIMEOUT:-7200s}"
MEMORY="${MEMORY:-8Gi}"
CPU="${CPU:-4}"

GCS_ROOT="gs://${BUCKET}/ai_impact_runs"

echo "==> Project: ${PROJECT}  Region: ${REGION}  Job: ${JOB_NAME}"
echo "==> Image:   ${IMAGE}"
echo "==> Bucket:  gs://${BUCKET}"
echo "==> GCS root for run I/O: ${GCS_ROOT}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI is required." >&2
  exit 1
fi

# Same SA-key token injection as deploy_cloud_run_dev.sh (corporate SSL proxy).
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
  echo "==> Token injected (expires ~1h)."
fi

gcloud config set project "${PROJECT}" >/dev/null

if ! gcloud artifacts repositories describe "${AR_REPO}" \
  --location="${REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "==> Creating Artifact Registry repo ${AR_REPO}…"
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --project="${PROJECT}" \
    --description="GEO audit tool images"
fi

if [[ "${SKIP_BUILD}" != "1" ]]; then
  echo "==> Cloud Build: push ${IMAGE}"
  BUILD_ID="$(gcloud builds submit \
    --project="${PROJECT}" \
    --region="${REGION}" \
    --config=deploy/cloudbuild-ai-impact-refit.yaml \
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
else
  echo "==> SKIP_BUILD=1: using IMAGE=${IMAGE}"
fi

JOB_ENV="GEO_DATA_ROOT=/var/geo-data,GCS_BUCKET=${BUCKET},AI_IMPACT_REFIT_GCS_ROOT=${GCS_ROOT}"

echo "==> Deploying Cloud Run Job ${JOB_NAME}…"
gcloud run jobs deploy "${JOB_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${SA_EMAIL}" \
  --cpu="${CPU}" \
  --memory="${MEMORY}" \
  --tasks=1 \
  --parallelism=1 \
  --max-retries=0 \
  --task-timeout="${TASK_TIMEOUT}" \
  --command=python \
  --args=-m,jobs.ai_impact_refit.run_job \
  --set-env-vars="${JOB_ENV}" \
  --add-volume=name=geo-data,type=cloud-storage,bucket="${BUCKET}" \
  --add-volume-mount=volume=geo-data,mount-path=/var/geo-data

# Allow the geo-audit API SA to execute this job with per-run env overrides.
gcloud run jobs add-iam-policy-binding "${JOB_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" \
  --quiet >/dev/null || true
gcloud run jobs add-iam-policy-binding "${JOB_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.jobsExecutorWithOverrides" \
  --quiet >/dev/null || true

echo ""
echo "Deployed Cloud Run Job ${JOB_NAME} → ${IMAGE}"
echo ""
echo "Point the geo-audit API (${ENV_NAME}) at this job:"
echo "  AI_IMPACT_REFIT_MODE=cloud_run"
echo "  AI_IMPACT_REFIT_JOB_NAME=${JOB_NAME}"
echo "  AI_IMPACT_REFIT_JOB_PROJECT=${PROJECT}"
echo "  AI_IMPACT_REFIT_JOB_REGION=${REGION}"
echo "  AI_IMPACT_REFIT_GCS_ROOT=${GCS_ROOT}"
echo ""
echo "Manual test (after staging panel.csv under the run URI):"
echo "  gcloud run jobs execute ${JOB_NAME} \\"
echo "    --project=${PROJECT} --region=${REGION} --wait \\"
echo "    --update-env-vars=AI_IMPACT_REFIT_RUN_URI=${GCS_ROOT}/<run_id>,AI_IMPACT_REFIT_SITE_ID=<run_id>,AI_IMPACT_REFIT_CATEGORY=advertiser-retail"

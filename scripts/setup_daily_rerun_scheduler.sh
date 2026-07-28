#!/usr/bin/env bash
# Setup Cloud Scheduler to trigger daily probe re-runs for all audits.
#
# This script creates two Cloud Scheduler jobs:
#   1. geo-audit-daily-rerun-dev   → hits the dev Cloud Run service at 02:00 UTC daily
#   2. geo-audit-daily-rerun-prod  → hits the prod Cloud Run service at 02:00 UTC daily
#
# The Cloud Run service must already be deployed and the service account must have
# Cloud Run Invoker role (roles/run.invoker).
#
# Prerequisites:
#   gcloud auth login
#   gcloud config set project emea-ds-sandbox
#
# Usage:
#   bash scripts/setup_daily_rerun_scheduler.sh [dev|prod|both]

set -euo pipefail

ENVIRONMENT="${1:-dev}"
PROJECT="${GCP_PROJECT:-emea-ds-sandbox}"
REGION="${GCP_REGION:-europe-west1}"

DEV_SERVICE_URL="${DEV_SERVICE_URL:-https://geo-audit-dev-4sawlje3ya-ew.a.run.app}"
PROD_SERVICE_URL="${PROD_SERVICE_URL:-}"  # Set this when prod is deployed

SA_EMAIL="${CLOUD_RUN_SA:-geo-audit-tool@${PROJECT}.iam.gserviceaccount.com}"

# Daily re-run endpoint — triggers a background re-run for ALL audits
# This endpoint scans the GCS bucket for audit directories and re-runs each one.
RERUN_PATH="/api/scheduled/daily-rerun"

# Schedule: 02:00 UTC daily
SCHEDULE="0 2 * * *"
SCHEDULER_REGION="europe-west1"

echo "==> Project: ${PROJECT}  Region: ${SCHEDULER_REGION}"

setup_job() {
  local env="$1"
  local service_url="$2"
  local job_name="geo-audit-daily-rerun-${env}"
  local target_url="${service_url}${RERUN_PATH}"

  echo ""
  echo "==> Setting up Cloud Scheduler job: ${job_name}"
  echo "    Target: ${target_url}"
  echo "    Schedule: ${SCHEDULE} (UTC)"

  if gcloud scheduler jobs describe "${job_name}" --location="${SCHEDULER_REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
    echo "    Job already exists — updating…"
    gcloud scheduler jobs update http "${job_name}" \
      --location="${SCHEDULER_REGION}" \
      --project="${PROJECT}" \
      --schedule="${SCHEDULE}" \
      --uri="${target_url}" \
      --http-method=POST \
      --message-body='{}' \
      --oidc-service-account-email="${SA_EMAIL}" \
      --oidc-token-audience="${service_url}" \
      --headers="Content-Type=application/json" \
      --time-zone="UTC" \
      --attempt-deadline="30m"
  else
    echo "    Creating new job…"
    gcloud scheduler jobs create http "${job_name}" \
      --location="${SCHEDULER_REGION}" \
      --project="${PROJECT}" \
      --schedule="${SCHEDULE}" \
      --uri="${target_url}" \
      --http-method=POST \
      --message-body='{}' \
      --oidc-service-account-email="${SA_EMAIL}" \
      --oidc-token-audience="${service_url}" \
      --headers="Content-Type=application/json" \
      --time-zone="UTC" \
      --attempt-deadline="30m"
  fi

  echo "    Done. Job ${job_name} is active."
}

case "${ENVIRONMENT}" in
  dev)
    setup_job "dev" "${DEV_SERVICE_URL}"
    ;;
  prod)
    if [[ -z "${PROD_SERVICE_URL}" ]]; then
      echo "ERROR: Set PROD_SERVICE_URL environment variable for prod deployment." >&2
      exit 1
    fi
    setup_job "prod" "${PROD_SERVICE_URL}"
    ;;
  both)
    setup_job "dev" "${DEV_SERVICE_URL}"
    if [[ -n "${PROD_SERVICE_URL}" ]]; then
      setup_job "prod" "${PROD_SERVICE_URL}"
    else
      echo "WARN: PROD_SERVICE_URL not set — skipping prod scheduler."
    fi
    ;;
  *)
    echo "Usage: $0 [dev|prod|both]" >&2
    exit 1
    ;;
esac

echo ""
echo "==> Cloud Scheduler setup complete."
echo ""
echo "To test immediately, run:"
echo "  gcloud scheduler jobs run geo-audit-daily-rerun-${ENVIRONMENT} --location=${SCHEDULER_REGION} --project=${PROJECT}"

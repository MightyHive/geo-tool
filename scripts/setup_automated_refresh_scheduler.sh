#!/usr/bin/env bash
# Setup Cloud Scheduler for automated GEO audit refreshes.
#
# Daily prompts:  02:00 UTC  → POST /api/scheduled/daily-rerun
# Weekly crawl:   01:00 UTC Mondays → POST /api/scheduled/weekly-crawl
#
# Eligibility: audits with created_at on/after 2026-07-22.
#
# Usage:
#   bash scripts/setup_automated_refresh_scheduler.sh [dev|staging|both]

set -euo pipefail

ENVIRONMENT="${1:-dev}"
PROJECT="${GCP_PROJECT:-emea-ds-sandbox}"
REGION="${GCP_REGION:-europe-west1}"
SCHEDULER_REGION="europe-west1"

DEV_SERVICE_URL="${DEV_SERVICE_URL:-https://geo-audit-dev-4sawlje3ya-ew.a.run.app}"
STAGING_SERVICE_URL="${STAGING_SERVICE_URL:-https://geo-audit-staging-4sawlje3ya-ew.a.run.app}"
SA_EMAIL="${CLOUD_RUN_SA:-geo-audit-tool@${PROJECT}.iam.gserviceaccount.com}"

DAILY_PATH="/api/scheduled/daily-rerun"
WEEKLY_PATH="/api/scheduled/weekly-crawl"
DAILY_SCHEDULE="0 2 * * *"
WEEKLY_SCHEDULE="0 1 * * 1"

echo "==> Project: ${PROJECT}  Region: ${SCHEDULER_REGION}"

upsert_http_job() {
  local job_name="$1"
  local service_url="$2"
  local path="$3"
  local schedule="$4"
  local target_url="${service_url}${path}"

  echo ""
  echo "==> ${job_name}"
  echo "    Target: ${target_url}"
  echo "    Schedule: ${schedule} (UTC)"

  if gcloud scheduler jobs describe "${job_name}" --location="${SCHEDULER_REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
    echo "    Updating existing job…"
    gcloud scheduler jobs update http "${job_name}" \
      --location="${SCHEDULER_REGION}" \
      --project="${PROJECT}" \
      --schedule="${schedule}" \
      --uri="${target_url}" \
      --http-method=POST \
      --message-body='{}' \
      --oidc-service-account-email="${SA_EMAIL}" \
      --oidc-token-audience="${service_url}" \
      --headers="Content-Type=application/json" \
      --time-zone="UTC" \
      --attempt-deadline="30m"
  else
    echo "    Creating job…"
    gcloud scheduler jobs create http "${job_name}" \
      --location="${SCHEDULER_REGION}" \
      --project="${PROJECT}" \
      --schedule="${schedule}" \
      --uri="${target_url}" \
      --http-method=POST \
      --message-body='{}' \
      --oidc-service-account-email="${SA_EMAIL}" \
      --oidc-token-audience="${service_url}" \
      --headers="Content-Type=application/json" \
      --time-zone="UTC" \
      --attempt-deadline="30m"
  fi
  echo "    Done."
}

setup_env() {
  local env="$1"
  local service_url="$2"
  upsert_http_job "geo-audit-daily-rerun-${env}" "${service_url}" "${DAILY_PATH}" "${DAILY_SCHEDULE}"
  upsert_http_job "geo-audit-weekly-crawl-${env}" "${service_url}" "${WEEKLY_PATH}" "${WEEKLY_SCHEDULE}"
}

case "${ENVIRONMENT}" in
  dev)
    setup_env "dev" "${DEV_SERVICE_URL}"
    ;;
  staging)
    setup_env "staging" "${STAGING_SERVICE_URL}"
    ;;
  both)
    setup_env "dev" "${DEV_SERVICE_URL}"
    setup_env "staging" "${STAGING_SERVICE_URL}"
    ;;
  *)
    echo "Usage: $0 [dev|staging|both]" >&2
    exit 1
    ;;
esac

echo ""
echo "==> Scheduler setup complete."
echo "Test:"
echo "  gcloud scheduler jobs run geo-audit-daily-rerun-${ENVIRONMENT} --location=${SCHEDULER_REGION} --project=${PROJECT}"
echo "  gcloud scheduler jobs run geo-audit-weekly-crawl-${ENVIRONMENT} --location=${SCHEDULER_REGION} --project=${PROJECT}"

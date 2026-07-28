#!/usr/bin/env bash
# Setup Cloud Scheduler to trigger scheduled audit refresh jobs.
#
# Pub/Sub mode: Cloud Scheduler publishes messages to a topic and a Cloud Function
# launches the Cloud Run scheduler runner job. This avoids IAP-protected HTTP.
#
# HTTP mode: legacy direct HTTP trigger of the Cloud Run service.
#
# Usage:
#   bash scripts/setup_automated_refresh_scheduler.sh [dev|staging|both] [pubsub|http]

set -euo pipefail

ENVIRONMENT="${1:-dev}"
SCHEDULER_MODE="${2:-pubsub}"
PROJECT="${GCP_PROJECT:-emea-ds-sandbox}"
REGION="${GCP_REGION:-europe-west1}"
SCHEDULER_REGION="europe-west1"
TOPIC_NAME="${SCHEDULER_TOPIC:-geo-audit-scheduler-topic}"

DEV_SERVICE_URL="${DEV_SERVICE_URL:-https://geo-audit-dev-4sawlje3ya-ew.a.run.app}"
STAGING_SERVICE_URL="${STAGING_SERVICE_URL:-https://geo-audit-staging-4sawlje3ya-ew.a.run.app}"
SA_EMAIL="${CLOUD_RUN_SA:-geo-audit-tool@${PROJECT}.iam.gserviceaccount.com}"

DEV_BUCKET="${DEV_GCS_BUCKET:-${PROJECT}-geo-audit-dev}"
STAGING_BUCKET="${STAGING_GCS_BUCKET:-${PROJECT}-geo-audit-staging}"

DEV_PROMPT_JOB="${DEV_PROMPT_PROBE_JOB_NAME:-geo-audit-prompt-probes}"
DEV_CRAWL_JOB="${DEV_AUDIT_CRAWL_JOB_NAME:-geo-audit-site-crawls}"
DEV_PDF_JOB="${DEV_PDF_EXPORT_JOB_NAME:-geo-audit-pdf-exports}"
DEV_SENTIMENT_JOB="${DEV_PROMPT_SENTIMENT_JOB_NAME:-geo-audit-prompt-sentiment}"
DEV_CONTENT_QUALITY_JOB="${DEV_CONTENT_QUALITY_JOB_NAME:-geo-audit-content-quality}"

STAGING_PROMPT_JOB="${STAGING_PROMPT_PROBE_JOB_NAME:-geo-audit-staging-prompt-probes}"
STAGING_CRAWL_JOB="${STAGING_AUDIT_CRAWL_JOB_NAME:-geo-audit-staging-site-crawls}"
STAGING_PDF_JOB="${STAGING_PDF_EXPORT_JOB_NAME:-geo-audit-staging-pdf-exports}"
STAGING_SENTIMENT_JOB="${STAGING_PROMPT_SENTIMENT_JOB_NAME:-geo-audit-staging-prompt-sentiment}"
STAGING_CONTENT_QUALITY_JOB="${STAGING_CONTENT_QUALITY_JOB_NAME:-geo-audit-staging-content-quality}"

DAILY_PATH="/api/scheduled/daily-rerun"
WEEKLY_PATH="/api/scheduled/weekly-crawl"
DAILY_SCHEDULE="0 2 * * *"
WEEKLY_SCHEDULE="0 1 * * 1"

echo "==> Project: ${PROJECT}  Region: ${SCHEDULER_REGION}  Mode: ${SCHEDULER_MODE}"

generate_pubsub_message() {
  local action="$1"
  local env="$2"
  local bucket prompt_job crawl_job pdf_job sentiment_job content_job app_env

  if [[ "${env}" == "staging" ]]; then
    app_env="staging"
    bucket="${STAGING_BUCKET}"
    prompt_job="${STAGING_PROMPT_JOB}"
    crawl_job="${STAGING_CRAWL_JOB}"
    pdf_job="${STAGING_PDF_JOB}"
    sentiment_job="${STAGING_SENTIMENT_JOB}"
    content_job="${STAGING_CONTENT_QUALITY_JOB}"
  else
    app_env="development"
    bucket="${DEV_BUCKET}"
    prompt_job="${DEV_PROMPT_JOB}"
    crawl_job="${DEV_CRAWL_JOB}"
    pdf_job="${DEV_PDF_JOB}"
    sentiment_job="${DEV_SENTIMENT_JOB}"
    content_job="${DEV_CONTENT_QUALITY_JOB}"
  fi

  printf '{"action":"%s","environment":"%s","APP_ENV":"%s","BUCKET":"%s","GEO_DATA_ROOT":"/var/geo-data","PROMPT_PROBE_JOB_NAME":"%s","PROMPT_PROBE_JOB_REGION":"%s","PROMPT_PROBE_JOB_PROJECT":"%s","AUDIT_CRAWL_JOB_NAME":"%s","AUDIT_CRAWL_JOB_REGION":"%s","AUDIT_CRAWL_JOB_PROJECT":"%s","PDF_EXPORT_JOB_NAME":"%s","PDF_EXPORT_JOB_REGION":"%s","PDF_EXPORT_JOB_PROJECT":"%s","PROMPT_SENTIMENT_JOB_NAME":"%s","PROMPT_SENTIMENT_JOB_REGION":"%s","PROMPT_SENTIMENT_JOB_PROJECT":"%s","CONTENT_QUALITY_JOB_NAME":"%s","CONTENT_QUALITY_JOB_REGION":"%s","CONTENT_QUALITY_JOB_PROJECT":"%s"}' \
    "${action}" "${env}" "${app_env}" "${bucket}" "${prompt_job}" "${REGION}" "${PROJECT}" "${crawl_job}" "${REGION}" "${PROJECT}" "${pdf_job}" "${REGION}" "${PROJECT}" "${sentiment_job}" "${REGION}" "${PROJECT}" "${content_job}" "${REGION}" "${PROJECT}"
}

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

upsert_pubsub_job() {
  local job_name="$1"
  local action="$2"
  local env="$4"
  local schedule="$3"
  local message

  message=$(generate_pubsub_message "${action}" "${env}")
  echo ""
  echo "==> ${job_name}"
  echo "    Topic: ${TOPIC_NAME}"
  echo "    Action: ${action}"
  echo "    Environment: ${env}"
  echo "    Schedule: ${schedule} (UTC)"

  if gcloud scheduler jobs describe "${job_name}" --location="${SCHEDULER_REGION}" --project="${PROJECT}" >/dev/null 2>&1; then
    echo "    Updating existing job…"
    gcloud scheduler jobs update pubsub "${job_name}" \
      --location="${SCHEDULER_REGION}" \
      --project="${PROJECT}" \
      --schedule="${schedule}" \
      --topic="${TOPIC_NAME}" \
      --message-body="${message}" \
      --time-zone="UTC" 
  else
    echo "    Creating job…"
    gcloud scheduler jobs create pubsub "${job_name}" \
      --location="${SCHEDULER_REGION}" \
      --project="${PROJECT}" \
      --schedule="${schedule}" \
      --topic="${TOPIC_NAME}" \
      --message-body="${message}" \
      --time-zone="UTC"
  fi
  echo "    Done."
}

setup_env() {
  local env="$1"
  local service_url="$2"

  if [[ "${SCHEDULER_MODE}" == "pubsub" ]]; then
    upsert_pubsub_job "geo-audit-daily-rerun-${env}" "daily-rerun" "${DAILY_SCHEDULE}" "${env}"
    upsert_pubsub_job "geo-audit-weekly-crawl-${env}" "weekly-crawl" "${WEEKLY_SCHEDULE}" "${env}"
  else
    upsert_http_job "geo-audit-daily-rerun-${env}" "${service_url}" "${DAILY_PATH}" "${DAILY_SCHEDULE}"
    upsert_http_job "geo-audit-weekly-crawl-${env}" "${service_url}" "${WEEKLY_PATH}" "${WEEKLY_SCHEDULE}"
  fi
}

create_pubsub_topic() {
  if gcloud pubsub topics describe "${TOPIC_NAME}" --project="${PROJECT}" >/dev/null 2>&1; then
    echo "==> Pub/Sub topic ${TOPIC_NAME} already exists."
  else
    echo "==> Creating Pub/Sub topic ${TOPIC_NAME}..."
    gcloud pubsub topics create "${TOPIC_NAME}" --project="${PROJECT}"
  fi
}

case "${ENVIRONMENT}" in
  dev)
    [[ "${SCHEDULER_MODE}" == "pubsub" ]] && create_pubsub_topic
    setup_env "dev" "${DEV_SERVICE_URL}"
    ;;
  staging)
    [[ "${SCHEDULER_MODE}" == "pubsub" ]] && create_pubsub_topic
    setup_env "staging" "${STAGING_SERVICE_URL}"
    ;;
  both)
    [[ "${SCHEDULER_MODE}" == "pubsub" ]] && create_pubsub_topic
    setup_env "dev" "${DEV_SERVICE_URL}"
    setup_env "staging" "${STAGING_SERVICE_URL}"
    ;;
  *)
    echo "Usage: $0 [dev|staging|both] [pubsub|http]" >&2
    exit 1
    ;;
esac

echo ""
echo "==> Scheduler setup complete."
echo "Test:"
echo "  gcloud scheduler jobs run geo-audit-daily-rerun-${ENVIRONMENT} --location=${SCHEDULER_REGION} --project=${PROJECT}"
echo "  gcloud scheduler jobs run geo-audit-weekly-crawl-${ENVIRONMENT} --location=${SCHEDULER_REGION} --project=${PROJECT}"

#!/usr/bin/env bash
# Setup Cloud Scheduler + Pub/Sub launcher for automated audit refresh.
#
# Canonical flow:
#   Cloud Scheduler → geo-audit-scheduler-topic → geo-audit-scheduler-launcher (CF)
#     → geo-audit-scheduler-runner-dev | geo-audit-scheduler-runner-staging (Cloud Run Job)
#       → prompt-probe / site-crawl Jobs for that environment
#
# Pub/Sub mode (default): avoids IAP-protected HTTP on the API service.
# HTTP mode: legacy direct HTTP trigger of the Cloud Run service.
#
# Usage:
#   bash scripts/setup_automated_refresh_scheduler.sh [dev|staging|both] [pubsub|http]
#
# Exclusion lists (comma-separated audit folder IDs; empty by default):
#   DEV_EXCLUDED_AUDITS / STAGING_EXCLUDED_AUDITS
#   or shared fallback SCHEDULE_EXCLUDED_AUDITS
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENVIRONMENT="${1:-dev}"
SCHEDULER_MODE="${2:-pubsub}"
PROJECT="${GCP_PROJECT:-geo-tool-emea-ds}"
REGION="${GCP_REGION:-europe-west1}"
SCHEDULER_REGION="europe-west1"
TOPIC_NAME="${SCHEDULER_TOPIC:-geo-audit-scheduler-topic}"
LAUNCHER_FN="${SCHEDULER_LAUNCHER_FN:-geo-audit-scheduler-launcher}"

DEV_SERVICE_URL="${DEV_SERVICE_URL:-https://geo-audit-dev-4sawlje3ya-ew.a.run.app}"
STAGING_SERVICE_URL="${STAGING_SERVICE_URL:-https://geo-audit-staging-4sawlje3ya-ew.a.run.app}"
SA_EMAIL="${CLOUD_RUN_SA:-geo-audit-tool@${PROJECT}.iam.gserviceaccount.com}"

DEV_RUNNER_JOB="${DEV_SCHEDULER_JOB_NAME:-geo-audit-scheduler-runner-dev}"
STAGING_RUNNER_JOB="${STAGING_SCHEDULER_JOB_NAME:-geo-audit-scheduler-runner-staging}"

DAILY_PATH="/api/scheduled/daily-rerun"
MONTHLY_PATH="/api/scheduled/monthly-crawl"
DAILY_SCHEDULE="0 2 * * *"
MONTHLY_SCHEDULE="0 1 1 * *"

DEV_EXCLUDED="${DEV_EXCLUDED_AUDITS:-${SCHEDULE_EXCLUDED_AUDITS:-}}"
STAGING_EXCLUDED="${STAGING_EXCLUDED_AUDITS:-${SCHEDULE_EXCLUDED_AUDITS:-}}"

echo "==> Project: ${PROJECT}  Region: ${SCHEDULER_REGION}  Mode: ${SCHEDULER_MODE}"

generate_pubsub_message() {
  local action="$1"
  local env="$2"
  local excluded_audits="$3"
  python3 -c 'import json,sys; print(json.dumps({"action":sys.argv[1],"environment":sys.argv[2],"excluded_audits":sys.argv[3]}, separators=(",",":")))' \
    "${action}" "${env}" "${excluded_audits}"
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
  local schedule="$3"
  local env="$4"
  local excluded_audits="$5"
  local message

  message=$(generate_pubsub_message "${action}" "${env}" "${excluded_audits}")
  echo ""
  echo "==> ${job_name}"
  echo "    Topic: ${TOPIC_NAME}"
  echo "    Action: ${action}"
  echo "    Environment: ${env}"
  echo "    excluded_audits: ${excluded_audits:-<empty>}"
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
  local excluded="$3"

  if [[ "${SCHEDULER_MODE}" == "pubsub" ]]; then
    upsert_pubsub_job "geo-audit-daily-rerun-${env}" "daily-rerun" "${DAILY_SCHEDULE}" "${env}" "${excluded}"
    upsert_pubsub_job "geo-audit-monthly-crawl-${env}" "monthly-crawl" "${MONTHLY_SCHEDULE}" "${env}" "${excluded}"
  else
    local daily_path="${DAILY_PATH}"
    local monthly_path="${MONTHLY_PATH}"
    if [[ -n "${excluded}" ]]; then
      local qs
      qs=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.urlencode({"excluded_audits": sys.argv[1]}))' "${excluded}")
      daily_path="${DAILY_PATH}?${qs}"
      monthly_path="${MONTHLY_PATH}?${qs}"
    fi
    upsert_http_job "geo-audit-daily-rerun-${env}" "${service_url}" "${daily_path}" "${DAILY_SCHEDULE}"
    upsert_http_job "geo-audit-monthly-crawl-${env}" "${service_url}" "${monthly_path}" "${MONTHLY_SCHEDULE}"
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

deploy_launcher_function() {
  echo ""
  echo "==> Deploying Pub/Sub launcher Function ${LAUNCHER_FN}…"
  gcloud functions deploy "${LAUNCHER_FN}" \
    --gen2 \
    --runtime=python312 \
    --region="${REGION}" \
    --project="${PROJECT}" \
    --source="${ROOT}/functions/scheduler_runner" \
    --entry-point=pubsub_handler \
    --trigger-topic="${TOPIC_NAME}" \
    --service-account="${SA_EMAIL}" \
    --set-env-vars="SCHEDULER_JOB_PROJECT=${PROJECT},SCHEDULER_JOB_REGION=${REGION},DEV_SCHEDULER_JOB_NAME=${DEV_RUNNER_JOB},STAGING_SCHEDULER_JOB_NAME=${STAGING_RUNNER_JOB}" \
    --timeout=60s \
    --memory=256Mi \
    --max-instances=5 \
    --quiet

  for _job in "${DEV_RUNNER_JOB}" "${STAGING_RUNNER_JOB}"; do
    if gcloud run jobs describe "${_job}" --project="${PROJECT}" --region="${REGION}" >/dev/null 2>&1; then
      gcloud run jobs add-iam-policy-binding "${_job}" \
        --project="${PROJECT}" \
        --region="${REGION}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/run.invoker" \
        --quiet >/dev/null || true
      gcloud run jobs add-iam-policy-binding "${_job}" \
        --project="${PROJECT}" \
        --region="${REGION}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/run.jobsExecutorWithOverrides" \
        --quiet >/dev/null || true
    else
      echo "    WARNING: runner Job ${_job} not found yet — deploy via deploy_cloud_run_{dev,staging}.sh first."
    fi
  done
  echo "    Launcher deployed."
}

case "${ENVIRONMENT}" in
  dev)
    if [[ "${SCHEDULER_MODE}" == "pubsub" ]]; then
      create_pubsub_topic
      deploy_launcher_function
    fi
    setup_env "dev" "${DEV_SERVICE_URL}" "${DEV_EXCLUDED}"
    ;;
  staging)
    if [[ "${SCHEDULER_MODE}" == "pubsub" ]]; then
      create_pubsub_topic
      deploy_launcher_function
    fi
    setup_env "staging" "${STAGING_SERVICE_URL}" "${STAGING_EXCLUDED}"
    ;;
  both)
    if [[ "${SCHEDULER_MODE}" == "pubsub" ]]; then
      create_pubsub_topic
      deploy_launcher_function
    fi
    setup_env "dev" "${DEV_SERVICE_URL}" "${DEV_EXCLUDED}"
    setup_env "staging" "${STAGING_SERVICE_URL}" "${STAGING_EXCLUDED}"
    ;;
  *)
    echo "Usage: $0 [dev|staging|both] [pubsub|http]" >&2
    exit 1
    ;;
esac

echo ""
echo "==> Scheduler setup complete."
echo "Canonical Pub/Sub path:"
echo "  Scheduler → ${TOPIC_NAME} → ${LAUNCHER_FN}"
echo "    → ${DEV_RUNNER_JOB} | ${STAGING_RUNNER_JOB}"
echo "Test (does enqueue real work):"
echo "  gcloud scheduler jobs run geo-audit-daily-rerun-dev --location=${SCHEDULER_REGION} --project=${PROJECT}"
echo "  gcloud scheduler jobs run geo-audit-monthly-crawl-dev --location=${SCHEDULER_REGION} --project=${PROJECT}"

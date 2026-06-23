#!/usr/bin/env bash
# Wire GA4 OAuth on Cloud Run from local .streamlit/secrets.toml [auth] (or [ga4_oauth]).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${GCP_PROJECT:-emea-ds-sandbox}"
REGION="${GCP_REGION:-europe-west1}"
SERVICE="${CLOUD_RUN_SERVICE:-geo-audit-staging}"
SECRETS_FILE="${ROOT}/.streamlit/secrets.toml"

if [[ ! -f "${SECRETS_FILE}" ]]; then
  echo "Missing ${SECRETS_FILE} — create it from .streamlit/secrets.toml.example" >&2
  exit 1
fi

_oauth_tmp="$(mktemp)"
python3 - "${SECRETS_FILE}" <<'PY' >"${_oauth_tmp}"
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

path = sys.argv[1]
with open(path, "rb") as f:
    t = tomllib.load(f)
g = t.get("ga4_oauth") if isinstance(t.get("ga4_oauth"), dict) else {}
a = t.get("auth") if isinstance(t.get("auth"), dict) else {}
cid = (g.get("client_id") or a.get("client_id") or "").strip()
csec = (g.get("client_secret") or a.get("client_secret") or "").strip()
cookie = (a.get("cookie_secret") or "").strip()
print(cid)
print(csec)
print(cookie)
PY
CLIENT_ID="$(sed -n '1p' "${_oauth_tmp}")"
CLIENT_SECRET="$(sed -n '2p' "${_oauth_tmp}")"
COOKIE_SECRET="$(sed -n '3p' "${_oauth_tmp}")"
rm -f "${_oauth_tmp}"

if [[ -z "${CLIENT_ID}" || -z "${CLIENT_SECRET}" ]]; then
  echo "No client_id/client_secret in [auth] or [ga4_oauth] in ${SECRETS_FILE}" >&2
  exit 1
fi

for name in google-oauth-client-id-geo-tool google-oauth-client-secret-geo-tool; do
  if ! gcloud secrets describe "${name}" --project="${PROJECT}" >/dev/null 2>&1; then
    gcloud secrets create "${name}" --project="${PROJECT}" --replication-policy=automatic
  fi
done

printf '%s' "${CLIENT_ID}" | gcloud secrets versions add google-oauth-client-id-geo-tool \
  --project="${PROJECT}" --data-file=-
printf '%s' "${CLIENT_SECRET}" | gcloud secrets versions add google-oauth-client-secret-geo-tool \
  --project="${PROJECT}" --data-file=-

SERVICE_URL="$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT}" --region="${REGION}" \
  --format='value(status.url)')"

GA4_REDIRECT="${SERVICE_URL}/api/ga4/callback"

ENV_UPDATE="WEB_PUBLIC_ORIGIN=${SERVICE_URL},DEPLOY_PUBLIC_ORIGIN=${SERVICE_URL},GA4_OAUTH_REDIRECT_URI=${GA4_REDIRECT}"
if [[ -n "${COOKIE_SECRET}" ]]; then
  if ! gcloud secrets describe auth-cookie-secret-geo-tool --project="${PROJECT}" >/dev/null 2>&1; then
    gcloud secrets create auth-cookie-secret-geo-tool --project="${PROJECT}" --replication-policy=automatic
  fi
  printf '%s' "${COOKIE_SECRET}" | gcloud secrets versions add auth-cookie-secret-geo-tool \
    --project="${PROJECT}" --data-file=-
  SECRETS_FLAGS="GA4_OAUTH_CLIENT_ID=google-oauth-client-id-geo-tool:latest,GA4_OAUTH_CLIENT_SECRET=google-oauth-client-secret-geo-tool:latest,AUTH_COOKIE_SECRET=auth-cookie-secret-geo-tool:latest"
else
  SECRETS_FLAGS="GA4_OAUTH_CLIENT_ID=google-oauth-client-id-geo-tool:latest,GA4_OAUTH_CLIENT_SECRET=google-oauth-client-secret-geo-tool:latest"
fi

SA_EMAIL="${CLOUD_RUN_SA:-geo-audit-tool@${PROJECT}.iam.gserviceaccount.com}"
for name in google-oauth-client-id-geo-tool google-oauth-client-secret-geo-tool auth-cookie-secret-geo-tool; do
  if gcloud secrets describe "${name}" --project="${PROJECT}" >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding "${name}" \
      --project="${PROJECT}" \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="roles/secretmanager.secretAccessor" \
      --quiet >/dev/null 2>&1 || true
  fi
done

gcloud run services update "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --update-env-vars="${ENV_UPDATE}" \
  --set-secrets="${SECRETS_FLAGS}"

echo ""
echo "GA4 OAuth configured on ${SERVICE}"
echo "  Redirect URI (add in Google Cloud Console): ${GA4_REDIRECT}"
echo "  Scopes: analytics.readonly, openid, email, profile"

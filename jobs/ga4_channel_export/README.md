# GA4 channel export job (AI impact)

Calls `research/ga4/ga4_channel_export.py::run_export` — same classification and Data API
pull as research.

## Auth

Same Google user OAuth as the setup wizard:

1. API writes `ga4_adc.json` from `request.session["ga4_user_creds_dict"]`
2. Job env: `GOOGLE_APPLICATION_CREDENTIALS` / `GA4_OAUTH_TOKEN_PATH` → that file
3. On failure: `ga4_meta.json` sets `needs_ga4_reauth: true`; UI uses `/api/ga4/login?return_to=…`

Local: `FORCE_GA4_LOGIN=1` for interactive CLI re-login.

## Env

`RUN_ID`, `GA4_PROPERTY_ID`, `GA4_PROPERTY_NAME`, `START_DATE`, `END_DATE`, `GCS_OUTPUT_URI`

# GA4 weekly channel export for AI-impact estimates
#
# Calls research/ga4/ga4_channel_export.py::run_export (same rules + OAuth as research).
#
# Auth: GOOGLE_APPLICATION_CREDENTIALS (wizard session ADC) or GA4_OAUTH_TOKEN_PATH.
# On auth failure sets needs_ga4_reauth in ga4_meta.json — UI uses /api/ga4/login
# (same flow as the setup wizard).

FROM python:3.12-slim-bookworm
WORKDIR /workspace
# Job image must include backend + research/ga4 (not the full research tree).
COPY jobs/ga4_channel_export/run_job.py /workspace/run_job.py
# Build context = repo root:
#   docker build -f jobs/ga4_channel_export/Dockerfile .
COPY backend /workspace/backend
COPY research/ga4 /workspace/research/ga4
COPY research/paths.py /workspace/research/paths.py
ENV PYTHONPATH=/workspace/backend:/workspace/research:/workspace/research/ga4
ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir pandas google-analytics-data google-auth google-auth-oauthlib \
      google-cloud-storage
CMD ["python", "-u", "/workspace/run_job.py"]

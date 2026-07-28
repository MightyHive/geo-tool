# Search Console export job
# Env: RUN_ID, GSC_SITE_URL, START_DATE, END_DATE, GCS_OUTPUT_URI
# Auth: user OAuth with webmasters.readonly (ADC JSON)

FROM python:3.12-slim-bookworm
WORKDIR /workspace
COPY run_job.py /workspace/run_job.py
ENV PYTHONUNBUFFERED=1
CMD ["python", "-u", "/workspace/run_job.py"]

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace
WORKDIR /workspace

COPY jobs/ai_signal_refresh/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY backend /workspace/backend
COPY jobs/ai_signal_refresh /workspace/jobs/ai_signal_refresh

# AI_SIGNAL_INPUT_PATH must point to the mounted 61-site session snapshot.
CMD ["python", "-m", "jobs.ai_signal_refresh.run_job"]

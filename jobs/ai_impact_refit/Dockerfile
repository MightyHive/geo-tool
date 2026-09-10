FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PYTHONPATH=/workspace:/workspace/backend:/workspace/research/modelling
WORKDIR /workspace

COPY jobs/ai_impact_refit/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY backend /workspace/backend
COPY research/modelling/custom_ci.py /workspace/research/modelling/custom_ci.py
COPY jobs/ai_impact_refit /workspace/jobs/ai_impact_refit

CMD ["python", "-m", "jobs.ai_impact_refit.run_job"]

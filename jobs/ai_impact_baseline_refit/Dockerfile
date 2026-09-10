FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg

WORKDIR /app
COPY jobs/ai_impact_baseline_refit/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY jobs/ai_impact_baseline_refit/run_job.py /app/run_job.py
COPY research/modelling/custom_ci.py /app/modelling/custom_ci.py
COPY research/modelling/train_baseline_artifact.py /app/modelling/train_baseline_artifact.py

CMD ["python", "/app/run_job.py"]

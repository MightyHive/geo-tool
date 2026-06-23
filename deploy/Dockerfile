# GEO audit staging/production: React UI + FastAPI + create-report crawl pipeline.
# Build: docker build -f deploy/Dockerfile .
# Or:   ./scripts/deploy_cloud_run_staging.sh

FROM node:22-bookworm-slim AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    APP_ENV=staging \
    GEO_DATA_ROOT=/var/geo-data \
    PYTHONPATH=/app/backend

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

# Application code (see .dockerignore — no audit_output, venv, or local secrets)
COPY backend ./backend
COPY assets ./assets
COPY api ./api
COPY skills ./skills
COPY scripts/build_prompt_performance_section.py ./scripts/

COPY --from=web-build /build/web/dist ./web/dist

RUN mkdir -p /var/geo-data/audit_output /var/geo-data/audit_archive

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-keep-alive 75"]

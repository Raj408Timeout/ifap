# syntax=docker/dockerfile:1.7
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

FROM base AS builder
COPY backend/pyproject.toml ./
COPY backend/src ./src
RUN pip install --prefix=/install .

FROM base AS runtime
RUN useradd --create-home --uid 10001 ifap
COPY --from=builder /install /usr/local
COPY backend/data ./data
# Data files live in the image; everything the app *writes* goes to /tmp (writable by the
# non-root user, and the only writable path on Cloud Run). Real deployments point
# IFAP_DATABASE__URL at Postgres; the SQLite default only lets the image start standalone.
ENV IFAP_KNOWLEDGE__DATASET_PATH=/app/data/question_templates.json \
    IFAP_WORKFLOW__TAXONOMY_PATH=/app/data/intent_taxonomy.json \
    IFAP_KNOWLEDGE__CHROMA_PATH=/tmp/ifap/chroma \
    IFAP_DATABASE__URL=sqlite+aiosqlite:////tmp/ifap/ifap.db \
    PORT=8000
USER ifap
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ[\"PORT\"]}/health')"
# Cloud Run injects $PORT (8080); docker compose uses the default 8000.
CMD ["sh", "-c", "mkdir -p /tmp/ifap && exec uvicorn ifap.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]

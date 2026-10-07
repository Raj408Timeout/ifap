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
ENV IFAP_KNOWLEDGE__DATASET_PATH=/app/data/question_templates.json \
    IFAP_WORKFLOW__TAXONOMY_PATH=/app/data/intent_taxonomy.json
USER ifap
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "ifap.main:app", "--host", "0.0.0.0", "--port", "8000"]

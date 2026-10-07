# 9. Deployment Strategy

## Environments
| Env | Runtime | DB | Vector | LLM |
|---|---|---|---|---|
| Local dev | `make api` + `make web` | SQLite | embedded Chroma (`backend/.chroma`) | none (heuristic) or any key |
| Docker | `make up` (docker compose) | PostgreSQL 17 | Chroma server | optional |
| Staging/Prod (target) | AKS / GKE / Azure Container Apps | Managed Postgres | Azure AI Search / Pinecone | Azure OpenAI / Vertex via OpenAI-compatible gateway |

## Local container topology
```mermaid
flowchart LR
  B[Browser :3000] --> W[web<br/>Next.js standalone]
  B --> A[api<br/>FastAPI + uvicorn :8000]
  A --> P[(postgres:17)]
  A --> C[(chroma :8000)]
  A -. optional .-> L[OpenAI-compatible LLM]
```

## Target cloud topology
```mermaid
flowchart TB
  U[Users] --> FD[Front Door / Cloud Armor + WAF]
  FD --> WEB[Web: Next.js]
  FD --> APIM[API Management / Gateway]
  APIM --> API[IFAP API pods - HPA]
  API --> PG[(Managed PostgreSQL)]
  API --> VS[(Vector store)]
  API --> GW[LLM gateway - quotas, caching]
  API --> BUS[[Event broker]]
  BUS --> AGW[Async agent workers - KEDA]
  API --> OTEL[OTel Collector] --> OBS[App Insights / Cloud Trace / Grafana]
  KV[Key Vault / Secret Manager] -.-> API
```

## Pipeline (GitHub Actions, `.github/workflows/ci.yml`)
```mermaid
flowchart LR
  PR[PR / push] --> BE[backend: black · ruff · pylint · pyright strict · pytest+cov]
  PR --> FE[frontend: tsc · next build]
  BE & FE --> IMG[docker compose build]
  IMG -. Phase 2 .-> PUSH[push to registry · SBOM · image scan]
  PUSH -. Phase 2 .-> DEP[deploy staging → smoke → prod blue/green]
```

## Practices
- Images are multi-stage, slim, run as non-root and have health checks.
- Configuration comes only from environment variables (12-factor); secrets come from the platform
  secret store and are never baked into images.
- DB schema is `create_all` in Phase 1, moving to Alembic migrations run as a pre-deploy job.
- Ingestion is idempotent (upsert by `template_id`) and runs on startup if empty, or on demand.
- Rollback is the previous image tag. Agent versions are selected per environment through
  `IFAP_WORKFLOW__PIPELINE`.

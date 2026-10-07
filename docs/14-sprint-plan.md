# 14. Sprint Plan (2-week sprints)

```mermaid
gantt
  title IFAP delivery plan
  dateFormat YYYY-MM-DD
  axisFormat %b %d
  section Phase 1 - Generate
  S1 Foundations (domain, ports, CI gates)        :done, s1, 2026-10-05, 14d
  S2 Agents + LangGraph + Chroma + dataset        :done, s2, after s1, 14d
  S3 API + Chat UI + hardening                    :active, s3, after s2, 14d
  section Phase 2 - Collect & Analyse
  S4 Responses + skip-logic runtime + Alembic     :s4, after s3, 14d
  S5 Analytics service + events → broker          :s5, after s4, 14d
  S6 Reporting dashboard + LLM eval suite         :s6, after s5, 14d
  section Phase 3 - Enterprise
  S7 AuthN/RBAC                                   :s7, after s6, 14d
  S8 Multi-tenancy (RLS, vector namespaces)       :s8, after s7, 14d
  S9 Agent marketplace + async agent workers      :s9, after s8, 14d
```

| Sprint | Goal | Key stories | Exit criteria |
|---|---|---|---|
| S1 | Foundations | Domain model, ports, settings, observability, CI with all gates | `make check` green |
| S2 | Intelligence | Agent framework, 4 agents, LangGraph orchestrator, 200-question dataset, Chroma + ingestion | Pipeline test passes offline |
| S3 | Experience | Generate/revise/publish API, chat + editor UI, Docker Compose | Demo: describe → edit → publish |
| S4 | Responses | `/responses`, runtime skip-logic evaluation, file uploads, Alembic | Respondent can complete a survey |
| S5 | Analytics | Analytics Agent subscribed to `ResponseSubmitted`, aggregates, broker + outbox | Metrics per question |
| S6 | Reporting | Dashboard, Insights Agent, LLM eval harness | Golden-set score ≥ target |
| S7 | Security | OIDC, RBAC roles, audit log | Pen-test findings closed |
| S8 | Tenancy | Tenant context, RLS, per-tenant vector namespaces & quotas | Isolation tests pass |
| S9 | Ecosystem | Agent marketplace, per-tenant pipelines, async workers | 3rd-party agent installed with no core change |

Phase 1 (S1–S3) is what this repository delivers.

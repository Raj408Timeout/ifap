# 8. API Specification (v1)

Base URL `http://localhost:8000`. The interactive OpenAPI docs are at `/docs` and the schema at
`/openapi.json`. All bodies are JSON. Errors use `{"error": "<Type>", "detail": "<message>"}`.

| Method | Path | Purpose | Success |
|---|---|---|---|
| GET | `/health` | Liveness + knowledge document count | 200 |
| POST | `/api/v1/questionnaires/generate` | Run the agent pipeline | 201 |
| GET | `/api/v1/questionnaires?limit&offset` | List (newest first) | 200 |
| GET | `/api/v1/questionnaires/{id}` | Get one | 200 / 404 |
| PUT | `/api/v1/questionnaires/{id}` | Revise (new version) + validation | 200 / 404 / 422 |
| POST | `/api/v1/questionnaires/{id}/publish` | Publish (locks) | 200 / 422 |
| GET | `/api/v1/knowledge/search?q&survey_type&top_k` | Inspect RAG retrieval | 200 |
| POST | `/api/v1/knowledge/ingest` | Re-ingest the template source (idempotent upsert) | 200 |
| GET | `/api/v1/agents` | Registered agents, active pipeline, LLM status | 200 |
| GET | `/api/v1/llm` | LLM provider, model, `configured`, `enabled` | 200 |
| PUT | `/api/v1/llm` | `{"enabled": bool}` - runtime on/off switch (422 if no provider configured) | 200 / 422 |

## POST /api/v1/questionnaires/generate
Request:
```json
{ "message": "Create a 12 question customer satisfaction survey for our online store",
  "question_count": null, "survey_type": null }
```
Response (abridged):
```json
{
  "questionnaire": {
    "id": "6f1c…", "title": "Customer Satisfaction Survey", "survey_type": "customer_satisfaction",
    "status": "draft", "version": 1,
    "questions": [{
      "id": "cs-005", "label": "Did you contact customer support during your last purchase?",
      "category": "Support", "answer_type": "boolean", "choices": [],
      "validations": [{"kind": "required", "value": true, "message": null}],
      "dependency_rules": [], "business_tags": ["support"]
    }],
    "source_template_ids": ["cs-005", "…"]
  },
  "intent": { "survey_type": "customer_satisfaction", "question_count": 12, "confidence": 0.75, "…": "…" },
  "validation": { "is_valid": true, "issues": [] },
  "trace": [{ "agent": "intent", "version": "1.0.0", "status": "succeeded", "attempts": 1,
              "duration_ms": 0.2, "strategy": "heuristic", "note": "" }],
  "source_count": 40
}
```

## Error mapping
| Domain error | HTTP |
|---|---|
| `NotFoundError` | 404 |
| `DomainRuleViolationError` (e.g. editing a published questionnaire) | 422 |
| `AgentExecutionError` (pipeline halted) | 502 |
| Request validation (Pydantic) | 422 |

## Versioning & evolution
URI versioning (`/api/v1`). Changes are additive within v1, and breaking changes go to `/api/v2`
served side-by-side. Phase 2 adds `/responses`, `/analytics` and a streaming
`/questionnaires/generate:stream` (SSE of agent trace events).

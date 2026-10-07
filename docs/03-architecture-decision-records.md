# 3. Architecture Decision Records

Format: Context → Decision → Consequences. Status of all: **Accepted** (Phase 1).

## ADR-001 Hexagonal architecture with DDD layering
**Context.** Many replaceable technologies (LLM, vector DB, SQL DB) and a long roadmap of agents.
**Decision.** Layers `domain ← application ← agents/orchestration ← adapters ← api`. The core
depends only on *ports* (`typing.Protocol`). `api/container.py` is the single composition root.
**Consequences.** Swapping infrastructure means writing an adapter. Enforced by
`tests/architecture/test_layering.py` on every CI run.

## ADR-002 LangGraph as the workflow orchestrator, behind a port
**Context.** Need stateful, inspectable multi-agent flows with branching later (human approval,
loops, parallel fan-out).
**Decision.** `LangGraphWorkflowOrchestrator` implements `WorkflowOrchestrator`. The graph is
built *from configuration* (`IFAP_WORKFLOW__PIPELINE`); a conditional edge after each node
stops the run when an agent halts.
**Consequences.** LangGraph types never leak into agents. The orchestrator could be replaced
(Temporal, Azure Durable Functions) without touching agents.

## ADR-003 Plugin-based agent framework
**Decision.** Agents subclass `BaseAgent` and self-register with `@agent_plugin(descriptor)`.
`load_plugins()` imports configured modules **and** the `ifap.agents` entry-point group, so
third-party packages add agents with no changes to IFAP.
**Consequences.** Open/Closed for agents. Descriptors carry `name`, `version` and
`capabilities` for the future marketplace.

## ADR-004 Pydantic v2 in the domain layer
**Context.** Purists keep the domain free of libraries. We need strong validation and JSON
round-trips everywhere.
**Decision.** Pydantic is the *only* third-party package allowed in `ifap.domain`. Models are
frozen value objects, and mutations return new instances (`revise`, `publish`).
**Consequences.** Less mapping code. Architecture tests reject any other third-party import.

## ADR-005 Grounded generation with deterministic fallback
**Decision.** The Builder's LLM output is a *plan* (selected ids + label rewrites), never a raw
question schema. Plans not grounded in retrieved candidates are rejected. With no LLM configured,
`DisabledLLMClient` (Null Object) raises `LLMUnavailableError`, and agents fall back to
deterministic strategies.
**Consequences.** No hallucinated answer types or choices. The platform is fully testable offline.
**Addendum - runtime switch.** `SwitchableLLMClient` (application layer, Proxy over `LLMClient`)
exposes `LLMControl` (`GET/PUT /api/v1/llm`). Off, unconfigured, unreachable and ungrounded
cases all surface as `LLMUnavailableError`, so there is exactly one fallback path, and the agent
trace records its reason. The free default provider is Ollama (`IFAP_LLM__PROVIDER=ollama`) via
the existing OpenAI-compatible adapter. The switch is process-local; multi-replica deployments
would move it to shared configuration (feature-flag service) in Phase 3.

## ADR-006 In-process event bus first, broker later
**Decision.** Use cases publish domain events (`QuestionnaireGenerated`, `QuestionnaireRevised`,
`QuestionnairePublished`, `KnowledgeIngested`) via the `EventPublisher` port, backed for now by
`InMemoryEventBus`.
**Consequences.** Future agents (analytics, sentiment) subscribe without coupling. Move to
Kafka / Azure Service Bus with an outbox when agents run out of process.

## ADR-007 Vector store abstraction with externally computed embeddings
**Decision.** `KnowledgeProvider` hides the vector store, and `EmbeddingProvider` computes
vectors. Chroma is used with `embedding_function=None`, so all stores share the same embeddings.
`InMemoryKnowledgeProvider` is the executable reference implementation.
**Consequences.** Pinecone, Azure AI Search and Weaviate adapters only translate
upsert/query/filter. The POC uses a deterministic feature-hashing embedder (no model download).

## ADR-008 Document-relational persistence for questionnaires
**Decision.** Store the aggregate as JSON plus indexed scalar columns (`survey_type`, `status`,
`updated_at`). SQLAlchemy 2.0 async uses PostgreSQL in Docker and SQLite locally/in tests.
**Consequences.** One read per aggregate and an easy evolving schema. Analytics in Phase 2 use
separate response tables.

## ADR-009 Configuration-driven, no magic values
**Decision.** All tunables live in `ifap.config.settings` (pydantic-settings, `IFAP_` prefix,
`__` nesting). Survey-type keywords and stopwords live in `data/intent_taxonomy.json`.

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
built *from configuration* (`IFAP_WORKFLOW__WORKFLOWS`, see ADR-010); a conditional edge after each node
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

## ADR-010 Workflows as configured graphs; agents emit signals, not destinations
**Context.** Autonomy needs loops and agent-influenced routing, but agents that name their
successors would hard-code the graph and break plug-in extensibility.
**Decision.** `WorkflowDefinition` (`steps`, `routes`, `max_visits_per_step`) is configuration.
Agents set `WorkflowState.signal` (e.g. validation's `invalid` / `incomplete`). The LangGraph
router maps `(step, signal)` to a next step, capped by visit count. Several named workflows are
compiled at start-up and selected per request. New agent outputs go into
`WorkflowState.artifacts[name]`, so the core state class does not change.
**Consequences.** Loops, retries and new topologies are config changes. Visit caps bound every
loop. The standard workflow behaves exactly as before.

## ADR-011 Tool calling in the LLM port + a guarded tool-loop runtime
**Decision.** `LLMClient.converse` (with provider-neutral `ChatMessage` / `ToolSpec` /
`ToolCall` DTOs) is implemented once in the adapter. `agents.runtime.run_tool_loop` is the shared
agent loop with hard guardrails (`max_tool_calls`, `max_seconds`). Tool failures go back to the
model as error results so it can self-correct. Tools are declared from Pydantic argument models.
The first consumer is `autonomous_builder`: grounded (it may only submit retrieved ids), its full
tool transcript is stored as an artifact, and it falls back to the deterministic selector.
**Consequences.** Any provider with tool calling works, including Ollama `qwen3:8b`, which was
verified live. Every autonomous decision can be audited from the trace and artifacts.

## ADR-012 MCP server as a driving adapter
**Decision.** `ifap.mcp_server` exposes the application services as MCP tools (official `mcp`
SDK 2.x, `MCPServer`) over stdio (Claude Desktop/Code) and streamable HTTP. It uses the same
composition root as the REST API and contains no business logic. Domain errors become
`ToolError`, so the calling model sees actionable messages. Architecture tests forbid inner layers
from importing it.
**Consequences.** External agents such as Claude can use IFAP as a toolset, including acting as
the builder themselves. IFAP *consuming* external MCP tools (client side) is a separate, future
tool-provider port.

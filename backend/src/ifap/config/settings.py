"""Typed, environment-driven configuration. Every tunable lives here - no magic values in code.

Environment variables use the `IFAP_` prefix and `__` for nesting, e.g.
`IFAP_LLM__PROVIDER=ollama`, `IFAP_WORKFLOW__DEFAULT_WORKFLOW=autonomous`.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[3]


class KnowledgeProviderKind(StrEnum):
    CHROMA = "chroma"
    IN_MEMORY = "in_memory"


class EmbeddingProviderKind(StrEnum):
    HASHING = "hashing"
    OPENAI_COMPATIBLE = "openai_compatible"


class LLMProviderKind(StrEnum):
    DISABLED = "disabled"
    OLLAMA = "ollama"
    GEMINI = "gemini"
    OPENAI_COMPATIBLE = "openai_compatible"


class LLMPreset(BaseModel):
    """Provider defaults; any of them can be overridden with the IFAP_LLM__* settings."""

    model_config = ConfigDict(frozen=True)

    base_url: str | None
    model: str
    timeout_seconds: float
    reasoning_effort: str | None = None
    placeholder_api_key: str | None = None  # for servers that ignore keys (Ollama)
    requests_per_minute: int | None = None  # client-side pacing per model; None = unlimited
    fallback_models: tuple[str, ...] = ()  # tried in order when the primary is unavailable


DEFAULT_TIMEOUT_SECONDS = 60.0
LLM_PRESETS: dict[LLMProviderKind, LLMPreset] = {
    # Local. Thinking models (qwen3) spend ~100 s reasoning on a laptop; the agents' calls are
    # short classify/select tasks, so thinking is off. 8B models generate ~5-20 tokens/s.
    LLMProviderKind.OLLAMA: LLMPreset(
        base_url="http://localhost:11434/v1",
        model="qwen3:8b",
        timeout_seconds=180.0,
        reasoning_effort="none",
        placeholder_api_key="ollama",
    ),
    # Google AI Studio's OpenAI-compatible endpoint (API key from aistudio.google.com).
    # Free tier quotas are *per model* (verified 2026-10-08 from 429 responses for
    # gemini-2.5-flash: 5 requests/minute, 20 requests/day), so a chain of models multiplies
    # daily capacity. Chain chosen from a 2026-10-09 probe of structured output + tool
    # calling on this endpoint (newer previews returned 503 "overloaded" or timed out).
    LLMProviderKind.GEMINI: LLMPreset(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-3.6-flash",
        fallback_models=("gemini-2.5-flash", "gemini-3.1-flash-lite"),
        timeout_seconds=30.0,  # hand a slow model over to the next one quickly
        requests_per_minute=5,
    ),
    LLMProviderKind.OPENAI_COMPATIBLE: LLMPreset(
        base_url=None, model="gpt-4o-mini", timeout_seconds=DEFAULT_TIMEOUT_SECONDS
    ),
}


class LLMSettings(BaseModel):
    """LLM provider + the startup state of the runtime on/off switch.

    `provider` unset: inferred as `openai_compatible` when an api_key is given, else disabled.
    `enabled` is only the *initial* switch position; it can be flipped at runtime via the API.
    Unset fields fall back to the provider's preset in `LLM_PRESETS`.
    """

    provider: LLMProviderKind | None = None
    enabled: bool = True
    api_key: SecretStr | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int = Field(default=1, ge=0)
    reasoning_effort: str | None = None
    # Rate limiting (HTTP 429): queue under the quota, retry after the provider's back-off,
    # and fall back to heuristics if waiting would take longer than the budget.
    requests_per_minute: int | None = Field(default=None, ge=1)
    rate_limit_retries: int = Field(default=1, ge=0)
    # One full quota window (+ margin): a queued call always gets a slot instead of falling back
    rate_limit_max_wait_seconds: float = Field(default=65.0, ge=0)
    rate_limit_default_retry_seconds: float = Field(default=10.0, gt=0)
    # Model chain: None = the provider preset's fallbacks, [] = no fallback models
    fallback_models: list[str] | None = None
    # How long a model that returned 5xx / timed out is skipped before being tried again
    model_cooldown_seconds: float = Field(default=60.0, ge=0)

    @property
    def kind(self) -> LLMProviderKind:
        if self.provider is not None:
            return self.provider
        if self.api_key is not None:
            return LLMProviderKind.OPENAI_COMPATIBLE
        return LLMProviderKind.DISABLED

    @property
    def configured(self) -> bool:
        """A provider is selected *and* usable: hosted providers need a real API key, so a
        missing secret degrades to heuristics instead of crashing the service at start-up."""
        return self.kind is not LLMProviderKind.DISABLED and self.resolved_api_key is not None

    @property
    def preset(self) -> LLMPreset:
        return LLM_PRESETS.get(self.kind, LLM_PRESETS[LLMProviderKind.OPENAI_COMPATIBLE])

    @property
    def resolved_base_url(self) -> str | None:
        return self.base_url or self.preset.base_url

    @property
    def resolved_model(self) -> str:
        return self.model or self.preset.model

    @property
    def resolved_timeout_seconds(self) -> float:
        return self.timeout_seconds or self.preset.timeout_seconds

    @property
    def resolved_reasoning_effort(self) -> str | None:
        return self.reasoning_effort or self.preset.reasoning_effort

    @property
    def resolved_models(self) -> list[str]:
        """Primary model first, then fallbacks, without duplicates."""
        fallbacks = (
            self.preset.fallback_models if self.fallback_models is None else self.fallback_models
        )
        return list(dict.fromkeys([self.resolved_model, *fallbacks]))

    @property
    def resolved_requests_per_minute(self) -> int | None:
        return self.requests_per_minute or self.preset.requests_per_minute

    @property
    def resolved_api_key(self) -> SecretStr | None:
        placeholder = self.preset.placeholder_api_key
        if self.api_key is None and placeholder is not None:
            return SecretStr(placeholder)
        return self.api_key


class EmbeddingSettings(BaseModel):
    provider: EmbeddingProviderKind = EmbeddingProviderKind.HASHING
    model: str = "text-embedding-3-small"
    dimension: int = Field(default=384, ge=16)


class KnowledgeSettings(BaseModel):
    provider: KnowledgeProviderKind = KnowledgeProviderKind.CHROMA
    collection: str = "question_templates"
    chroma_path: Path = BACKEND_ROOT / ".chroma"
    chroma_host: str | None = None
    chroma_port: int = 8000
    dataset_path: Path = BACKEND_ROOT / "data" / "question_templates.json"
    ingest_on_startup: bool = True
    ingest_batch_size: int = Field(default=64, ge=1)


class WorkflowDefinition(BaseModel):
    """A named agent graph.

    `steps` run in order. `routes` maps an agent's emitted *signal* to the step to run next,
    e.g. `{"validation": {"incomplete": "autonomous_builder"}}` - this is how loops and
    agent-decided routing are configured without code. `max_visits_per_step` caps every loop.
    """

    steps: list[str] = Field(min_length=1)
    routes: dict[str, dict[str, str]] = {}
    max_visits_per_step: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def _routes_reference_steps(self) -> Self:
        known = set(self.steps)
        for source, signals in self.routes.items():
            unknown = {source, *signals.values()} - known
            if unknown:
                raise ValueError(f"routes reference unknown steps: {sorted(unknown)}")
        return self


STANDARD_WORKFLOW = WorkflowDefinition(
    steps=["intent", "template_retrieval", "questionnaire_builder", "validation"]
)
AUTONOMOUS_WORKFLOW = WorkflowDefinition(
    steps=["intent", "template_retrieval", "autonomous_builder", "validation"],
    routes={"validation": {"invalid": "autonomous_builder", "incomplete": "autonomous_builder"}},
    max_visits_per_step=2,
)


class WorkflowSettings(BaseModel):
    workflows: dict[str, WorkflowDefinition] = {
        "standard": STANDARD_WORKFLOW,
        "autonomous": AUTONOMOUS_WORKFLOW,
    }
    default_workflow: str = "standard"
    plugin_modules: list[str] = ["ifap.agents.builtin"]
    taxonomy_path: Path = BACKEND_ROOT / "data" / "intent_taxonomy.json"
    retrieval_top_k: int = Field(default=40, ge=1, le=200)
    retrieval_min_score: float = Field(default=0.05, ge=0.0, le=1.0)
    # Prompt size for the Builder's LLM call (local models are slow on long catalogues)
    builder_llm_max_candidates: int = Field(default=20, ge=1)
    default_question_count: int = Field(default=10, ge=1, le=100)
    max_question_count: int = Field(default=50, ge=1, le=100)
    agent_max_attempts: int = Field(default=2, ge=1)
    agent_retry_backoff_seconds: float = Field(default=0.2, ge=0.0)
    # Guardrails for tool-calling (autonomous) agents
    autonomous_max_tool_calls: int = Field(default=8, ge=1)
    autonomous_max_seconds: float = Field(default=600.0, gt=0)

    @model_validator(mode="after")
    def _default_workflow_exists(self) -> Self:
        if self.default_workflow not in self.workflows:
            raise ValueError(f"default_workflow '{self.default_workflow}' is not defined")
        return self


class DatabaseSettings(BaseModel):
    url: str = f"sqlite+aiosqlite:///{BACKEND_ROOT / 'ifap.db'}"
    echo: bool = False
    # Fail fast on a dead connection instead of hanging (Postgres only)
    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    # Start-up resilience: serverless Postgres may be waking up or briefly unreachable
    startup_attempts: int = Field(default=4, ge=1)
    startup_backoff_seconds: float = Field(default=2.0, ge=0)


class ObservabilitySettings(BaseModel):
    service_name: str = "ifap-api"
    log_level: str = "INFO"
    log_json: bool = True
    # stdio MCP servers must keep stdout for the protocol, so they log to stderr
    log_stream: Literal["stdout", "stderr"] = "stdout"
    otlp_endpoint: str | None = None
    console_traces: bool = False


class McpSettings(BaseModel):
    # Stay under the host's tool timeout (Claude Desktop gives up after ~60 s): a tool call
    # waits at most this long, then returns a job id to poll with get_generation_result.
    wait_seconds: float = Field(default=40.0, gt=0)
    max_jobs: int = Field(default=50, ge=1)


class ApiSettings(BaseModel):
    title: str = "Intelligent Feedback & Assessment Platform"
    version: str = "0.1.0"
    prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000"]
    # e.g. r"https://ifap-[a-z0-9-]+-myscope\.vercel\.app": your Vercel scope's deployments only
    cors_origin_regex: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IFAP_",
        env_nested_delimiter="__",
        # backend/.env is found regardless of the working directory (e.g. when Claude Desktop
        # launches the MCP server); a .env in the current directory overrides it.
        env_file=(BACKEND_ROOT / ".env", ".env"),
        extra="ignore",
    )

    environment: str = "local"
    api: ApiSettings = ApiSettings()
    mcp: McpSettings = McpSettings()
    llm: LLMSettings = LLMSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    knowledge: KnowledgeSettings = KnowledgeSettings()
    workflow: WorkflowSettings = WorkflowSettings()
    database: DatabaseSettings = DatabaseSettings()
    observability: ObservabilitySettings = ObservabilitySettings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


ENV_PREFIX = "IFAP_"
NESTED_DELIMITER = "__"


def unrecognised_env_vars(environ: Mapping[str, str]) -> list[str]:
    """`IFAP_*` variables that match no setting - typically a single-underscore typo such as
    `IFAP_LLM_PROVIDER`, which pydantic-settings would otherwise ignore silently."""
    return sorted(
        key for key in environ if key.startswith(ENV_PREFIX) and not _is_known_setting(key)
    )


def _is_known_setting(key: str) -> bool:
    section, _, field = key.removeprefix(ENV_PREFIX).lower().partition(NESTED_DELIMITER)
    info = Settings.model_fields.get(section)
    if info is None:
        return False
    if not field:
        return True
    nested = info.annotation
    return (
        isinstance(nested, type)
        and issubclass(nested, BaseModel)
        and (field.split(NESTED_DELIMITER)[0] in nested.model_fields)
    )

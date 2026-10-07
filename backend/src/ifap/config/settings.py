"""Typed, environment-driven configuration. Every tunable lives here - no magic values in code.

Environment variables use the `IFAP_` prefix and `__` for nesting, e.g.
`IFAP_LLM__API_KEY`, `IFAP_KNOWLEDGE__PROVIDER=chroma`, `IFAP_WORKFLOW__PIPELINE='["intent"]'`.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
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
    OPENAI_COMPATIBLE = "openai_compatible"


# Defaults applied when IFAP_LLM__PROVIDER=ollama and no explicit base_url/model is given.
OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"
OLLAMA_DEFAULT_MODEL = "qwen3:8b"
OLLAMA_PLACEHOLDER_API_KEY = "ollama"  # Ollama ignores the key, the OpenAI client requires one
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"
# Thinking models (qwen3, deepseek-r1) spend ~100 s reasoning on a laptop; the agents' tasks are
# short classify/select calls, so the Ollama preset turns thinking off unless overridden.
OLLAMA_DEFAULT_REASONING_EFFORT = "none"
OLLAMA_DEFAULT_TIMEOUT_SECONDS = 180.0  # local 8B models generate ~5-20 tokens/s
DEFAULT_TIMEOUT_SECONDS = 60.0


class LLMSettings(BaseModel):
    """LLM provider + the startup state of the runtime on/off switch.

    `provider` unset: inferred as `openai_compatible` when an api_key is given, else disabled.
    `enabled` is only the *initial* switch position; it can be flipped at runtime via the API.
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

    @property
    def kind(self) -> LLMProviderKind:
        if self.provider is not None:
            return self.provider
        if self.api_key is not None:
            return LLMProviderKind.OPENAI_COMPATIBLE
        return LLMProviderKind.DISABLED

    @property
    def configured(self) -> bool:
        return self.kind is not LLMProviderKind.DISABLED

    @property
    def resolved_base_url(self) -> str | None:
        if self.base_url or self.kind is not LLMProviderKind.OLLAMA:
            return self.base_url
        return OLLAMA_DEFAULT_BASE_URL

    @property
    def resolved_model(self) -> str:
        if self.model:
            return self.model
        return OLLAMA_DEFAULT_MODEL if self.kind is LLMProviderKind.OLLAMA else OPENAI_DEFAULT_MODEL

    @property
    def resolved_timeout_seconds(self) -> float:
        if self.timeout_seconds is not None:
            return self.timeout_seconds
        if self.kind is LLMProviderKind.OLLAMA:
            return OLLAMA_DEFAULT_TIMEOUT_SECONDS
        return DEFAULT_TIMEOUT_SECONDS

    @property
    def resolved_reasoning_effort(self) -> str | None:
        if self.reasoning_effort or self.kind is not LLMProviderKind.OLLAMA:
            return self.reasoning_effort
        return OLLAMA_DEFAULT_REASONING_EFFORT

    @property
    def resolved_api_key(self) -> SecretStr | None:
        if self.api_key is None and self.kind is LLMProviderKind.OLLAMA:
            return SecretStr(OLLAMA_PLACEHOLDER_API_KEY)
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


class WorkflowSettings(BaseModel):
    pipeline: list[str] = ["intent", "template_retrieval", "questionnaire_builder", "validation"]
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


class DatabaseSettings(BaseModel):
    url: str = f"sqlite+aiosqlite:///{BACKEND_ROOT / 'ifap.db'}"
    echo: bool = False


class ObservabilitySettings(BaseModel):
    service_name: str = "ifap-api"
    log_level: str = "INFO"
    log_json: bool = True
    otlp_endpoint: str | None = None
    console_traces: bool = False


class ApiSettings(BaseModel):
    title: str = "Intelligent Feedback & Assessment Platform"
    version: str = "0.1.0"
    prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IFAP_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    environment: str = "local"
    api: ApiSettings = ApiSettings()
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

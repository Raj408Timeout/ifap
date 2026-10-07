"""Plugin-based agent framework.

* `Agent`            - the abstraction every agent satisfies (the orchestrator knows nothing else).
* `BaseAgent`        - template method adding retries, tracing, metrics and an execution trace.
* `AgentRegistry`    - name -> factory map; agents self-register with `@agent_plugin`.
* `load_plugins`     - imports configured modules + `ifap.agents` entry points, so new agents
                       ship as separate packages without editing this code (Open/Closed).
"""

from __future__ import annotations

import asyncio
import importlib
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict

from ifap.application.ports import KnowledgeProvider, LLMClient
from ifap.application.workflow import AgentStatus, AgentTrace, WorkflowState
from ifap.config.settings import WorkflowSettings
from ifap.domain.errors import AgentExecutionError, AgentNotRegisteredError
from ifap.domain.intent import IntentTaxonomy
from ifap.observability.logging import get_logger
from ifap.observability.telemetry import agent_duration, agent_runs, tracer

ENTRY_POINT_GROUP = "ifap.agents"
_log = get_logger(__name__)


class AgentDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str
    description: str
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentDependencies:
    """Everything an agent may need, injected by the composition root."""

    llm: LLMClient
    knowledge: KnowledgeProvider
    workflow: WorkflowSettings
    taxonomy: IntentTaxonomy


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    state: WorkflowState
    strategy: str = "deterministic"
    note: str = ""


class Agent(ABC):
    descriptor: ClassVar[AgentDescriptor]

    @abstractmethod
    async def run(self, state: WorkflowState) -> WorkflowState:
        """Consume the shared state and return an updated copy."""


class BaseAgent(Agent):
    """Template method: subclasses implement `_execute`; this class adds cross-cutting concerns."""

    def __init__(self, *, max_attempts: int, backoff_seconds: float) -> None:
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds

    @classmethod
    @abstractmethod
    def create(cls, deps: AgentDependencies) -> Self:
        """Factory hook used by the registry (dependency injection entry point)."""

    @abstractmethod
    async def _execute(self, state: WorkflowState) -> AgentOutcome: ...

    async def run(self, state: WorkflowState) -> WorkflowState:
        name = self.descriptor.name
        started = time.perf_counter()
        with tracer.start_as_current_span(f"agent.{name}") as span:
            span.set_attribute("agent.version", self.descriptor.version)
            outcome, attempts, error = await self._run_with_retries(state)
            duration_ms = (time.perf_counter() - started) * 1000
            status = AgentStatus.FAILED if outcome is None else AgentStatus.SUCCEEDED
            span.set_attribute("agent.status", status.value)
            agent_runs.add(1, {"agent": name, "status": status.value})
            agent_duration.record(duration_ms, {"agent": name})
        trace_entry = AgentTrace(
            agent=name,
            version=self.descriptor.version,
            status=status,
            attempts=attempts,
            duration_ms=round(duration_ms, 2),
            strategy=outcome.strategy if outcome else "",
            note=outcome.note if outcome else str(error),
        )
        _log.info("agent.completed", agent=name, status=status.value, duration_ms=duration_ms)
        if outcome is None:
            return state.with_trace(trace_entry).halt(f"Agent '{name}' failed: {error}")
        return outcome.state.with_trace(trace_entry)

    async def _run_with_retries(
        self, state: WorkflowState
    ) -> tuple[AgentOutcome | None, int, Exception | None]:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self._execute(state), attempt, None
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_error = exc
                _log.warning(
                    "agent.retry", agent=self.descriptor.name, attempt=attempt, error=str(exc)
                )
                await asyncio.sleep(self._backoff_seconds * attempt)
        return None, self._max_attempts, last_error


AgentFactory = Callable[[AgentDependencies], Agent]


class AgentRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, AgentFactory] = {}
        self._descriptors: dict[str, AgentDescriptor] = {}

    def register(self, descriptor: AgentDescriptor, factory: AgentFactory) -> None:
        existing = self._descriptors.get(descriptor.name)
        if existing is not None and existing != descriptor:
            raise ValueError(f"Agent '{descriptor.name}' already registered as {existing.version}")
        self._factories[descriptor.name] = factory
        self._descriptors[descriptor.name] = descriptor

    def unregister(self, name: str) -> None:
        self._factories.pop(name, None)
        self._descriptors.pop(name, None)

    def create(self, name: str, deps: AgentDependencies) -> Agent:
        factory = self._factories.get(name)
        if factory is None:
            raise AgentNotRegisteredError(f"No agent registered as '{name}'")
        return factory(deps)

    def descriptors(self) -> list[AgentDescriptor]:
        return list(self._descriptors.values())

    def __contains__(self, name: object) -> bool:
        return name in self._factories


DEFAULT_REGISTRY = AgentRegistry()


def agent_plugin[A: BaseAgent](
    descriptor: AgentDescriptor, registry: AgentRegistry = DEFAULT_REGISTRY
) -> Callable[[type[A]], type[A]]:
    """Class decorator: attaches the descriptor and registers the agent's factory."""

    def decorate(cls: type[A]) -> type[A]:
        cls.descriptor = descriptor
        registry.register(descriptor, cls.create)
        return cls

    return decorate


def load_plugins(modules: Iterable[str]) -> None:
    """Import plugin modules; importing triggers `@agent_plugin` registration."""
    for module in modules:
        importlib.import_module(module)
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        entry_point.load()


def ensure_agent_succeeded(state: WorkflowState) -> WorkflowState:
    if state.halted:
        raise AgentExecutionError(state.halt_reason or "workflow halted")
    return state

"""LangGraph implementation of `WorkflowOrchestrator`.

The graph is *built from a workflow definition* (see `WorkflowDefinition` in settings):

* `steps` become nodes chained in order;
* after every node a router decides the next node:
  1. the agent halted              -> END
  2. the agent emitted a `signal` that `routes` maps to a step, and that step has been
     visited fewer than `max_visits` times -> jump there (this is how loops are made)
  3. otherwise                     -> the next step in order (or END).

Agents never name each other: they emit signals, and configuration owns the topology.
Adding a loop, a retry, or a new agent is a configuration change, not a code change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph

from ifap.agents.framework import Agent
from ifap.application.ports import StepObserver
from ifap.application.workflow import WorkflowState

Routes = Mapping[str, Mapping[str, str]]
NodeFn = Callable[[WorkflowState], Awaitable[dict[str, object]]]


class CompiledGraph(Protocol):
    def astream(self, state: WorkflowState, *, stream_mode: str) -> AsyncIterator[object]: ...


class LangGraphWorkflowOrchestrator:
    def __init__(
        self,
        agents: Sequence[Agent],
        *,
        name: str = "standard",
        routes: Routes | None = None,
        max_visits: int = 3,
    ) -> None:
        names = [agent.descriptor.name for agent in agents]
        if not names:
            raise ValueError("Workflow pipeline must contain at least one agent")
        if len(set(names)) != len(names):
            raise ValueError(f"Workflow '{name}' lists an agent twice: {names}")
        self._name = name
        self._names = names
        self._graph = _compile(agents, routes or {}, max_visits)

    @property
    def name(self) -> str:
        return self._name

    @property
    def pipeline(self) -> list[str]:
        return list(self._names)

    async def run(self, state: WorkflowState, on_step: StepObserver | None = None) -> WorkflowState:
        """Streams the full state after every step; `on_step` sees each one (live progress)."""
        final = state
        async for snapshot in self._graph.astream(state, stream_mode="values"):
            final = WorkflowState.model_validate(snapshot)
            if on_step is not None and final.trace:
                on_step(final)
        return final


def _node(agent: Agent) -> NodeFn:
    async def execute(state: WorkflowState) -> dict[str, object]:
        # A signal is consumed by the router right after the agent that emitted it.
        updated = await agent.run(state.with_signal(None))
        return dict(updated)  # shallow field -> value map; LangGraph merges it into state

    return execute


def _router(
    following: str, signal_routes: Mapping[str, str], max_visits: int
) -> Callable[[WorkflowState], str]:
    def route(state: WorkflowState) -> str:
        if state.halted:
            return END
        target = signal_routes.get(state.signal or "")
        if target is not None and state.visits(target) < max_visits:
            return target
        return following

    return route


def _compile(agents: Sequence[Agent], routes: Routes, max_visits: int) -> CompiledGraph:
    # LangGraph's builder generics are partially untyped; `Any` marks the typed boundary.
    graph: Any = StateGraph(WorkflowState)
    names = [agent.descriptor.name for agent in agents]
    for agent in agents:
        graph.add_node(agent.descriptor.name, _node(agent))
    graph.add_edge(START, names[0])
    for current, following in zip(names, [*names[1:], END], strict=True):
        signal_routes = routes.get(current, {})
        destinations = sorted({following, END, *signal_routes.values()})
        graph.add_conditional_edges(
            current, _router(following, signal_routes, max_visits), destinations
        )
    compiled: CompiledGraph = graph.compile()
    return compiled

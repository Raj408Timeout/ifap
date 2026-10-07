"""LangGraph implementation of `WorkflowOrchestrator`.

The graph is *built from configuration*: an ordered list of agents becomes a chain of nodes,
with a conditional edge after each node that short-circuits to END when an agent halts the
workflow. Adding an agent to `IFAP_WORKFLOW__PIPELINE` adds a node - no code change.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph

from ifap.agents.framework import Agent
from ifap.application.workflow import WorkflowState


class CompiledGraph(Protocol):
    async def ainvoke(self, state: WorkflowState) -> dict[str, object]: ...


NodeFn = Callable[[WorkflowState], Awaitable[dict[str, object]]]


class LangGraphWorkflowOrchestrator:
    def __init__(self, agents: Sequence[Agent]) -> None:
        if not agents:
            raise ValueError("Workflow pipeline must contain at least one agent")
        self._names = [agent.descriptor.name for agent in agents]
        self._graph = _compile(agents)

    @property
    def pipeline(self) -> list[str]:
        return list(self._names)

    async def run(self, state: WorkflowState) -> WorkflowState:
        result = await self._graph.ainvoke(state)
        return WorkflowState.model_validate(result)


def _node(agent: Agent) -> NodeFn:
    async def execute(state: WorkflowState) -> dict[str, object]:
        updated = await agent.run(state)
        return dict(updated)  # shallow field -> value map; LangGraph merges it into state

    return execute


def _route_after(next_node: str) -> Callable[[WorkflowState], str]:
    def route(state: WorkflowState) -> str:
        return END if state.halted else next_node

    return route


def _compile(agents: Sequence[Agent]) -> CompiledGraph:
    # LangGraph's builder generics are partially untyped; `Any` marks the typed boundary.
    graph: Any = StateGraph(WorkflowState)
    names = [agent.descriptor.name for agent in agents]
    for agent in agents:
        graph.add_node(agent.descriptor.name, _node(agent))
    graph.add_edge(START, names[0])
    for current, following in zip(names, [*names[1:], END], strict=True):
        graph.add_conditional_edges(current, _route_after(following), [following, END])
    compiled: CompiledGraph = graph.compile()
    return compiled

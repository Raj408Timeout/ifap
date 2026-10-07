"""Platform endpoints: health, knowledge base search/ingestion, agent catalogue."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ifap.api.dependencies import ContainerDep
from ifap.api.schemas import (
    AgentsResponse,
    HealthResponse,
    IngestionResponse,
    KnowledgeSearchResponse,
    SetLLMRequest,
)
from ifap.application.ports import LLMStatus
from ifap.domain.knowledge import KnowledgeQuery

health_router = APIRouter(tags=["platform"])
router = APIRouter(tags=["platform"])


@health_router.get("/health", response_model=HealthResponse)
async def health(container: ContainerDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        environment=container.settings.environment,
        knowledge_documents=await container.knowledge.count(),
    )


@router.get("/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    container: ContainerDep,
    q: Annotated[str, Query(min_length=1)],
    survey_type: str | None = None,
    top_k: Annotated[int, Query(ge=1, le=100)] = 10,
) -> KnowledgeSearchResponse:
    query = KnowledgeQuery(text=q, survey_type=survey_type, top_k=top_k)
    return KnowledgeSearchResponse(results=await container.knowledge.search(query))


@router.post("/knowledge/ingest", response_model=IngestionResponse)
async def ingest_knowledge(container: ContainerDep) -> IngestionResponse:
    indexed = await container.ingestion.ingest(container.template_source)
    return IngestionResponse(indexed=indexed, total=await container.knowledge.count())


@router.get("/agents", response_model=AgentsResponse)
async def list_agents(container: ContainerDep) -> AgentsResponse:
    return AgentsResponse(
        workflows=container.generator.workflows,
        default_workflow=container.generator.default_workflow,
        llm_enabled=container.llm.status().enabled,
        agents=list(container.agent_descriptors),
    )


@router.get("/llm", response_model=LLMStatus, summary="Current LLM provider and switch state")
async def get_llm_status(container: ContainerDep) -> LLMStatus:
    return container.llm.status()


@router.put("/llm", response_model=LLMStatus, summary="Turn LLM-backed strategies on or off")
async def set_llm_enabled(body: SetLLMRequest, container: ContainerDep) -> LLMStatus:
    return container.llm.set_enabled(body.enabled)

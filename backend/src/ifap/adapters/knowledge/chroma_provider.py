"""Chroma `KnowledgeProvider` (POC vector store).

Embeddings are computed by the injected `EmbeddingProvider` (not Chroma's built-in function),
so switching vector stores never changes the vectors. The full template is stored as a JSON
payload in metadata, letting retrieval rebuild domain objects without a second lookup.
Chroma's client is synchronous, so calls run in a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, cast

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from ifap.adapters.knowledge.filters import clamp_score, flat_metadata, metadata_filters
from ifap.application.ports import EmbeddingProvider
from ifap.config.settings import KnowledgeSettings
from ifap.domain.knowledge import KnowledgeQuery, QuestionTemplate, RetrievedTemplate

PAYLOAD_KEY = "payload"


def create_chroma_client(settings: KnowledgeSettings) -> ClientAPI:
    if settings.chroma_host:
        return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    return chromadb.PersistentClient(path=str(settings.chroma_path))


class ChromaKnowledgeProvider:
    def __init__(
        self, *, client: ClientAPI, collection: str, embeddings: EmbeddingProvider
    ) -> None:
        self._embeddings = embeddings
        self._collection: Collection = client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"}, embedding_function=None
        )

    async def upsert(self, templates: Sequence[QuestionTemplate]) -> int:
        if not templates:
            return 0
        documents = [template.to_document_text() for template in templates]
        vectors = await self._embeddings.embed(documents)
        metadatas = [
            {**flat_metadata(template), PAYLOAD_KEY: template.model_dump_json()}
            for template in templates
        ]
        await asyncio.to_thread(
            self._collection.upsert,
            ids=[template.template_id for template in templates],
            embeddings=cast(Any, vectors),
            documents=documents,
            metadatas=cast(Any, metadatas),
        )
        return len(templates)

    async def search(self, query: KnowledgeQuery) -> list[RetrievedTemplate]:
        [vector] = await self._embeddings.embed([query.text])
        result = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=cast(Any, [vector]),
            n_results=query.top_k,
            where=cast(Any, _where(metadata_filters(query))),
            include=cast(Any, ["metadatas", "distances"]),
        )
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        return [
            _to_retrieved(metadata, distance)
            for metadata, distance in zip(metadatas, distances, strict=True)
        ]

    async def count(self) -> int:
        return await asyncio.to_thread(self._collection.count)


def _where(filters: dict[str, str]) -> dict[str, object] | None:
    clauses: list[dict[str, object]] = [{key: {"$eq": value}} for key, value in filters.items()]
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _to_retrieved(metadata: Mapping[str, object], distance: float) -> RetrievedTemplate:
    template = QuestionTemplate.model_validate_json(str(metadata[PAYLOAD_KEY]))
    return RetrievedTemplate(template=template, score=clamp_score(1.0 - distance))

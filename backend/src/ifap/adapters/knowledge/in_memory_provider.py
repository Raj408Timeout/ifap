"""Reference `KnowledgeProvider` using brute-force cosine similarity. Used in tests and as the
executable specification every other provider must match (see tests/integration)."""

from __future__ import annotations

from collections.abc import Sequence

from ifap.adapters.knowledge.filters import clamp_score, flat_metadata, metadata_filters
from ifap.application.ports import EmbeddingProvider
from ifap.domain.knowledge import KnowledgeQuery, QuestionTemplate, RetrievedTemplate


class InMemoryKnowledgeProvider:
    def __init__(self, embeddings: EmbeddingProvider) -> None:
        self._embeddings = embeddings
        self._items: dict[str, tuple[QuestionTemplate, list[float]]] = {}

    async def upsert(self, templates: Sequence[QuestionTemplate]) -> int:
        vectors = await self._embeddings.embed([t.to_document_text() for t in templates])
        for template, vector in zip(templates, vectors, strict=True):
            self._items[template.template_id] = (template, vector)
        return len(templates)

    async def search(self, query: KnowledgeQuery) -> list[RetrievedTemplate]:
        [query_vector] = await self._embeddings.embed([query.text])
        filters = metadata_filters(query)
        scored = [
            RetrievedTemplate(template=template, score=clamp_score(_dot(query_vector, vector)))
            for template, vector in self._items.values()
            if _matches(template, filters)
        ]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: query.top_k]

    async def get(self, template_ids: Sequence[str]) -> list[QuestionTemplate]:
        return [self._items[tid][0] for tid in template_ids if tid in self._items]

    async def count(self) -> int:
        return len(self._items)


def _matches(template: QuestionTemplate, filters: dict[str, str]) -> bool:
    metadata = flat_metadata(template)
    return all(metadata.get(key) == value for key, value in filters.items())


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))

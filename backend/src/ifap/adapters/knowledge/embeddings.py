"""Embedding adapters.

* `HashingEmbeddingProvider` - deterministic, offline feature-hashing embedder (unigrams +
  bigrams). Good enough for a POC and for reproducible tests; no model download required.
* `OpenAICompatibleEmbeddingProvider` - any OpenAI-compatible embeddings endpoint.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from itertools import pairwise

from langchain_openai import OpenAIEmbeddings

from ifap.config.settings import EmbeddingSettings, LLMSettings

_TOKEN = re.compile(r"[a-z0-9]+")
_BIGRAM_WEIGHT = 0.5
_MIN_STEM_LENGTH = 4


class HashingEmbeddingProvider:
    def __init__(self, dimension: int) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        tokens = [_normalise(token) for token in _TOKEN.findall(text.lower())]
        features = [(token, 1.0) for token in tokens]
        features += [(f"{a}_{b}", _BIGRAM_WEIGHT) for a, b in pairwise(tokens)]
        for feature, weight in features:
            index, sign = self._bucket(feature)
            vector[index] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def _bucket(self, feature: str) -> tuple[int, float]:
        digest = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
        return digest % self._dimension, 1.0 if (digest >> 63) & 1 else -1.0


def _normalise(token: str) -> str:
    """Very light stemming so 'surveys' ~ 'survey' and 'satisfied' ~ 'satisfaction'."""
    for suffix in ("faction", "fied", "ing", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM_LENGTH:
            return token[: -len(suffix)]
    return token


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, embedding: EmbeddingSettings, llm: LLMSettings) -> None:
        self._dimension = embedding.dimension
        self._client = OpenAIEmbeddings(
            model=embedding.model,
            api_key=llm.api_key,
            base_url=llm.base_url,
            dimensions=embedding.dimension,
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._client.aembed_documents(list(texts))

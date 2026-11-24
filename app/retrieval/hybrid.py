from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.core.exceptions import RetrievalError
from app.retrieval.bm25 import BM25Retriever
from app.retrieval.cache import RetrievalCache
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.vector import VectorRetriever
from app.schemas.retrieval import RetrievedChunk


@dataclass(frozen=True)
class RetrievalSnapshot:
    bm25: BM25Retriever
    vector: VectorRetriever
    cache: RetrievalCache | None
    version: str = ""


class HybridRetriever:
    def __init__(
        self,
        bm25: BM25Retriever,
        vector: VectorRetriever,
        *,
        rrf_k: int,
        cache: RetrievalCache | None = None,
    ) -> None:
        self._current = RetrievalSnapshot(bm25, vector, cache)
        self._read_view: ContextVar[RetrievalSnapshot | None] = ContextVar(
            "retrieval_read_view", default=None
        )
        self._rrf_k = rrf_k

    def replace_snapshot(
        self,
        bm25: BM25Retriever,
        vector: VectorRetriever,
        cache: RetrievalCache | None,
        version: str,
    ) -> None:
        self._current = RetrievalSnapshot(bm25, vector, cache, version)

    @property
    def knowledge_version(self) -> str:
        return (self._read_view.get() or self._current).version

    @property
    def published_version(self) -> str:
        return self._current.version

    @contextmanager
    def pin_snapshot(self) -> Iterator[None]:
        pinned = self._read_view.get()
        binding = self._read_view.set(pinned if pinned is not None else self._current)
        try:
            yield
        finally:
            self._read_view.reset(binding)

    async def retrieve(self, query: str, *, top_k: int) -> list[RetrievedChunk]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        view = self._read_view.get() or self._current
        if view.cache is not None:
            cached = await view.cache.get(query, top_k)
            if cached is not None:
                return cached
        results = await asyncio.gather(
            asyncio.to_thread(view.bm25.search, query, top_k=top_k),
            view.vector.search(query, top_k=top_k),
            return_exceptions=True,
        )
        usable: list[list[RetrievedChunk]] = []
        failed = 0
        for result in results:
            if isinstance(result, BaseException):
                failed += 1
            else:
                usable.append(result)
        if not any(usable) and failed:
            raise RetrievalError("当前知识检索无法取得结果。")
        merged = reciprocal_rank_fusion(usable, k=self._rrf_k)[:top_k]
        if view.cache is not None:
            await view.cache.set(query, top_k, merged)
        return merged

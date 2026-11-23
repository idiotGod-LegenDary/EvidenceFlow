"""Hybrid retriever fusing BM25 and dense vectors with RRF."""

from __future__ import annotations

import logging
import time
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

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalSnapshot:
    bm25: BM25Retriever
    vector: VectorRetriever
    cache: RetrievalCache | None
    version: str = ""


class HybridRetriever:
    """BM25 + dense search, fused with RRF.

    Each retriever is allowed to fail independently. If both fail, raise.
    """

    def __init__(
        self,
        bm25: BM25Retriever,
        vector: VectorRetriever,
        *,
        rrf_k: int,
        cache: RetrievalCache | None = None,
    ) -> None:
        self._snapshot = RetrievalSnapshot(bm25, vector, cache)
        self._pinned: ContextVar[RetrievalSnapshot | None] = ContextVar(
            "knowledge_snapshot", default=None
        )
        self._rrf_k = rrf_k

    def replace_snapshot(
        self,
        bm25: BM25Retriever,
        vector: VectorRetriever,
        cache: RetrievalCache | None,
        version: str,
    ) -> None:
        self._snapshot = RetrievalSnapshot(bm25, vector, cache, version)

    @property
    def knowledge_version(self) -> str:
        return (self._pinned.get() or self._snapshot).version

    @property
    def published_version(self) -> str:
        return self._snapshot.version

    @contextmanager
    def pin_snapshot(self) -> Iterator[None]:
        """Pin all rewrites, hops and tool retrievals in one graph execution."""
        token = self._pinned.set(self._pinned.get() or self._snapshot)
        try:
            yield
        finally:
            self._pinned.reset(token)

    async def retrieve(self, query: str, *, top_k: int) -> list[RetrievedChunk]:
        snapshot = self._pinned.get() or self._snapshot
        if snapshot.cache is not None:
            cached = await snapshot.cache.get(query, top_k)
            if cached is not None:
                return cached
        started = time.perf_counter()
        lexical: list[RetrievedChunk] = []
        dense: list[RetrievedChunk] = []
        lexical_error: Exception | None = None
        dense_error: Exception | None = None
        try:
            lexical = snapshot.bm25.search(query, top_k=top_k)
        except Exception as exc:  # BM25 is in-process; unexpected errors still isolated
            lexical_error = exc
            logger.exception("bm25_failed")
        try:
            dense = await snapshot.vector.search(query, top_k=top_k)
        except Exception as exc:
            dense_error = exc
            logger.exception("vector_search_failed")
        if not lexical and not dense and (lexical_error or dense_error):
            raise RetrievalError("both lexical and dense retrievers failed") from (
                dense_error or lexical_error
            )
        fused = reciprocal_rank_fusion([lexical, dense], k=self._rrf_k)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "hybrid_retrieval_completed",
            extra={
                "top_k": top_k,
                "lexical": len(lexical),
                "dense": len(dense),
                "fused": len(fused),
                "latency_ms": round(elapsed_ms, 2),
            },
        )
        trimmed = fused[:top_k]
        if snapshot.cache is not None:
            await snapshot.cache.set(query, top_k, trimmed)
        return trimmed

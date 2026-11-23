"""Build incremental knowledge snapshots, then publish them without an await.

Reuse is process-local and scoped to one fixed embedding provider. A failed build
never mutates the active index or the reusable document/vector maps.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from app.config.settings import Settings
from app.core.exceptions import InvalidRequestError, RetrievalError
from app.providers.base import EmbeddingProvider
from app.retrieval.bm25 import BM25Retriever
from app.retrieval.cache import MemoryRetrievalCache, RedisRetrievalCache
from app.retrieval.chunking import document_id_from_path
from app.retrieval.cleaning import dedupe_chunks
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.ingest import category_for_stem
from app.retrieval.unstructured_parser import list_knowledge_files, parse_path
from app.retrieval.vector import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorRetriever,
    build_records,
)
from app.schemas.retrieval import RetrievedChunk


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


class KnowledgeBuildReport(BaseModel):
    version: str
    previous_version: str = ""
    changed: bool
    documents: int
    chunks: int
    added: list[str]
    modified: list[str]
    deleted: list[str]
    parsed_documents: int
    embedded_texts: int
    reused_texts: int
    elapsed_ms: float
    vector_store: str


@dataclass(frozen=True)
class SourceDocument:
    sha256: str
    chunks: list[RetrievedChunk]


class KnowledgeRuntime:
    """Single-process publisher. Serialize writers; readers keep their old snapshot."""

    def __init__(self, settings: Settings, embedder: EmbeddingProvider, redis=None) -> None:
        self.settings = settings
        self.embedder = embedder
        self.redis = redis
        self._lock = asyncio.Lock()
        self._documents: dict[str, SourceDocument] = {}
        self._vectors: dict[str, list[float]] = {}
        self.report: KnowledgeBuildReport | None = None
        self.store: InMemoryVectorStore | QdrantVectorStore = InMemoryVectorStore()
        # Provider/model identity also prevents Redis cache collisions across workers
        # configured with different backends. No credentials are included.
        self._identity = {
            "schema": "knowledge-snapshot-v1",
            "provider": type(embedder).__name__,
            "backend": getattr(embedder, "backend", "unknown"),
            "dim": embedder.dim,
            "model": settings.embedding_model
            if settings.embedding_configured
            else settings.local_embedding_model,
            "endpoint": settings.embedding_base_url,
            "unstructured": settings.unstructured_configured,
            "strategy": settings.unstructured_strategy,
            "ocr": settings.unstructured_ocr_languages,
            "rrf_k": settings.rrf_k,
        }
        self.retriever = HybridRetriever(
            BM25Retriever(), VectorRetriever(self.store, embedder), rrf_k=settings.rrf_k
        )

    def _scan(self) -> dict[str, str]:
        root = Path(self.settings.knowledge_dir)
        if not root.is_dir():
            raise InvalidRequestError("knowledge directory does not exist")
        return {path.name: digest(path.read_bytes()) for path in list_knowledge_files(root)}

    async def reload(
        self, *, force: bool = False, allow_empty: bool = False
    ) -> KnowledgeBuildReport:
        async with self._lock:
            return await self._reload(force=force, allow_empty=allow_empty)

    async def _reload(self, *, force: bool, allow_empty: bool) -> KnowledgeBuildReport:
        started = time.perf_counter()
        manifest = await asyncio.to_thread(self._scan)
        if not manifest and self._documents and not allow_empty:
            raise InvalidRequestError("empty knowledge reload requires allow_empty=true")
        version = digest(json.dumps([self._identity, manifest], sort_keys=True))
        prior = self._documents
        added = sorted(manifest.keys() - prior.keys())
        deleted = sorted(prior.keys() - manifest.keys())
        modified = sorted(
            name for name in manifest.keys() & prior.keys() if manifest[name] != prior[name].sha256
        )
        previous_version = self.report.version if self.report else ""
        if version == previous_version and not force:
            assert self.report is not None
            return self.report.model_copy(
                update={
                    "previous_version": previous_version,
                    "changed": False,
                    "added": [],
                    "modified": [],
                    "deleted": [],
                    "parsed_documents": 0,
                    "embedded_texts": 0,
                    "reused_texts": len(self._vectors),
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                }
            )

        documents: dict[str, SourceDocument] = {}
        parsed = 0
        root = Path(self.settings.knowledge_dir)
        for name, sha256 in manifest.items():
            if not force and name in prior and prior[name].sha256 == sha256:
                documents[name] = prior[name]
                continue
            path = root / name
            chunks = await asyncio.to_thread(
                parse_path,
                path,
                document_id=document_id_from_path(path, root=root),
                title=path.stem,
                category=category_for_stem(path.stem),
                settings=self.settings,
            )
            # Do not silently publish a parser fallback that dropped a nonempty file.
            if not chunks:
                raise RetrievalError(f"knowledge file produced no chunks: {name}")
            documents[name] = SourceDocument(sha256, chunks)
            parsed += 1
        chunks = await asyncio.to_thread(
            dedupe_chunks, [chunk for doc in documents.values() for chunk in doc.chunks]
        )
        texts = {digest(chunk.content): chunk.content for chunk in chunks}
        reused = {} if force else {key: self._vectors[key] for key in texts if key in self._vectors}
        missing = [key for key in texts if key not in reused]
        vectors = dict(reused)
        if missing:
            generated = await self.embedder.embed([texts[key] for key in missing])
            if len(generated) != len(missing):
                raise RetrievalError("embedding count mismatch; snapshot not published")
            if any(
                len(row) != self.embedder.dim or not all(math.isfinite(x) for x in row)
                for row in generated
            ):
                raise RetrievalError(
                    "invalid embedding dimensions or values; snapshot not published"
                )
            vectors.update(zip(missing, generated, strict=True))
        stamped = [
            chunk.model_copy(
                deep=True,
                update={
                    "knowledge_version": version,
                    "content_sha256": digest(chunk.content),
                },
            )
            for chunk in chunks
        ]
        records = build_records(stamped, [vectors[digest(chunk.content)] for chunk in chunks])
        bm25 = await asyncio.to_thread(BM25Retriever, stamped)
        store: InMemoryVectorStore | QdrantVectorStore = InMemoryVectorStore()
        if self.settings.qdrant_url:
            # A fresh collection avoids stale tails when documents shrink or disappear.
            candidate = QdrantVectorStore(
                self.settings.qdrant_url,
                f"{self.settings.qdrant_collection}_{version}",
                self.embedder.dim,
            )
            # Unlike startup's legacy path, a reload must not conceal backend failure.
            try:
                await asyncio.to_thread(candidate.upsert, records)
                store = candidate
            except Exception as exc:
                if self.report is not None:
                    raise RetrievalError(
                        "qdrant reload failed; previous snapshot retained"
                    ) from exc
                logging.getLogger(__name__).warning("qdrant_startup_failed_using_memory")
                store.upsert(records)
        else:
            store.upsert(records)
        if await asyncio.to_thread(self._scan) != manifest:
            raise RetrievalError("knowledge files changed during build; retry reload")
        cache = (
            RedisRetrievalCache(
                self.redis, ttl_seconds=self.settings.retrieval_cache_ttl_seconds, namespace=version
            )
            if self.redis is not None
            else MemoryRetrievalCache(
                ttl_seconds=self.settings.retrieval_cache_ttl_seconds, namespace=version
            )
        )
        report = KnowledgeBuildReport(
            version=version,
            previous_version=previous_version,
            changed=version != previous_version,
            documents=len(documents),
            chunks=len(chunks),
            added=added,
            modified=modified,
            deleted=deleted,
            parsed_documents=parsed,
            embedded_texts=len(missing),
            reused_texts=len(reused),
            elapsed_ms=(time.perf_counter() - started) * 1000,
            vector_store=type(store).__name__,
        )
        # No await in the publication boundary. Existing requests own the old bundle.
        self.retriever.replace_snapshot(bm25, VectorRetriever(store, self.embedder), cache, version)
        self._documents, self._vectors, self.store, self.report = documents, vectors, store, report
        return report

    async def aclose(self) -> None:
        closer = getattr(self.embedder, "aclose", None)
        if closer is not None:
            await closer()

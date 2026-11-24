"""Regression coverage for evidence lifecycle and concurrent snapshot publication."""

import asyncio
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.core.exceptions import InvalidRequestError, RetrievalError
from app.providers.embeddings import HashEmbeddingProvider
from app.retrieval.cache import MemoryRetrievalCache, RedisRetrievalCache
from app.retrieval.knowledge import KnowledgeRuntime, digest


class RecordingEmbedder(HashEmbeddingProvider):
    def __init__(self):
        super().__init__(dim=16)
        self.batches: list[list[str]] = []
        self.fail = False

    async def embed(self, texts):
        self.batches.append(texts)
        if self.fail:
            raise RetrievalError("injected embedding outage")
        return await super().embed(texts)


def write(root: Path, name: str, body: str):
    (root / name).write_text(f"# {name}\n{body}", encoding="utf-8")


def runtime(root: Path, **overrides):
    settings = Settings(
        _env_file=None,
        app_env="testing",
        knowledge_dir=str(root),
        embedding_dim=16,
        **overrides,
    )
    return KnowledgeRuntime(settings, RecordingEmbedder())


@pytest.mark.asyncio
async def test_incremental_add_modify_delete_and_idempotent_reload(tmp_path):
    write(tmp_path, "return_policy.md", "七天内退货。")
    write(tmp_path, "shipping.md", "包邮门槛五十元。")
    write(tmp_path, "warranty.md", "保修一年。")
    kb = runtime(tmp_path)
    first = await kb.reload()
    assert (first.parsed_documents, first.embedded_texts) == (3, 3)
    original = await kb.retriever.retrieve("退货", top_k=10)
    old_return = next(hit for hit in original if hit.title == "return_policy")
    noop = await kb.reload()
    assert not noop.changed
    assert noop.version == first.version
    assert (noop.parsed_documents, noop.embedded_texts, noop.reused_texts) == (0, 0, 3)

    write(tmp_path, "return_policy.md", "十四天内退货。")
    (tmp_path / "shipping.md").unlink()
    write(tmp_path, "coupon.md", "优惠券不叠加。")
    changed = await kb.reload()
    assert changed.added == ["coupon.md"]
    assert changed.modified == ["return_policy.md"]
    assert changed.deleted == ["shipping.md"]
    assert (changed.parsed_documents, changed.embedded_texts, changed.reused_texts) == (2, 2, 1)
    hits = await kb.retriever.retrieve("退货", top_k=10)  # same warm-cache query
    assert {hit.title for hit in hits} == {"return_policy", "coupon", "warranty"}
    assert all(hit.knowledge_version == changed.version for hit in hits)
    new_return = next(hit for hit in hits if hit.title == "return_policy")
    assert new_return.chunk_id == old_return.chunk_id
    assert new_return.content == "十四天内退货。"
    assert new_return.content_sha256 == digest(new_return.content)
    assert new_return.content_sha256 != old_return.content_sha256
    full = await kb.reload(force=True)
    assert full.version == changed.version
    assert (full.parsed_documents, full.embedded_texts, full.reused_texts) == (3, 3, 0)


@pytest.mark.asyncio
async def test_document_shrink_removes_obsolete_tail_chunks(tmp_path):
    write(
        tmp_path, "return_policy.md", "\n".join(f"## 条款{i}\n第{i}个独立条款。" for i in range(6))
    )
    kb = runtime(tmp_path)
    first = await kb.reload()
    assert first.chunks == 6
    write(tmp_path, "return_policy.md", "只有一个新条款。")
    report = await kb.reload()
    hits = await kb.retriever.retrieve("条款", top_k=20)
    assert report.chunks == len(hits) == 1
    assert hits[0].content == "只有一个新条款。"


@pytest.mark.asyncio
async def test_embedding_failure_keeps_old_index_and_cache_then_recovers(tmp_path):
    write(tmp_path, "return_policy.md", "旧政策七天。")
    kb = runtime(tmp_path)
    first = await kb.reload()
    await kb.retriever.retrieve("政策", top_k=5)
    write(tmp_path, "return_policy.md", "新政策十四天。")
    kb.embedder.fail = True
    with pytest.raises(RetrievalError):
        await kb.reload()
    assert kb.report is first
    assert (await kb.retriever.retrieve("政策", top_k=5))[0].content == "旧政策七天。"
    kb.embedder.fail = False
    recovered = await kb.reload()
    assert recovered.previous_version == first.version
    assert recovered.embedded_texts == 1
    assert (await kb.retriever.retrieve("政策", top_k=5))[0].content == "新政策十四天。"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [[], [[0.0]], [[float("nan")] * 16]])
async def test_invalid_embeddings_never_publish(tmp_path, monkeypatch, invalid):
    write(tmp_path, "faq.md", "旧知识。")
    kb = runtime(tmp_path)
    first = await kb.reload()
    write(tmp_path, "faq.md", "新知识。")

    async def malformed(texts):
        return invalid

    monkeypatch.setattr(kb.embedder, "embed", malformed)
    with pytest.raises(RetrievalError):
        await kb.reload()
    assert kb.report is first


@pytest.mark.asyncio
async def test_empty_and_missing_directory_are_distinguished(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    write(root, "faq.md", "唯一的知识。")
    kb = runtime(root)
    first = await kb.reload()
    (root / "faq.md").unlink()
    with pytest.raises(InvalidRequestError, match="allow_empty"):
        await kb.reload()
    assert kb.report is first
    empty = await kb.reload(allow_empty=True)
    assert empty.chunks == 0
    assert await kb.retriever.retrieve("知识", top_k=5) == []
    root.rmdir()
    with pytest.raises(InvalidRequestError, match="directory"):
        await kb.reload(allow_empty=True)


@pytest.mark.asyncio
async def test_multihop_scope_pins_snapshot_and_restores_after_error(tmp_path):
    write(tmp_path, "faq.md", "旧政策。")
    kb = runtime(tmp_path)
    first = await kb.reload()
    with pytest.raises(RuntimeError), kb.retriever.pin_snapshot():
        before = await kb.retriever.retrieve("政策", top_k=5)
        write(tmp_path, "faq.md", "新政策。")
        current = await kb.reload()
        with kb.retriever.pin_snapshot():
            after = await kb.retriever.retrieve("另一次检索", top_k=5)
        assert before[0].knowledge_version == after[0].knowledge_version == first.version
        raise RuntimeError("end request")
    fresh = await kb.retriever.retrieve("政策", top_k=5)
    assert fresh[0].knowledge_version == current.version


@pytest.mark.asyncio
async def test_inflight_read_and_late_cache_writer_cannot_pollute_new_snapshot(
    tmp_path, monkeypatch
):
    write(tmp_path, "faq.md", "旧证据。")
    kb = runtime(tmp_path)
    old = await kb.reload()
    old_vector = kb.retriever._current.vector
    original_search = old_vector.search
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_search(query, *, top_k):
        entered.set()
        await release.wait()
        return await original_search(query, top_k=top_k)

    monkeypatch.setattr(old_vector, "search", delayed_search)
    pending = asyncio.create_task(kb.retriever.retrieve("证据", top_k=5))
    await asyncio.wait_for(entered.wait(), timeout=5)
    write(tmp_path, "faq.md", "新证据。")
    new = await kb.reload()
    release.set()
    prior_hits = await asyncio.wait_for(pending, timeout=5)
    assert {hit.knowledge_version for hit in prior_hits} == {old.version}
    fresh_hits = await kb.retriever.retrieve("证据", top_k=5)
    assert {hit.knowledge_version for hit in fresh_hits} == {new.version}
    assert fresh_hits[0].content == "新证据。"


@pytest.mark.asyncio
async def test_two_writers_serialize_and_second_update_is_noop(tmp_path, monkeypatch):
    write(tmp_path, "faq.md", "旧政策。")
    kb = runtime(tmp_path)
    await kb.reload()
    write(tmp_path, "faq.md", "新政策。")
    original = kb.embedder.embed
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(texts):
        entered.set()
        await release.wait()
        return await original(texts)

    monkeypatch.setattr(kb.embedder, "embed", blocked)
    first = asyncio.create_task(kb.reload())
    await asyncio.wait_for(entered.wait(), timeout=5)
    second = asyncio.create_task(kb.reload())
    release.set()
    a, b = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
    assert a.changed and not b.changed
    assert a.embedded_texts == 1 and b.embedded_texts == 0


@pytest.mark.asyncio
async def test_source_change_while_building_rejects_candidate(tmp_path, monkeypatch):
    write(tmp_path, "faq.md", "旧政策。")
    kb = runtime(tmp_path)
    first = await kb.reload()
    write(tmp_path, "faq.md", "待构建的政策。")
    original = kb.embedder.embed

    async def racing(texts):
        write(tmp_path, "faq.md", "构建途中又修改的政策。")
        return await original(texts)

    monkeypatch.setattr(kb.embedder, "embed", racing)
    with pytest.raises(RetrievalError, match="changed during build"):
        await kb.reload()
    assert kb.report is first


@pytest.mark.asyncio
async def test_parser_failure_or_empty_output_retains_previous_snapshot(tmp_path, monkeypatch):
    write(tmp_path, "faq.md", "旧政策。")
    kb = runtime(tmp_path)
    first = await kb.reload()
    write(tmp_path, "faq.md", "新政策。")
    monkeypatch.setattr("app.retrieval.knowledge.parse_path", lambda *args, **kwargs: [])
    with pytest.raises(RetrievalError, match="no chunks"):
        await kb.reload()
    assert kb.report is first


@pytest.mark.asyncio
async def test_redis_namespaces_isolate_versions_and_memory_cache_owns_values(tmp_path):
    write(tmp_path, "faq.md", "知识。")
    kb = runtime(tmp_path)
    await kb.reload()
    chunks = await kb.retriever.retrieve("知识", top_k=5)

    class FakeRedis:
        def __init__(self):
            self.data = {}

        async def get(self, key):
            return self.data.get(key)

        async def set(self, key, value, *, ex):
            self.data[key] = value

    redis = FakeRedis()
    a = RedisRetrievalCache(redis, ttl_seconds=60, namespace="old")
    b = RedisRetrievalCache(redis, ttl_seconds=60, namespace="new")
    await a.set("知识", 5, chunks)
    assert await a.get("知识", 5)
    assert await b.get("知识", 5) is None
    memory = MemoryRetrievalCache(ttl_seconds=60, namespace="old")
    await memory.set("知识", 5, chunks)
    chunks[0].content = "外部修改"
    cached = await memory.get("知识", 5)
    assert cached[0].content == "知识。"
    cached[0].content = "另一次修改"
    assert (await memory.get("知识", 5))[0].content == "知识。"


@pytest.mark.asyncio
async def test_qdrant_uses_distinct_collections_and_failed_reload_keeps_active_store(
    tmp_path, monkeypatch
):
    collections = []
    fail = False

    def fake_upsert(store, records):
        if fail:
            raise ConnectionError("qdrant unavailable")
        collections.append(store._collection)

    monkeypatch.setattr("app.retrieval.knowledge.QdrantVectorStore.upsert", fake_upsert)
    write(tmp_path, "faq.md", "旧证据。")
    kb = runtime(tmp_path, qdrant_url="http://test.invalid:6333")
    await kb.reload()
    write(tmp_path, "faq.md", "新证据。")
    current = await kb.reload()
    assert len(set(collections)) == 2
    active_store = kb.store
    fail = True
    write(tmp_path, "faq.md", "更新失败的证据。")
    with pytest.raises(RetrievalError, match="previous snapshot retained"):
        await kb.reload()
    assert kb.store is active_store
    assert kb.report is current

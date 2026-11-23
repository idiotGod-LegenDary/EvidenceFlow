"""Dependency-injection container and runtime composition."""

from __future__ import annotations

import logging

from app.business.orders import OrderGatewayFactory, database_order_gateway
from app.config.settings import Settings
from app.infra.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from app.infra.redis_client import connect_redis
from app.mcp.adapter import merge_mcp_tools
from app.mcp.runtime import LOCAL_CATALOG
from app.providers.base import LLMProvider
from app.providers.deepseek import DeepSeekProvider
from app.providers.embeddings import build_embedding_provider
from app.providers.fake import OfflineLLMProvider
from app.providers.reranker import CrossEncoderRerankProvider
from app.repositories.db import create_engine, create_session_factory, init_schema
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.knowledge import KnowledgeRuntime
from app.retrieval.reranker import DocumentReranker
from app.services.chat_service import ChatService
from app.services.seed import seed_demo_data
from app.tools.factory import build_tool_registry

logger = logging.getLogger(__name__)


class AppContainer:
    def __init__(
        self,
        settings: Settings,
        llm: LLMProvider,
        chat_service: ChatService,
        engine,
        session_factory,
        retriever: HybridRetriever,
        redis=None,
        vector_store=None,
        knowledge: KnowledgeRuntime | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.chat_service = chat_service
        self.engine = engine
        self.session_factory = session_factory
        self.retriever = retriever
        self.redis = redis
        self._vector_store = vector_store
        self.knowledge = knowledge

    @property
    def vector_store(self):
        return self.knowledge.store if self.knowledge is not None else self._vector_store

    async def aclose(self) -> None:
        if self.knowledge is not None:
            await self.knowledge.aclose()
        closer = getattr(self.llm, "aclose", None)
        if closer:
            await closer()
        if self.redis is not None:
            closer = getattr(self.redis, "aclose", None)
            if closer:
                await closer()
        await self.engine.dispose()


async def build_infrastructure(settings: Settings):
    """Create the database session factory and optional Redis connection."""
    engine = create_engine(settings)
    await init_schema(engine)
    session_factory = create_session_factory(engine)
    redis = await connect_redis(settings)
    return engine, session_factory, redis


async def build_retrieval(settings: Settings, redis):
    """Build the knowledge ingestion and hybrid retrieval components."""
    embedder = build_embedding_provider(settings)
    knowledge = KnowledgeRuntime(settings, embedder, redis)
    await knowledge.reload()
    reranker = DocumentReranker(CrossEncoderRerankProvider(settings))
    return knowledge, reranker


async def build_container(
    settings: Settings,
    seed: bool = False,
    *,
    gateway_factory: OrderGatewayFactory = database_order_gateway,
    llm_provider: LLMProvider | None = None,
) -> AppContainer:
    """Compose runtime services from independently testable infrastructure builders."""
    llm: LLMProvider = (
        llm_provider
        if llm_provider is not None
        else (DeepSeekProvider(settings) if settings.llm_configured else OfflineLLMProvider())
    )
    engine, session_factory, redis = await build_infrastructure(settings)
    rate_limiter = (
        RedisRateLimiter(redis, limit=settings.rate_limit_per_minute)
        if redis is not None
        else InMemoryRateLimiter(limit=settings.rate_limit_per_minute)
    )
    knowledge, reranker = await build_retrieval(settings, redis)
    retriever, store = knowledge.retriever, knowledge.store
    registry = build_tool_registry(retriever, gateway_factory)
    if settings.mcp_enabled:
        specs = list(LOCAL_CATALOG)
        if settings.mcp_remote_url:
            specs.append(
                {
                    "name": "mcp_remote_echo",
                    "description": "Remote MCP tool via HTTP (same Function Calling schema)",
                    "transport": "http",
                    "endpoint": settings.mcp_remote_url.rstrip("/") + "/mcp/call",
                }
            )
        merge_mcp_tools(registry, specs)
    chat_service = ChatService(
        settings=settings,
        llm=llm,
        retriever=retriever,
        reranker=reranker,
        session_factory=session_factory,
        rate_limiter=rate_limiter,
        registry=registry,
        gateway_factory=gateway_factory,
    )
    if seed:
        async with session_factory() as session:
            await seed_demo_data(session)
            await session.commit()
    logger.info(
        "runtime_ready database=%s cache=%s vector_store=%s llm=%s local_embedding=%s cross_encoder=%s",
        "sqlite" if settings.use_sqlite else "postgresql",
        "redis" if redis is not None else "memory",
        type(store).__name__,
        "deepseek" if settings.llm_configured else "offline",
        settings.local_embedding_configured,
        settings.cross_encoder_configured,
    )
    return AppContainer(
        settings=settings,
        llm=llm,
        chat_service=chat_service,
        engine=engine,
        session_factory=session_factory,
        retriever=retriever,
        redis=redis,
        vector_store=store,
        knowledge=knowledge,
    )

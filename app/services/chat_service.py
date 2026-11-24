from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from contextlib import aclosing

from app.business.orders import OrderGatewayFactory, database_order_gateway
from app.config.settings import Settings
from app.core.exceptions import RateLimitError
from app.core.ids import new_id
from app.core.request_scope import request_session
from app.infra.rate_limit import RateLimiter
from app.observability.metrics import REQUEST_LATENCY, REQUESTS, ROUTING, TOKEN_USAGE
from app.providers.base import LLMProvider
from app.repositories.session_repository import SessionRepository
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.reranker import DocumentReranker
from app.runtime.contracts import Completed, TurnContext
from app.runtime.coordinator import RequestCoordinator
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.case_service import CaseService
from app.tools.factory import build_tool_registry
from app.tools.registry import ToolRegistry


class ChatService:
    def __init__(
        self,
        *,
        settings: Settings,
        llm: LLMProvider,
        retriever: HybridRetriever,
        reranker: DocumentReranker,
        session_factory,
        rate_limiter: RateLimiter,
        registry: ToolRegistry | None = None,
        gateway_factory: OrderGatewayFactory = database_order_gateway,
    ) -> None:
        self._sessions, self._limiter, self._retriever = session_factory, rate_limiter, retriever
        self.registry = (
            registry if registry is not None else build_tool_registry(retriever, gateway_factory)
        )
        self.cases = CaseService(session_factory, retriever, self.registry, gateway_factory)
        self.coordinator = RequestCoordinator(settings, llm, retriever, reranker, self.registry)

    async def chat(self, request: ChatRequest, *, trace_id: str) -> ChatResponse:
        result = None
        async with aclosing(self._exchange(request, trace_id)) as events:
            async for event in events:
                if event["event"] == "answer":
                    result = ChatResponse.model_validate(event["data"])
        if result is None:
            raise RuntimeError("Request completed without a response")
        return result

    async def stream(self, request: ChatRequest, *, trace_id: str) -> AsyncGenerator[dict, None]:
        async with aclosing(self._exchange(request, trace_id)) as events:
            async for event in events:
                yield event

    async def _exchange(self, request: ChatRequest, trace_id: str) -> AsyncGenerator[dict, None]:
        if not await self._limiter.allow("user:" + request.user_id):
            raise RateLimitError()
        started = time.perf_counter()
        yield {"event": "started", "trace_id": trace_id}
        response = None
        if request.confirm_action_id:
            response = await self.cases.confirm(request, trace_id)
        else:
            session_id = request.session_id or new_id("sess_")
            async with request_session(self._sessions) as db:
                sessions = SessionRepository(db)
                await sessions.get_or_create(session_id, request.user_id)
                turn = TurnContext(
                    request, session_id, trace_id, db, await sessions.recent_messages(session_id)
                )
                with self._retriever.pin_snapshot():
                    async for update in self.coordinator.execute(turn):
                        if isinstance(update, Completed):
                            if response is not None:
                                raise RuntimeError("Duplicate completion")
                            response = update.response
                        else:
                            yield {"event": "node", "node": update.phase}
                    if response is None:
                        raise RuntimeError("Coordinator did not complete")
                    await self.cases.propose(db, request, response)
                    await sessions.append_message(session_id, "user", request.message)
                    await sessions.append_message(session_id, "assistant", response.answer)
                    await db.commit()
        response.latency.total_ms = round((time.perf_counter() - started) * 1000, 3)
        strategy = response.strategy.value if response.strategy else "clarify"
        REQUESTS.labels(
            strategy=strategy,
            intent=response.intent.value if response.intent else "unknown",
            status="refused" if response.refused else "ok",
        ).inc()
        REQUEST_LATENCY.observe(response.latency.total_ms / 1000)
        ROUTING.labels(strategy=strategy).inc()
        TOKEN_USAGE.labels(kind="total").inc(response.token_usage.total_tokens)
        for offset in range(0, len(response.answer), 48):
            yield {"event": "token", "text": response.answer[offset : offset + 48]}
        yield {"event": "answer", "data": response.model_dump(mode="json")}
        yield {"event": "done", "trace_id": trace_id}

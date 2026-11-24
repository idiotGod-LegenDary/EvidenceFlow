from __future__ import annotations

from collections.abc import AsyncIterator

from app.business.dialogue import DialogueSlots, resolve_turn
from app.config.settings import Settings
from app.core.enums import IntentLabel, QueryComplexity, RetrievalStrategy
from app.core.exceptions import GuardrailError, InvalidRequestError
from app.core.ids import new_id
from app.guardrails.injection import detect_prompt_injection
from app.providers.base import LLMProvider
from app.repositories.models import DialogueRow
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.reranker import DocumentReranker
from app.runtime.contracts import Completed, Progress, TurnContext
from app.runtime.dispatch import ReadDispatcher
from app.runtime.evidence import EvidenceEngine
from app.runtime.planning import PlanCompiler
from app.runtime.routing import OPERATIONS, route_request
from app.schemas.chat import ActionProposal, ChatResponse
from app.tools.registry import ToolRegistry


class RequestCoordinator:
    def __init__(
        self,
        settings: Settings,
        llm: LLMProvider,
        retriever: HybridRetriever,
        reranker: DocumentReranker,
        registry: ToolRegistry,
    ):
        self.planner = PlanCompiler(
            llm, registry, min(settings.max_tool_calls, settings.max_agent_steps)
        )
        self.dispatcher = ReadDispatcher(registry)
        self.evidence = EvidenceEngine(retriever, reranker, llm, settings.rerank_top_n)
        self.registry = registry

    async def execute(self, turn: TurnContext) -> AsyncIterator[Progress | Completed]:
        response = ChatResponse(answer="", session_id=turn.session_id, trace_id=turn.trace_id)
        yield Progress("request_validation")
        try:
            detect_prompt_injection(turn.request.message)
        except GuardrailError as exc:
            response.answer, response.refused, response.error_code = (
                "请求被安全策略拦截。",
                True,
                exc.error_code.value,
            )
            response.strategy = RetrievalStrategy.REFUSE
            yield Completed(response)
            return
        yield Progress("dialogue_resolution")
        saved = await turn.db.get(DialogueRow, turn.session_id)
        previous = DialogueSlots.model_validate_json(saved.slots_json) if saved else DialogueSlots()
        turn.slots, turn.query, question = resolve_turn(turn.request.message, previous)
        if saved is None:
            saved = DialogueRow(session_id=turn.session_id, slots_json="{}")
            turn.db.add(saved)
        saved.slots_json = turn.slots.model_dump_json()
        if question:
            response.answer = question
            yield Completed(response)
            return
        route = route_request(turn.query, turn.slots.selected_order)
        response.intent = route.intent
        if (
            route.kind == "lookup"
            and not route.order_id
            and route.intent
            in {IntentLabel.ORDER_INQUIRY, IntentLabel.LOGISTICS, IntentLabel.REFUND}
        ):
            response.answer = "请提供需要查询的订单号。"
            yield Completed(response)
            return
        if route.kind in {"greeting", "refuse"}:
            response.answer = (
                "你好，可以帮你核对订单、查询政策和提交售后申请。"
                if route.kind == "greeting"
                else "这个问题超出电商售后范围，请说明订单、商品或政策问题。"
            )
            response.refused = route.kind == "refuse"
            response.strategy = (
                RetrievalStrategy.REFUSE if response.refused else RetrievalStrategy.DIRECT
            )
            response.complexity = (
                QueryComplexity.OUT_OF_SCOPE if response.refused else QueryComplexity.SIMPLE
            )
            yield Completed(response)
            return
        response.strategy = (
            RetrievalStrategy.SINGLE if route.kind == "policy" else RetrievalStrategy.AGENT
        )
        response.complexity = (
            QueryComplexity.SINGLE_HOP if route.kind == "policy" else QueryComplexity.TOOL_REQUIRED
        )
        yield Progress("plan_validation")
        try:
            plan = await self.planner.compile(turn, route)
        except InvalidRequestError:
            response.answer = "未能形成符合当前订单范围的处理方案，请核对订单或补充说明。"
            response.token_usage = turn.usage
            yield Completed(response)
            return
        if plan.clarification:
            response.answer = plan.clarification
        else:
            yield Progress("read_execution")
            await self.dispatcher.execute(plan, turn, allow_handoff=route.kind == "handoff")
            if (plan.proposal and any(not result.success for result in turn.results)) or (
                turn.results and not any(result.success for result in turn.results)
            ):
                response.answer = "未找到可访问的业务记录，请核对订单号或联系人工客服。"
            elif plan.proposal:
                tool = self.registry.get(plan.proposal.tool)
                title = str(OPERATIONS[plan.proposal.tool]["name"])
                response.pending_action = ActionProposal(
                    action_id=new_id("act_"),
                    tool_name=tool.name,
                    arguments=plan.proposal.arguments,
                    risk=tool.risk,
                    summary=f"确认后将为订单 {route.order_id} 提交{title}操作。请核对订单与依据。",
                )
                response.answer = response.pending_action.summary
                if plan.proposal.tool != "cancel_order":
                    response.answer += "申请提交不代表退款到账。"
                    response.pending_action.summary = response.answer
            else:
                if route.kind == "policy":
                    yield Progress("evidence_collection")
                    turn.evidence = await self.evidence.collect(turn.query)
                yield Progress("answer_validation")
                response.answer, response.citations = await self.evidence.answer(
                    turn, policy=route.kind == "policy"
                )
        response.token_usage = turn.usage
        yield Completed(response)

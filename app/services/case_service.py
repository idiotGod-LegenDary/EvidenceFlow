from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.orders import (
    OrderGatewayFactory,
    OrderState,
    database_order_gateway,
    verify_operation,
)
from app.core.enums import RetrievalStrategy
from app.core.exceptions import AppError, InvalidRequestError, ToolExecutionError
from app.core.ids import new_id
from app.core.request_scope import request_session
from app.core.time import utcnow
from app.repositories.models import CaseEventRow, CaseRow
from app.repositories.session_repository import SessionRepository
from app.retrieval.hybrid import HybridRetriever
from app.schemas.case import CaseDetail, CaseEvent, CaseStatus, CaseSummary
from app.schemas.chat import ActionProposal, ChatRequest, ChatResponse
from app.schemas.retrieval import RetrievedChunk
from app.tools.base import ToolContext
from app.tools.permissions import assert_permitted
from app.tools.registry import ToolRegistry

LOCAL_ACTIONS = {"cancel_order", "create_return_request", "create_refund_request"}
ALLOWED_STATES = {
    "cancel_order": {"created", "paid"},
    "create_return_request": {"shipped", "delivered"},
    "create_refund_request": {"paid", "shipped", "delivered"},
}


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def order_fingerprint(value: dict) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def add_event(
    db: AsyncSession, row: CaseRow, kind: str, message: str, trace_id: str, **detail
) -> None:
    db.add(
        CaseEventRow(
            case_id=row.case_id,
            kind=kind,
            message=message,
            trace_id=trace_id,
            detail_json=encode(detail),
        )
    )
    row.updated_at = utcnow()


class CaseService:
    def __init__(
        self,
        session_factory,
        retriever: HybridRetriever,
        registry: ToolRegistry,
        gateway_factory: OrderGatewayFactory = database_order_gateway,
    ) -> None:
        self._sessions = session_factory
        self._retriever = retriever
        self._registry = registry
        self._gateway_factory = gateway_factory

    async def propose(self, db: AsyncSession, request: ChatRequest, response: ChatResponse) -> None:
        action = response.pending_action
        if action is None:
            return
        if action.tool_name not in LOCAL_ACTIONS:
            raise InvalidRequestError("该操作暂不支持工单执行。")
        tool = self._registry.get(action.tool_name)
        tool.parse(action.arguments)
        order_id = str(action.arguments.get("order_id", ""))
        order = await self._gateway_factory(db).get_visible_order(order_id, request.user_id)
        with self._retriever.pin_snapshot():
            version = self._retriever.knowledge_version
            candidates = await self._retriever.retrieve(
                {
                    "cancel_order": "取消订单 未发货 取消规则",
                    "create_return_request": "退货申请 退货条件",
                    "create_refund_request": "退款申请 退款规则",
                }[action.tool_name],
                top_k=8,
            )
            keyword = {
                "cancel_order": "取消",
                "create_return_request": "退货",
                "create_refund_request": "退款",
            }[action.tool_name]
            evidence = [item for item in candidates if keyword in item.content][:3]
        case_id = new_id("case_")[:32]
        expires = utcnow() + timedelta(minutes=15)
        action.case_id = case_id
        action.knowledge_version = version
        action.expires_at = expires
        row = CaseRow(
            case_id=case_id,
            action_id=action.action_id,
            session_id=response.session_id,
            user_id=request.user_id,
            title=request.message[:96],
            request_text=request.message,
            order_id=order_id,
            status="pending_review",
            action_json=action.model_dump_json(),
            knowledge_version=version,
            evidence_json=encode([e.model_dump(mode="json") for e in evidence]),
            order_before_json=encode(order.model_dump(mode="json")),
            expires_at=expires,
            notice="请核对订单、处置动作与政策依据。",
        )
        db.add(row)
        await db.flush()
        add_event(
            db,
            row,
            "proposed",
            "已保存处置建议与当时的订单、政策证据。",
            response.trace_id,
            knowledge_version=version,
            order_status=order.status.value,
        )
        if not evidence or not version:
            row.status = "needs_review"
            row.notice = "未取得可追溯的政策证据，请补充资料后重新审核。"
            response.pending_action = None
            response.answer = row.notice
            add_event(db, row, "review_required", row.notice, response.trace_id)
        elif order.status.value not in ALLOWED_STATES[action.tool_name]:
            row.status = "needs_review"
            row.notice = "当前订单状态不支持此操作，请调整处置方式。"
            response.pending_action = None
            response.answer = row.notice
            add_event(db, row, "review_required", row.notice, response.trace_id)
        response.case_id = case_id
        response.case_status = row.status

    async def _owned(self, db: AsyncSession, case_id: str, user_id: str) -> CaseRow:
        row = await db.scalar(
            select(CaseRow).where(CaseRow.case_id == case_id, CaseRow.user_id == user_id)
        )
        if row is None:
            raise InvalidRequestError("工单不存在。")
        return row

    async def list_cases(self, user_id: str, status: str | None = None) -> list[CaseSummary]:
        async with self._sessions() as db:
            query = select(CaseRow).where(CaseRow.user_id == user_id)
            if status:
                query = query.where(CaseRow.status == status)
            rows = (await db.scalars(query.order_by(CaseRow.created_at.desc()).limit(100))).all()
            return [self._summary(row) for row in rows]

    async def detail(self, case_id: str, user_id: str) -> CaseDetail:
        async with self._sessions() as db:
            row = await self._owned(db, case_id, user_id)
            events = (
                await db.scalars(
                    select(CaseEventRow)
                    .where(CaseEventRow.case_id == case_id)
                    .order_by(CaseEventRow.id)
                )
            ).all()
            messages = await SessionRepository(db).recent_messages(row.session_id, limit=100)
            return CaseDetail(
                **self._summary(row).model_dump(),
                request_text=row.request_text,
                action=ActionProposal.model_validate_json(row.action_json),
                knowledge_version=row.knowledge_version,
                evidence=[RetrievedChunk.model_validate(e) for e in json.loads(row.evidence_json)],
                order_before=json.loads(row.order_before_json),
                result=json.loads(row.result_json),
                verification=json.loads(row.verification_json),
                messages=messages,
                events=[
                    CaseEvent(
                        kind=e.kind,
                        message=e.message,
                        detail=json.loads(e.detail_json),
                        created_at=as_utc(e.created_at),
                    )
                    for e in events
                ],
            )

    @staticmethod
    def _summary(row: CaseRow) -> CaseSummary:
        return CaseSummary(
            case_id=row.case_id,
            session_id=row.session_id,
            title=row.title,
            order_id=row.order_id,
            status=cast(CaseStatus, row.status),
            notice=row.notice,
            attempts=row.attempts,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def dismiss(self, case_id: str, user_id: str, trace_id: str) -> CaseDetail:
        async with self._sessions() as db:
            await self._owned(db, case_id, user_id)
            changed = await db.execute(
                update(CaseRow)
                .where(
                    CaseRow.case_id == case_id,
                    CaseRow.status.in_(["pending_review", "failed", "needs_review"]),
                )
                .values(status="cancelled", notice="已撤销处置建议。", updated_at=utcnow())
            )
            if changed.rowcount:
                row = await self._owned(db, case_id, user_id)
                add_event(db, row, "cancelled", "已撤销处置建议。", trace_id)
            await db.commit()
        return await self.detail(case_id, user_id)

    async def confirm(self, request: ChatRequest, trace_id: str) -> ChatResponse:
        async with request_session(self._sessions) as db:
            if not self._gateway_factory(db).supports_local_transactions:
                raise InvalidRequestError(
                    "外部业务网关需要异步执行与结果回查，不能使用本地事务确认入口。"
                )
            row = await db.scalar(
                select(CaseRow).where(
                    CaseRow.action_id == request.confirm_action_id,
                    CaseRow.user_id == request.user_id,
                    CaseRow.session_id == request.session_id,
                )
            )
            if row is None:
                raise InvalidRequestError("待确认操作不存在。")
            # The conditional write serializes competing confirmations before any business write.
            claim = await db.execute(
                update(CaseRow)
                .where(
                    CaseRow.case_id == row.case_id, CaseRow.status.in_(["pending_review", "failed"])
                )
                .values(status="executing", attempts=CaseRow.attempts + 1, updated_at=utcnow())
                .execution_options(synchronize_session=False)
            )
            await db.refresh(row)
            if cast(CursorResult, claim).rowcount == 1:
                await self._execute(db, row, trace_id)
            elif row.status == "completed":
                add_event(db, row, "result_reused", "重复确认，返回已保存的执行结果。", trace_id)
            response = self._response(row, trace_id)
            sessions = SessionRepository(db)
            await sessions.append_message(row.session_id, "user", request.message)
            await sessions.append_message(row.session_id, "assistant", response.answer)
            await db.commit()
            return response

    async def _execute(self, db: AsyncSession, row: CaseRow, trace_id: str) -> None:
        action = ActionProposal.model_validate_json(row.action_json)
        if action.tool_name not in LOCAL_ACTIONS:
            raise InvalidRequestError("该操作暂不支持工单执行。")
        gateway = self._gateway_factory(db)
        await gateway.lock_order(row.order_id)
        baseline = await gateway.inspect(row.order_id, row.user_id)
        order = baseline.order
        current = order.model_dump(mode="json")
        reasons = []
        if as_utc(row.expires_at) <= utcnow():
            reasons.append("确认已过期")
        if self._retriever.published_version != row.knowledge_version:
            reasons.append("知识库版本已变化")
        before = json.loads(row.order_before_json)
        if order_fingerprint(current) != order_fingerprint(before):
            reasons.append("订单信息已变化")
        if order.status.value not in ALLOWED_STATES[action.tool_name]:
            reasons.append("当前订单状态不支持此操作")
        if action.tool_name == "create_return_request":
            if any(item.status == "requested" for item in baseline.returns):
                reasons.append("该订单已有待处理的退货申请")
        if reasons:
            row.status = "needs_review"
            row.notice = "；".join(reasons) + "，请重新审核。"
            add_event(
                db,
                row,
                "review_required",
                row.notice,
                trace_id,
                order_before=before,
                order_current=current,
                proposed_version=row.knowledge_version,
                current_version=self._retriever.published_version,
            )
            return
        add_event(db, row, "confirmed", "已确认，开始复核并执行。", trace_id)
        try:
            async with db.begin_nested():
                tool = self._registry.get(action.tool_name)
                assert_permitted(tool, confirmed=True)
                result = await tool.run(
                    action.arguments,
                    ToolContext(
                        user_id=row.user_id,
                        session_id=row.session_id,
                        trace_id=trace_id,
                        confirmed_action_id=row.action_id,
                        idempotency_key=row.action_id,
                    ),
                )
                if not result.success:
                    raise ToolExecutionError("操作未完成。")
                checks = await self._verify(db, row, action, result.output, baseline)
                if not all(checks.values()):
                    raise ToolExecutionError("执行结果未通过业务状态验证。")
                if self._retriever.published_version != row.knowledge_version:
                    raise ToolExecutionError("执行期间知识版本变化，请重新审核。")
            row.result_json = encode(result.output)
            row.verification_json = encode(checks)
            row.status = "completed"
            row.notice = "操作已完成，业务状态验证通过。"
            add_event(db, row, "completed", row.notice, trace_id, checks=checks)
        except AppError as exc:
            row.status = "failed"
            row.notice = exc.message
            row.result_json = "{}"
            row.verification_json = "{}"
            add_event(
                db,
                row,
                "failed",
                "执行未完成，本次业务写入已回滚。",
                trace_id,
                error_code=exc.error_code.value,
            )

    async def _verify(
        self,
        db: AsyncSession,
        row: CaseRow,
        action: ActionProposal,
        result: dict,
        baseline: OrderState,
    ) -> dict[str, bool]:
        await db.flush()
        after = await self._gateway_factory(db).inspect(row.order_id, row.user_id)
        return verify_operation(action.tool_name, action.arguments, result, baseline, after)

    @staticmethod
    def _response(row: CaseRow, trace_id: str) -> ChatResponse:
        action = ActionProposal.model_validate_json(row.action_json)
        if row.status == "completed":
            label = {
                "cancel_order": "订单已取消",
                "create_refund_request": "退款申请已提交，尚未到账",
                "create_return_request": "退货申请已提交，尚未退款",
            }[action.tool_name]
            answer = f"{row.order_id}：{label}。业务状态验证通过。"
        else:
            answer = row.notice
        return ChatResponse(
            answer=answer,
            session_id=row.session_id,
            trace_id=trace_id,
            strategy=RetrievalStrategy.AGENT,
            case_id=row.case_id,
            case_status=row.status,
            pending_action=action if row.status == "failed" else None,
        )

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.config.settings import Settings
from app.core.exceptions import InvalidRequestError
from app.core.request_scope import get_db_session
from app.core.time import utcnow
from app.repositories.models import AuditLogRow, CaseRow, OrderRow, RefundRow, ReturnRow
from app.schemas.chat import ChatRequest
from app.services.container import build_container


@pytest.fixture
async def runtime(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "cancel.md").write_text(
        "# 取消与售后\n未发货的订单可申请取消。已发货可申请退货。退款申请不代表已到账。",
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'cases.db'}",
        knowledge_dir=str(knowledge),
        local_embedding_enabled=False,
        cross_encoder_enabled=False,
        unstructured_enabled=False,
        rate_limit_per_minute=1000,
    )
    container = await build_container(settings, seed=True)
    yield container, settings, knowledge
    await container.aclose()


async def propose(container, message="取消订单 ORD10003"):
    return await container.chat_service.chat(
        ChatRequest(message=message, user_id="u_demo"), trace_id="proposal"
    )


async def confirm(container, proposal, **overrides):
    request = ChatRequest(
        message="确认执行",
        user_id="u_demo",
        session_id=proposal.session_id,
        confirm_action_id=proposal.pending_action.action_id,
    )
    return await container.chat_service.chat(
        request.model_copy(update=overrides), trace_id="confirm"
    )


async def state(container):
    async with container.session_factory() as db:
        order = await db.get(OrderRow, "ORD10003")
        audit = await db.scalar(
            select(func.count())
            .select_from(AuditLogRow)
            .where(AuditLogRow.action == "cancel_order")
        )
        return order.status, audit


@pytest.mark.asyncio
async def test_restart_and_lost_response_reuse(runtime):
    container, settings, _ = runtime
    proposal = await propose(container)
    detail = await container.chat_service.cases.detail(proposal.case_id, "u_demo")
    assert detail.status == "pending_review" and detail.evidence[0].content
    assert detail.evidence[0].knowledge_version == detail.knowledge_version
    assert await state(container) == ("paid", 0)

    restarted = await build_container(settings)
    try:
        first = await confirm(restarted, proposal)
        again = await confirm(container, proposal)
        assert first.case_status == again.case_status == "completed"
        assert first.answer == again.answer
        assert await state(container) == ("cancelled", 1)
        detail = await restarted.chat_service.cases.detail(proposal.case_id, "u_demo")
        assert all(detail.verification.values()) and detail.attempts == 1
        assert detail.events[-1].kind == "result_reused"
    finally:
        await restarted.aclose()


@pytest.mark.asyncio
async def test_concurrent_confirmations_use_one_business_write(runtime):
    container, _, _ = runtime
    proposal = await propose(container)
    replies = await asyncio.gather(*(confirm(container, proposal) for _ in range(4)))
    assert all(r.case_status == "completed" for r in replies)
    assert await state(container) == ("cancelled", 1)


@pytest.mark.asyncio
async def test_confirm_is_bound_to_owner_and_session(runtime):
    container, _, _ = runtime
    proposal = await propose(container)
    for overrides in ({"user_id": "another"}, {"session_id": "another"}):
        with pytest.raises(InvalidRequestError):
            await confirm(container, proposal, **overrides)
    with pytest.raises(InvalidRequestError):
        await container.chat_service.cases.detail(proposal.case_id, "another")
    assert await container.chat_service.cases.list_cases("another") == []
    with pytest.raises(InvalidRequestError):
        await container.chat_service.chat(
            ChatRequest(message="你好", user_id="another", session_id=proposal.session_id),
            trace_id="t",
        )
    assert await state(container) == ("paid", 0)


@pytest.mark.asyncio
async def test_order_change_invalidates_proposal(runtime):
    container, _, _ = runtime
    proposal = await propose(container)
    async with container.session_factory() as db:
        await db.execute(
            update(OrderRow).where(OrderRow.order_id == "ORD10003").values(status="shipped")
        )
        await db.commit()
    result = await confirm(container, proposal)
    assert result.case_status == "needs_review" and "订单信息已变化" in result.answer
    assert await state(container) == ("shipped", 0)
    detail = await container.chat_service.cases.detail(proposal.case_id, "u_demo")
    assert detail.order_before["status"] == "paid"
    assert detail.events[-1].detail["order_current"]["status"] == "shipped"


@pytest.mark.asyncio
async def test_policy_change_keeps_historical_body_and_blocks_old_action(runtime):
    container, _, knowledge = runtime
    proposal = await propose(container)
    old = await container.chat_service.cases.detail(proposal.case_id, "u_demo")
    (knowledge / "cancel.md").write_text("# 取消\n取消订单需要人工处理。", encoding="utf-8")
    await container.knowledge.reload()
    reply = await confirm(container, proposal)
    assert reply.case_status == "needs_review" and "知识库版本已变化" in reply.answer
    saved = await container.chat_service.cases.detail(proposal.case_id, "u_demo")
    assert saved.evidence == old.evidence
    assert saved.knowledge_version != container.retriever.published_version
    assert await state(container) == ("paid", 0)


@pytest.mark.asyncio
async def test_expired_and_dismissed_actions_cannot_execute(runtime):
    container, _, _ = runtime
    proposal = await propose(container)
    async with container.session_factory() as db:
        await db.execute(
            update(CaseRow)
            .where(CaseRow.case_id == proposal.case_id)
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await db.commit()
    assert (await confirm(container, proposal)).case_status == "needs_review"
    another = await propose(container)
    await container.chat_service.cases.dismiss(another.case_id, "u_demo", "dismiss")
    assert (await confirm(container, another)).case_status == "cancelled"
    assert await state(container) == ("paid", 0)


@pytest.mark.asyncio
async def test_tool_failure_rolls_back_business_writes_and_can_retry(runtime, monkeypatch):
    container, _, _ = runtime
    proposal = await propose(container)
    tool = container.chat_service.registry.get("cancel_order")
    original = tool.execute

    async def fail_after_write(parsed, context):
        await original(parsed, context)
        raise RuntimeError("interrupted")

    monkeypatch.setattr(tool, "execute", fail_after_write)
    failed = await confirm(container, proposal)
    assert failed.case_status == "failed"
    assert await state(container) == ("paid", 0)
    monkeypatch.setattr(tool, "execute", original)
    assert (await confirm(container, proposal)).case_status == "completed"
    assert await state(container) == ("cancelled", 1)


@pytest.mark.asyncio
async def test_verifier_rejects_claimed_success_and_rolls_back(runtime, monkeypatch):
    container, _, _ = runtime
    proposal = await propose(container)
    tool = container.chat_service.registry.get("cancel_order")
    original = tool.execute

    async def wrong_result(parsed, context):
        result = await original(parsed, context)
        get_db_session().add(
            RefundRow(
                refund_id="unexpected", order_id="ORD10003", status="requested", amount_cents=7900
            )
        )
        return result

    monkeypatch.setattr(tool, "execute", wrong_result)
    result = await confirm(container, proposal)
    assert result.case_status == "failed" and "验证" in result.answer
    assert await state(container) == ("paid", 0)
    async with container.session_factory() as db:
        assert await db.get(RefundRow, "unexpected") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message, table, key",
    [
        ("我要退款 ORD10003", RefundRow, "refund_id"),
        ("我要退货 ORD10001", ReturnRow, "return_id"),
    ],
)
async def test_refund_and_return_create_one_verified_record(runtime, message, table, key):
    container, _, _ = runtime
    proposal = await propose(container, message)
    assert (await confirm(container, proposal)).case_status == "completed"
    assert (await confirm(container, proposal)).case_status == "completed"
    detail = await container.chat_service.cases.detail(proposal.case_id, "u_demo")
    assert all(detail.verification.values())
    async with container.session_factory() as db:
        assert await db.get(table, detail.result[key]) is not None
        assert await db.scalar(select(func.count()).select_from(table)) == 1


@pytest.mark.asyncio
async def test_unexpected_process_failure_leaves_pending_action_retryable(runtime, monkeypatch):
    container, _, _ = runtime
    proposal = await propose(container)
    service = container.chat_service.cases
    original = service._verify

    async def crash(*args):
        raise RuntimeError("process interrupted before commit")

    monkeypatch.setattr(service, "_verify", crash)
    with pytest.raises(RuntimeError):
        await confirm(container, proposal)
    assert await state(container) == ("paid", 0)
    detail = await service.detail(proposal.case_id, "u_demo")
    assert detail.status == "pending_review" and detail.attempts == 0
    monkeypatch.setattr(service, "_verify", original)
    assert (await confirm(container, proposal)).case_status == "completed"


@pytest.mark.asyncio
async def test_unrelated_retrieval_does_not_authorize_action(runtime, monkeypatch):
    from app.schemas.retrieval import RetrievedChunk

    container, _, _ = runtime

    async def unrelated(*args, **kwargs):
        return [
            RetrievedChunk(
                chunk_id="wrong",
                document_id="wrong",
                title="保修",
                content="耳机保修一年。",
                score=1,
            )
        ]

    monkeypatch.setattr(container.retriever, "retrieve", unrelated)
    proposal = await propose(container)
    assert proposal.case_status == "needs_review" and proposal.pending_action is None
    detail = await container.chat_service.cases.detail(proposal.case_id, "u_demo")
    assert detail.evidence == []
    assert await state(container) == ("paid", 0)

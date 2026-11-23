import pytest

from app.business.orders import DatabaseOrderGateway
from app.config.settings import Settings
from app.core.exceptions import InvalidRequestError
from app.repositories.order_repository import OrderRepository
from app.schemas.chat import ChatRequest
from app.services.container import build_container


@pytest.mark.asyncio
@pytest.mark.parametrize("local_transactions", [True, False])
async def test_same_gateway_factory_is_used_for_reads_proposals_and_execution(
    tmp_path, local_transactions
):
    calls = []

    class RecordingGateway(DatabaseOrderGateway):
        supports_local_transactions = local_transactions

        async def get_visible_order(self, order_id, user_id):
            calls.append("read")
            return await super().get_visible_order(order_id, user_id)

        async def inspect(self, order_id, user_id):
            calls.append("inspect")
            return await super().inspect(order_id, user_id)

        async def cancel_order(self, order_id, *, trace_id):
            calls.append("write")
            return await super().cancel_order(order_id, trace_id=trace_id)

    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "cancel.md").write_text("# 取消\n未发货订单可申请取消。", encoding="utf-8")
    container = await build_container(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}",
            knowledge_dir=str(knowledge),
            local_embedding_enabled=False,
            cross_encoder_enabled=False,
            unstructured_enabled=False,
        ),
        seed=True,
        gateway_factory=lambda db: RecordingGateway(OrderRepository(db)),
    )
    try:
        proposal = await container.chat_service.chat(
            ChatRequest(message="取消订单 ORD10003", user_id="u_demo"), trace_id="gateway"
        )
        assert proposal.pending_action is not None and "read" in calls
        request = ChatRequest(
            message="确认执行",
            user_id="u_demo",
            session_id=proposal.session_id,
            confirm_action_id=proposal.pending_action.action_id,
        )
        if local_transactions:
            reply = await container.chat_service.chat(request, trace_id="confirm")
            assert reply.case_status == "completed" and calls.count("write") == 1
            assert calls.count("inspect") == 2
        else:
            with pytest.raises(InvalidRequestError, match="外部"):
                await container.chat_service.chat(request, trace_id="confirm")
            assert "write" not in calls and "inspect" not in calls
            detail = await container.chat_service.cases.detail(proposal.case_id, "u_demo")
            assert detail.status == "pending_review" and detail.attempts == 0
    finally:
        await container.aclose()

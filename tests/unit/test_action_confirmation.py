import json

import pytest

from app.agents.nodes.planner import Planner, planner_node
from app.core.enums import ToolRisk
from app.providers.fake import FakeLLMProvider, OfflineLLMProvider
from app.schemas.chat import ActionProposal, ChatMessage
from app.tools.aftersale_tools import CancelOrderTool
from app.tools.order_tools import GetOrderTool
from app.tools.registry import ToolRegistry


def _action() -> ActionProposal:
    return ActionProposal(
        action_id="act_test",
        tool_name="cancel_order",
        arguments={"order_id": "ORD10003"},
        risk=ToolRisk.DESTRUCTIVE,
        summary="确认后将取消订单 ORD10003。",
    )


@pytest.mark.asyncio
async def test_cancellation_needs_explicit_request_and_confirmation() -> None:
    registry = ToolRegistry()
    registry.register(GetOrderTool())
    registry.register(CancelOrderTool())
    planner = Planner(OfflineLLMProvider(), registry)

    proposal = await planner_node({"user_query": "取消订单 ORD10003"}, planner)
    assert proposal["pending_action"].tool_name == "cancel_order"
    assert "cancel_order" not in proposal["pending_action"].summary

    question = await planner_node({"user_query": "取消订单有什么规则？"}, planner)
    assert question["pending_action"] is None

    confirmed = await planner_node(
        {"user_query": "确认执行", "confirmed_action": _action()}, planner
    )
    assert len(confirmed["plan"].steps) == 1
    assert confirmed["plan"].steps[0].arguments == {"order_id": "ORD10003"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order_id,query,history,allowed",
    [
        ("<待用户提供>", "帮我取消订单", [], False),
        ("ORD10003", "帮我取消订单", [], False),
        ("", "帮我取消订单", [], False),
        ("ORD10003", "取消订单 ORD100030", [], False),
        ("ORD10003", "取消订单 ORD10003", [], True),
        (
            "ORD10003",
            "帮我取消订单",
            [ChatMessage(role="assistant", content="订单 ORD10003")],
            False,
        ),
        (
            "ORD10003",
            "帮我取消订单",
            [ChatMessage(role="user", content="我的订单是 ORD10003")],
            True,
        ),
    ],
)
async def test_proposed_order_requires_valid_user_reference(order_id, query, history, allowed):
    registry = ToolRegistry()
    registry.register(CancelOrderTool())
    payload = {
        "steps": [
            {
                "step_id": 1,
                "goal": "取消",
                "tool_name": "cancel_order",
                "arguments": {"order_id": order_id},
            }
        ]
    }
    planner = Planner(FakeLLMProvider(canned={"": json.dumps(payload)}), registry)
    result = await planner_node({"user_query": query, "messages": history}, planner)
    if allowed:
        assert result["pending_action"].arguments["order_id"] == order_id
    else:
        assert not result["plan"].steps and not result.get("pending_action")
        assert result["extra"]["missing_order_id"]

import copy
import hashlib
import json

import pytest

from app.business.dialogue import DialogueSlots, resolve_turn
from app.config.settings import Settings
from app.observability.trace import CallBudget
from eval.p0_compare import BASELINE, digest
from eval.p0_dataset import PROTOCOL, SEALED, DialogueTask, Turn, load_tasks
from eval.p0_runner import grade_turn, trial


def test_negated_order_is_not_a_selection():
    slots = DialogueSlots(
        phase="awaiting_selection", operation="cancel", candidates=["ORD10003", "ORD10001"]
    )
    updated, _, clarification = resolve_turn("不要选 ORD10001，另一单还需确认", slots)
    assert clarification and updated.selected_order is None
    updated, resolved, clarification = resolve_turn("ORD10003", updated)
    assert not clarification and "取消订单 ORD10003" == resolved


def test_consultation_resets_pending_operation():
    previous = DialogueSlots(phase="awaiting_order", operation="refund", reason="重复付款")
    slots, _, clarification = resolve_turn("先说退款政策，别办理", previous)
    assert clarification is None and slots.operation is None
    _, resolved, _ = resolve_turn("ORD10003", slots)
    assert "我要退款" not in resolved


def test_explicit_missing_number_does_not_reuse_old_order():
    previous = DialogueSlots(selected_order="ORD10003")
    slots, _, clarification = resolve_turn("我要退款，但订单号还没找到", previous)
    assert clarification and slots.selected_order is None and slots.phase == "awaiting_order"


def test_negative_request_does_not_create_or_resume_an_application():
    previous = DialogueSlots(phase="awaiting_order", operation="cancel")
    slots, _, response = resolve_turn("不要取消 ORD10003", previous)
    assert response and slots.operation is None and slots.phase == "idle"
    _, query, _ = resolve_turn("ORD10003", slots)
    assert "取消" not in query


def test_policy_acknowledgement_can_be_followed_by_explicit_application():
    previous = DialogueSlots(selected_order="ORD10003")
    slots, query, response = resolve_turn("规则已经了解；现在帮我取消这笔", previous)
    assert not response and query == "取消订单 ORD10003"
    _, _, response = resolve_turn("规则已经了解；别帮我取消这笔", slots)
    assert response


@pytest.mark.asyncio
async def test_order_specific_policy_plan_always_includes_evidence_lookup():
    from app.agents.nodes.planner import Planner, planner_node
    from app.providers.fake import FakeLLMProvider
    from app.tools.aftersale_tools import CancelOrderTool
    from app.tools.knowledge_tools import SearchKnowledgeTool
    from app.tools.order_tools import GetOrderTool
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(CancelOrderTool())
    registry.register(GetOrderTool())
    registry.register(SearchKnowledgeTool(None))
    payload = {
        "steps": [
            {
                "step_id": 1,
                "goal": "查订单",
                "tool_name": "get_order",
                "arguments": {"order_id": "ORD10003"},
            }
        ]
    }
    result = await planner_node(
        {"user_query": "订单 ORD10003 的取消条件是什么？"},
        Planner(FakeLLMProvider(canned={"": json.dumps(payload)}), registry),
    )
    assert result["pending_action"] is None
    assert result["plan"].steps[0].tool_name == "search_knowledge"


def test_frozen_assets_are_unchanged_and_new_queries_do_not_overlap_old_dataset():
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    assert digest(SEALED) == protocol["dataset_sha256"]
    assert digest(BASELINE) == protocol["baseline_sha256"]
    tasks = load_tasks()
    assert len(tasks) == 32 and len({t.family for t in tasks}) == 8
    from eval.service_dataset import load_tasks as legacy

    old_queries = {t.query for t in legacy()}
    assert all(turn.query not in old_queries for task in tasks for turn in task.turns)


@pytest.mark.asyncio
async def test_oracle_accepts_valid_policy_citations_and_detects_wrong_confirmed_write():
    task = DialogueTask(
        id="development_policy",
        family="development",
        turns=[Turn(query="请介绍退货政策。", goal="policy", source="return_policy")],
    )
    result = await trial(task, Settings(), CallBudget(1))
    assert result["success"], result["turns"][0]["checks"]
    row = result["turns"][0]
    damaged = copy.deepcopy(row["after"])
    damaged["orders"]["ORD10003"]["status"] = "cancelled"
    fake = {"action": {"tool_name": "cancel_order", "arguments": {"order_id": "ORD10003"}}}
    graded = grade_turn(
        task.turns[0],
        row["response"],
        fake,
        row,
        task,
        row["before"],
        damaged,
        None,
        "completed",
        {},
    )
    assert graded["wrong_proposal"] and graded["wrong_execution"] and not graded["success"]
    assert not graded["checks"]["citations_resolve"]


@pytest.mark.asyncio
async def test_evaluator_confirms_wrong_proposal_and_observes_actual_wrong_execution(monkeypatch):
    from app.services.chat_service import ChatService

    original = ChatService.chat
    query = "请介绍取消订单的规定。"

    async def wrong_proposal(self, request, *, trace_id):
        if request.message == query:
            request = request.model_copy(update={"message": "取消订单 ORD10003"})
        return await original(self, request, trace_id=trace_id)

    monkeypatch.setattr(ChatService, "chat", wrong_proposal)
    task = DialogueTask(
        id="development_wrong_action",
        family="development",
        turns=[Turn(query=query, goal="policy", source="cancel_policy")],
    )
    result = await trial(task, Settings(), CallBudget(1))
    assert result["wrong_proposal"] and result["wrong_execution"] and not result["success"]
    turn = result["turns"][0]
    assert turn["before"] == turn["before_confirm"]
    assert turn["after"]["orders"]["ORD10003"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_uncited_model_claim_is_replaced_with_actual_evidence():
    from app.agents.nodes.generate import generate_node
    from app.providers.fake import FakeLLMProvider
    from app.schemas.retrieval import RetrievedChunk

    text = "普通商品签收后可提交退货申请。"
    chunk = RetrievedChunk(
        chunk_id="verified",
        document_id="returns",
        title="退货规定",
        content=text,
        score=1,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        knowledge_version="frozen",
    )
    provider = FakeLLMProvider(
        canned={
            "": json.dumps(
                {"answer": "定制商品肯定能退，保证退款到账。", "citation_chunk_ids": ["invented"]}
            )
        }
    )
    result = await generate_node(
        {"user_query": "退货规则是什么？", "retrieved_chunks": [chunk]}, provider
    )
    assert "肯定能退" not in result["final_answer"] and text in result["final_answer"]
    assert result["citations"][0].chunk_id == "verified"

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.business.dialogue import DialogueSlots, resolve_turn
from app.core.enums import IntentLabel, ToolRisk
from app.core.exceptions import InvalidRequestError, ToolPermissionError
from app.providers.fake import FakeLLMProvider, OfflineLLMProvider
from app.runtime.contracts import (
    ExecutionPlan,
    ReadCall,
    RequestRoute,
    TurnContext,
)
from app.runtime.dispatch import ReadDispatcher
from app.runtime.evidence import EvidenceEngine
from app.runtime.planning import PlanCompiler, bounded_history, default_plan
from app.runtime.routing import route_request
from app.schemas.agent import ToolResult
from app.schemas.chat import ChatMessage, ChatRequest
from app.schemas.retrieval import RetrievedChunk
from app.tools.base import Tool
from app.tools.factory import build_tool_registry
from app.tools.registry import ToolRegistry


def context(query="取消订单 ORD10003"):
    request = ChatRequest(message=query, user_id="u_demo")
    turn = TurnContext(request, "session", "trace", None, [])
    turn.slots, turn.query, _ = resolve_turn(query, DialogueSlots())
    return turn


@pytest.mark.parametrize(
    "query,kind",
    [
        ("你好", "greeting"),
        ("你好，取消订单 ORD10003", "lookup"),
        ("取消订单 ORD10003", "operation"),
        ("订单 ORD10003 能不能退货？", "policy"),
        ("申请退款成功代表到账吗？", "policy"),
        ("预测股市", "refuse"),
        ("转人工", "handoff"),
        ("物流 ORD10003", "lookup"),
    ],
)
def test_routes_are_explicit_and_bounded(query, kind):
    assert route_request(query).kind == kind


def test_plan_cannot_mutate_a_required_proposal():
    registry = build_tool_registry(None)
    turn = context()
    route = route_request(turn.query, turn.slots.selected_order)
    compiler = PlanCompiler(OfflineLLMProvider(), registry, 6)
    required = default_plan(turn, route)
    forged = required.model_copy(deep=True)
    forged.proposal.arguments["order_id"] = "ORD10001"
    with pytest.raises(InvalidRequestError):
        compiler.validate(forged, required, route)


@pytest.mark.parametrize(
    "call",
    [
        ReadCall(tool="cancel_order", arguments={"order_id": "ORD10003"}),
        ReadCall(tool="get_order", arguments={"order_id": "ORD10001"}),
        ReadCall(tool="not_registered", arguments={}),
    ],
)
def test_reads_cannot_smuggle_writes_or_unauthorized_orders(call):
    compiler = PlanCompiler(OfflineLLMProvider(), build_tool_registry(None), 6)
    route = RequestRoute("lookup", IntentLabel.ORDER_INQUIRY, "ORD10003")
    required = ExecutionPlan(reads=[ReadCall(tool="get_order", arguments={"order_id": "ORD10003"})])
    with pytest.raises(InvalidRequestError):
        compiler.validate(ExecutionPlan(reads=[call]), required, route)


def test_missing_fact_lookup_and_tool_budget_are_rejected():
    compiler = PlanCompiler(OfflineLLMProvider(), build_tool_registry(None), 1)
    route = RequestRoute("lookup", IntentLabel.ORDER_INQUIRY, "ORD10003")
    with pytest.raises(InvalidRequestError):
        compiler.validate(ExecutionPlan(), ExecutionPlan(), route)
    call = ReadCall(tool="get_order", arguments={"order_id": "ORD10003"})
    with pytest.raises(InvalidRequestError):
        compiler.validate(ExecutionPlan(reads=[call, call]), ExecutionPlan(), route)


def test_history_budget_is_utf8_based_and_keeps_recent_messages():
    turn = context()
    turn.history = [
        ChatMessage(role="user", content="旧" * 100),
        ChatMessage(role="user", content="新问题"),
    ]
    assert bounded_history(turn, max_bytes=12) == [{"role": "user", "text": "新问题"}]


class EmptyInput(BaseModel):
    pass


class CountingTool(Tool):
    name = "count"
    description = "read counter"
    input_model = EmptyInput
    count = 0

    async def execute(self, parsed, context):
        self.count += 1
        return ToolResult(name=self.name, success=True, output={"count": self.count})


@pytest.mark.asyncio
async def test_dispatcher_memoizes_reads_and_blocks_writes_before_invocation():
    registry = ToolRegistry()
    tool = CountingTool()
    registry.register(tool)
    turn = context()
    call = ReadCall(tool="count")
    await ReadDispatcher(registry).execute(ExecutionPlan(reads=[call, call]), turn)
    assert tool.count == 1 and len(turn.results) == 2
    tool.risk = ToolRisk.DESTRUCTIVE
    with pytest.raises(ToolPermissionError):
        await ReadDispatcher(registry).execute(ExecutionPlan(reads=[call]), turn)
    assert tool.count == 1


def test_registry_rejects_duplicate_names():
    registry = ToolRegistry()
    registry.register(CountingTool())
    with pytest.raises(ValueError):
        registry.register(CountingTool())


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [[], ["invented"], ["verified", "invented"]])
async def test_policy_claim_without_complete_valid_citations_becomes_an_excerpt(ids):
    turn = context("退货规则是什么？")
    turn.evidence = [
        RetrievedChunk(
            chunk_id="verified",
            document_id="returns",
            title="退货",
            content="定制商品不能无理由退货。",
            score=1,
        )
    ]
    llm = FakeLLMProvider(canned={"": json.dumps({"text": "保证退款到账。", "evidence_ids": ids})})
    answer, citations = await EvidenceEngine(None, None, llm, 3).answer(turn, policy=True)
    assert "保证" not in answer and "定制" in answer
    assert citations[0].chunk_id == "verified"


@pytest.mark.asyncio
async def test_needs_review_is_exposed_and_business_facts_are_not_model_invented():
    turn = context("退货政策")
    turn.evidence = [
        RetrievedChunk(chunk_id="c", document_id="d", title="政策", content="条件待核实", score=1)
    ]
    llm = FakeLLMProvider(
        canned={
            "": json.dumps({"text": "证据缺少条件。", "evidence_ids": ["c"], "needs_review": True})
        }
    )
    answer, _ = await EvidenceEngine(None, None, llm, 3).answer(turn, policy=True)
    assert "人工核对" in answer
    turn.results = [
        ToolResult(
            name="get_order",
            success=True,
            output={"order_id": "ORD10003", "status": "paid", "total_cents": 7900},
        )
    ]
    before = len(llm.calls)
    answer, _ = await EvidenceEngine(None, None, llm, 3).answer(turn, policy=False)
    assert "7900" in answer and len(llm.calls) == before


def test_core_loads_and_executes_when_legacy_framework_imports_are_forbidden():
    script = """
import asyncio, importlib.abc, sys, tempfile
from pathlib import Path
class DenyLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("langgraph", "langchain", "app.agents", "app.skills", "app.memory")):
            raise ImportError("legacy import: " + fullname)
sys.meta_path.insert(0,DenyLegacy())
from app.config.settings import Settings
from app.providers.fake import OfflineLLMProvider
from app.services.container import build_container
from app.schemas.chat import ChatRequest
async def run():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp); kb=root/"knowledge"; kb.mkdir()
        (kb/"cancel.md").write_text("# 取消政策\\n未发货订单可申请取消。",encoding="utf-8")
        container=await build_container(Settings(_env_file=None,app_env="testing",database_url=f"sqlite+aiosqlite:///{(root/'db.sqlite').as_posix()}",knowledge_dir=str(kb),local_embedding_enabled=False,cross_encoder_enabled=False,mcp_enabled=False),seed=True,llm_provider=OfflineLLMProvider())
        try:
            reply=await container.chat_service.chat(ChatRequest(message="取消订单 ORD10003",user_id="u_demo"),trace_id="independent")
            assert reply.pending_action is not None
        finally:
            await container.aclose()
asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, result.stderr
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert not any("langgraph" in d or "langchain" in d for d in project["project"]["dependencies"])


def test_fusion_does_not_double_count_duplicates_from_one_backend():
    from app.retrieval.fusion import reciprocal_rank_fusion

    item = RetrievedChunk(chunk_id="a", document_id="d", title="t", content="c", score=1)
    assert (
        reciprocal_rank_fusion([[item, item]])[0].score == reciprocal_rank_fusion([[item]])[0].score
    )

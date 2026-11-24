import json

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.config.settings import Settings
from app.observability.trace import CallBudget
from app.providers.deepseek import DeepSeekProvider
from app.providers.fake import FakeLLMProvider
from app.repositories.models import CaseRow, SessionRow
from app.schemas.agent import ToolResult
from app.schemas.chat import ChatRequest
from app.services.container import build_container
from eval.service_benchmark import run_task
from eval.service_dataset import load_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("read_success", [True, False])
async def test_proposal_executes_prerequisite_reads_and_stops_on_failure(
    tmp_path, monkeypatch, read_success
):
    kb = tmp_path / "knowledge"
    kb.mkdir()
    (kb / "cancel.md").write_text("# 取消政策\n未发货订单可申请取消。", encoding="utf-8")
    plan = {
        "reads": [{"tool": "get_order", "arguments": {"order_id": "ORD10003"}}],
        "proposal": {"tool": "cancel_order", "arguments": {"order_id": "ORD10003"}},
    }
    container = await build_container(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}", knowledge_dir=str(kb)),
        seed=True,
        llm_provider=FakeLLMProvider(canned={"": json.dumps(plan)}),
    )
    calls = []

    async def read(parsed, context):
        calls.append(parsed.order_id)
        return ToolResult(name="get_order", success=read_success, output={})

    monkeypatch.setattr(container.chat_service.registry.get("get_order"), "execute", read)
    try:
        reply = await container.chat_service.chat(
            ChatRequest(message="取消订单 ORD10003", user_id="u_demo"),
            trace_id="read-before-propose",
        )
        assert calls == ["ORD10003"]
        assert (reply.pending_action is not None) is read_success
        assert (reply.case_id is not None) is read_success
    finally:
        await container.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [0, 1])
async def test_real_transport_schema_budget_and_whole_request_usage(monkeypatch, budget):
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        task = json.loads(payload["messages"][-1]["content"])
        return httpx.Response(
            200,
            json={
                "model": "served-v2",
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "reads": [],
                                    "proposal": task["required_proposal"],
                                    "clarification": None,
                                }
                            )
                        }
                    }
                ],
            },
        )

    settings = Settings(deepseek_api_key=SecretStr("not-real"))
    async with httpx.AsyncClient(
        base_url="https://unit.invalid", transport=httpx.MockTransport(respond)
    ) as client:
        provider = DeepSeekProvider(settings, client)
        monkeypatch.setattr("app.services.container.DeepSeekProvider", lambda settings: provider)
        result = await run_task(load_tasks()[0], settings, CallBudget(budget))
    assert len(requests) == budget
    assert not result["unsafe_write"]
    if budget:
        assert result["success"] and result["case_status"] == "completed"
        assert result["response"]["token_usage"]["total_tokens"] == 17
    else:
        assert result["error_type"] == "EvaluationBudgetExceeded"
        assert result["before"] == result["after"]


@pytest.mark.asyncio
async def test_untrusted_model_proposal_does_not_persist_or_write(monkeypatch):
    def respond(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "reads": [],
                                    "proposal": {
                                        "tool": "cancel_order",
                                        "arguments": {"order_id": "ORD10001"},
                                    },
                                }
                            )
                        }
                    }
                ]
            },
        )

    settings = Settings(deepseek_api_key=SecretStr("not-real"))
    async with httpx.AsyncClient(
        base_url="https://unit.invalid", transport=httpx.MockTransport(respond)
    ) as client:
        provider = DeepSeekProvider(settings, client)
        monkeypatch.setattr("app.services.container.DeepSeekProvider", lambda settings: provider)
        result = await run_task(load_tasks()[0], settings, CallBudget(1))
    assert not result["success"] and result["response"]["pending_action"] is None
    assert result["response"]["case_id"] is None and result["before"] == result["after"]


@pytest.mark.asyncio
async def test_stream_cancellation_rolls_back_session_and_case(tmp_path):
    kb = tmp_path / "knowledge"
    kb.mkdir()
    (kb / "cancel.md").write_text("# 取消政策\n未发货订单可申请取消。", encoding="utf-8")
    container = await build_container(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}", knowledge_dir=str(kb)),
        seed=True,
    )
    try:
        stream = container.chat_service.stream(
            ChatRequest(message="取消订单 ORD10003", user_id="cancel_stream"), trace_id="cancel"
        )
        async for event in stream:
            if event.get("node") == "plan_validation":
                await stream.aclose()
                break
        async with container.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(CaseRow)) == 0
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(SessionRow)
                    .where(SessionRow.user_id == "cancel_stream")
                )
                == 0
            )
    finally:
        await container.aclose()

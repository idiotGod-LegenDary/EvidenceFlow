import json

import httpx
import pytest
from pydantic import SecretStr

from app.config.settings import Settings
from app.core.exceptions import LLMOutputError
from app.observability.trace import CallBudget, capture_trace
from app.providers.deepseek import DeepSeekProvider
from app.providers.json_parser import parse_model
from app.schemas.agent import IntentResult
from eval.compare_service_runs import compare
from eval.service_benchmark import run_task, summarize
from eval.service_dataset import load_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("max_calls", [2, 3])
async def test_online_transport_reaches_state_oracle_and_obeys_budget(monkeypatch, max_calls):
    payloads = iter(
        [
            {
                "label": "cancel_order",
                "confidence": 1,
                "rationale": "test",
                "prompt_version": "test",
            },
            {"complexity": "tool_required", "strategy": "agent", "confidence": 1},
            {
                "steps": [
                    {
                        "step_id": 1,
                        "goal": "cancel",
                        "tool_name": "cancel_order",
                        "arguments": {"order_id": "ORD10003"},
                    }
                ]
            },
        ]
    )

    def respond(request):
        return httpx.Response(
            200,
            json={
                "model": "test-served-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [{"message": {"content": json.dumps(next(payloads))}}],
            },
        )

    settings = Settings(deepseek_api_key=SecretStr("test-not-real"))
    async with httpx.AsyncClient(
        base_url="https://unit.invalid", transport=httpx.MockTransport(respond)
    ) as client:
        provider = DeepSeekProvider(settings, client)
        monkeypatch.setattr("app.services.container.DeepSeekProvider", lambda settings: provider)
        result = await run_task(load_tasks()[0], settings, CallBudget(max_calls))
    assert len(result["model_calls"]) == max_calls
    assert not result["unsafe_write"]
    if max_calls == 3:
        assert result["success"] and result["case_status"] == "completed"
        summary = summarize([result], 1, mode="online")
        assert (
            summary["reported_prompt_tokens"] == 30 and summary["reported_completion_tokens"] == 15
        )
    else:
        assert not result["success"] and result["error_type"] == "EvaluationBudgetExceeded"
        assert result["before"] == result["after"]


@pytest.mark.parametrize("content", ["not JSON", "{}"])
def test_invalid_structured_output_is_visible_even_when_caller_falls_back(content):
    with capture_trace() as trace, pytest.raises(LLMOutputError):
        parse_model(content, IntentResult)
    assert trace.export()["output_errors"] == ["IntentResult"]


def test_comparison_rejects_model_or_task_mismatch(tmp_path):
    left, right = tmp_path / "before", tmp_path / "after"
    left.mkdir()
    right.mkdir()
    summary = {
        "requested_model": "same",
        "passed": 0,
        "unsafe_writes": 0,
        "model_http_attempts": 1,
        "median_task_latency_ms": 1,
    }
    row = {"task_id": "one", "repeat": 0, "success": False}
    for path in (left, right):
        (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (path / "tasks.jsonl").write_text(json.dumps(row), encoding="utf-8")
    row["success"] = True
    (right / "tasks.jsonl").write_text(json.dumps(row), encoding="utf-8")
    assert compare(left, right)["improved"] == [["one", 0]]
    summary["requested_model"] = "different"
    (right / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="requested_model"):
        compare(left, right)
    summary["requested_model"] = "same"
    (right / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    row["repeat"] = 1
    (right / "tasks.jsonl").write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="same attempted tasks"):
        compare(left, right)


@pytest.mark.asyncio
@pytest.mark.parametrize("order_id", ["<待用户提供>", "ORD10003"])
async def test_missing_order_with_model_parameter_becomes_clarification(monkeypatch, order_id):
    payloads = iter(
        [
            {"label": "cancel_order", "confidence": 1, "prompt_version": "test"},
            {"complexity": "tool_required", "strategy": "agent", "confidence": 1},
            {
                "steps": [
                    {
                        "step_id": 1,
                        "goal": "取消",
                        "tool_name": "cancel_order",
                        "arguments": {"order_id": order_id},
                    }
                ]
            },
        ]
    )

    def respond(request):
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [{"message": {"content": json.dumps(next(payloads))}}],
            },
        )

    settings = Settings(deepseek_api_key=SecretStr("test-not-real"))
    async with httpx.AsyncClient(
        base_url="https://unit.invalid", transport=httpx.MockTransport(respond)
    ) as client:
        provider = DeepSeekProvider(settings, client)
        monkeypatch.setattr("app.services.container.DeepSeekProvider", lambda settings: provider)
        task = next(t for t in load_tasks() if t.family == "cancel_missing")
        result = await run_task(task, settings, CallBudget(3))
    assert result["success"] and result["error_type"] is None
    assert result["response"]["case_id"] is None and result["response"]["pending_action"] is None
    assert result["before"] == result["after"] and not result["tool_calls"]

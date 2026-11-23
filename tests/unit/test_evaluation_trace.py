import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from app.config.settings import Settings
from app.core.exceptions import LLMProviderError
from app.observability.trace import CallBudget, EvaluationBudgetExceeded, capture_trace, model_span
from app.providers.deepseek import DeepSeekProvider


@pytest.mark.asyncio
async def test_trace_records_actual_model_usage_and_no_credentials():
    secret = "test-only-not-a-real-key"
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "served-model-version",
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 25,
                    "completion_tokens": 4,
                    "total_tokens": 29,
                    "prompt_cache_hit_tokens": 10,
                },
            },
        )

    async with httpx.AsyncClient(
        base_url="https://unit.invalid", transport=httpx.MockTransport(respond)
    ) as client:
        provider = DeepSeekProvider(Settings(deepseek_api_key=SecretStr(secret)), client)
        budget = CallBudget(2)
        with capture_trace(budget) as trace:
            response = await provider.generate([{"role": "user", "content": "JSON"}], max_tokens=50)
        record = trace.export()["model_calls"][0]
        assert response.model == record["actual_model"] == "served-model-version"
        assert response.usage.total_tokens == 29 and budget.used == 1
        assert record["usage"]["prompt_cache_hit_tokens"] == 10
        assert record["usage_reported"] and record["success"]
        assert requests[0]["thinking"] == {"type": "disabled"}
        assert record["generation"]["max_tokens"] == 50
        assert secret not in json.dumps(trace.export())


@pytest.mark.asyncio
async def test_request_budget_counts_retries_and_stops_before_http(monkeypatch):
    attempts = 0
    monkeypatch.setattr("app.core.retry._full_jitter_delay", lambda *args: 0)

    def unavailable(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    async with httpx.AsyncClient(
        base_url="https://unit.invalid", transport=httpx.MockTransport(unavailable)
    ) as client:
        provider = DeepSeekProvider(
            Settings(deepseek_api_key=SecretStr("test"), llm_max_retries=5), client
        )
        with capture_trace(CallBudget(2)) as trace, pytest.raises(EvaluationBudgetExceeded):
            await provider.generate([{"role": "user", "content": "JSON"}])
        assert attempts == len(trace.model_calls) == 2
        assert all(c["http_status"] == 503 and not c["success"] for c in trace.model_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 422])
async def test_non_retryable_http_errors_are_single_attempt(status):
    async with httpx.AsyncClient(
        base_url="https://unit.invalid",
        transport=httpx.MockTransport(lambda r: httpx.Response(status)),
    ) as client:
        provider = DeepSeekProvider(Settings(deepseek_api_key=SecretStr("test")), client)
        with capture_trace(CallBudget(3)) as trace, pytest.raises(LLMProviderError):
            await provider.generate([])
        assert len(trace.model_calls) == 1 and not trace.model_calls[0]["success"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage", [None, {}, {"prompt_tokens": 4}, {"prompt_tokens": -1, "completion_tokens": 2}]
)
async def test_missing_or_invalid_usage_cannot_be_reported_as_zero_cost(usage):
    body = {"choices": [{"message": {"content": "ok"}}], "usage": usage}
    async with httpx.AsyncClient(
        base_url="https://unit.invalid",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    ) as client:
        provider = DeepSeekProvider(Settings(deepseek_api_key=SecretStr("test")), client)
        with capture_trace() as trace:
            await provider.generate([])
        assert not trace.model_calls[0]["usage_reported"]


@pytest.mark.asyncio
async def test_concurrent_trace_contexts_do_not_mix():
    async def one(label):
        with capture_trace() as trace:
            with model_span([{"role": "user", "content": label}], "test") as record:
                await asyncio.sleep(0)
                record["success"] = True
        return trace.export()

    traces = await asyncio.gather(one("first"), one("second"))
    assert [t["model_calls"][0]["messages"][0]["content"] for t in traces] == ["first", "second"]

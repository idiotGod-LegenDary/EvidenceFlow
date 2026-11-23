from __future__ import annotations

import copy
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


class EvaluationBudgetExceeded(RuntimeError):
    pass


@dataclass
class CallBudget:
    max_calls: int
    used: int = 0

    def reserve(self) -> None:
        if self.used >= self.max_calls:
            raise EvaluationBudgetExceeded("模型请求次数达到本轮上限。")
        self.used += 1


@dataclass
class RunTrace:
    budget: CallBudget | None = None
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    output_errors: list[str] = field(default_factory=list)

    def export(self) -> dict:
        return {
            "model_calls": copy.deepcopy(self.model_calls),
            "tool_calls": copy.deepcopy(self.tool_calls),
            "output_errors": list(self.output_errors),
        }


_trace: ContextVar[RunTrace | None] = ContextVar("evaluation_trace", default=None)


def record_output_error(schema_name: str) -> None:
    trace = _trace.get()
    if trace is not None:
        trace.output_errors.append(schema_name)


@contextmanager
def capture_trace(budget: CallBudget | None = None) -> Iterator[RunTrace]:
    trace = RunTrace(budget=budget)
    token = _trace.set(trace)
    try:
        yield trace
    finally:
        _trace.reset(token)


@contextmanager
def model_span(messages: list[dict[str, str]], model: str) -> Iterator[dict]:
    trace = _trace.get()
    record: dict[str, Any] = {}
    if trace is not None:
        if trace.budget is not None:
            trace.budget.reserve()
        record = {
            "requested_model": model,
            "messages": copy.deepcopy(messages),
            "usage_reported": False,
            "success": False,
        }
        trace.model_calls.append(record)
    started = time.perf_counter()
    try:
        yield record
    except BaseException as exc:
        if trace is not None:
            record["error_type"] = type(exc).__name__
        raise
    finally:
        if trace is not None:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)


@contextmanager
def tool_span(name: str, arguments: dict) -> Iterator[dict]:
    trace = _trace.get()
    record: dict[str, Any] = {}
    if trace is not None:
        record = {"name": name, "arguments": copy.deepcopy(arguments), "success": False}
        trace.tool_calls.append(record)
    started = time.perf_counter()
    try:
        yield record
    except BaseException as exc:
        if trace is not None:
            record["error_type"] = type(exc).__name__
        raise
    finally:
        if trace is not None:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)

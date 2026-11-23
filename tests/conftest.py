"""pytest bootstrap: never call real LLM/DB from developer .env."""

import os
from pathlib import Path

import pytest
from sse_starlette.sse import AppStatus

os.environ["APP_ENV"] = "testing"
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["CROSS_ENCODER_FORCE"] = "false"
os.environ["LOCAL_EMBEDDING_FORCE"] = "false"
os.environ["UNSTRUCTURED_FORCE"] = "false"
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/local/test.db"
os.environ["REDIS_URL"] = ""
os.environ["QDRANT_URL"] = ""
os.environ["LOG_LEVEL"] = "WARNING"

Path("data/local").mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def isolate_sse_shutdown_state(monkeypatch):
    # Each TestClient owns a different event loop; the SSE exit event is process-global.
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    monkeypatch.setattr(AppStatus, "should_exit", False)

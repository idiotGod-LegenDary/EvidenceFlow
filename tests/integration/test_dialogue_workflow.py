from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from alembic import command
from alembic.config import Config
from app.config.settings import Settings
from app.repositories.models import DialogueRow
from app.schemas.chat import ChatRequest
from app.services.container import build_container


@pytest.mark.asyncio
async def test_ambiguous_selection_survives_restart_and_then_executes(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "cancel.md").write_text("# 取消条件\n尚未发货的订单可以取消。", encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'dialogue.db'}", knowledge_dir=str(knowledge)
    )
    container = await build_container(settings, seed=True)
    try:
        first = await container.chat_service.chat(
            ChatRequest(
                message="我想取消 ORD10003 和 ORD10001 其中一单，尚未决定", user_id="u_demo"
            ),
            trace_id="choice",
        )
        assert first.pending_action is None and first.case_id is None and "哪" in first.answer
        async with container.session_factory() as db:
            row = await db.get(DialogueRow, first.session_id)
            assert "awaiting_selection" in row.slots_json
        await container.aclose()
        container = await build_container(settings)
        second = await container.chat_service.chat(
            ChatRequest(message="选 ORD10003", session_id=first.session_id, user_id="u_demo"),
            trace_id="selection",
        )
        assert second.pending_action and second.pending_action.arguments["order_id"] == "ORD10003"
        done = await container.chat_service.chat(
            ChatRequest(
                message="确认",
                user_id="u_demo",
                session_id=first.session_id,
                confirm_action_id=second.pending_action.action_id,
            ),
            trace_id="execute",
        )
        assert done.case_status == "completed"
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_consultation_is_read_only_and_can_be_followed_by_application(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "returns.md").write_text(
        "# 退货条件\n已发货商品可以申请退货，需说明原因。", encoding="utf-8"
    )
    container = await build_container(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'consult.db'}",
            knowledge_dir=str(knowledge),
        ),
        seed=True,
    )
    try:
        first = await container.chat_service.chat(
            ChatRequest(message="仅咨询 ORD10001 的退货条件", user_id="u_demo"), trace_id="consult"
        )
        assert first.pending_action is None and first.case_id is None
        assert (
            first.citations
            and first.citations[0].content_sha256
            and first.citations[0].knowledge_version
        )
        second = await container.chat_service.chat(
            ChatRequest(
                message="我要退货这笔，原因是外观有瑕疵",
                user_id="u_demo",
                session_id=first.session_id,
            ),
            trace_id="apply",
        )
        assert second.pending_action and second.pending_action.tool_name == "create_return_request"
        assert second.pending_action.arguments["order_id"] == "ORD10001"
    finally:
        await container.aclose()


def test_dialogue_table_migrates_without_changing_existing_cases(tmp_path):
    config = Config()
    config.set_main_option("script_location", str(Path("alembic").resolve()))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'migrate.db'}")
    command.upgrade(config, "head")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        assert {c["name"] for c in inspect(engine).get_columns("dialogue_states")} == {
            "session_id",
            "slots_json",
        }
        command.downgrade(config, "0003")
        assert "dialogue_states" not in inspect(engine).get_table_names()
        assert "service_cases" in inspect(engine).get_table_names()
        command.upgrade(config, "head")
    finally:
        engine.dispose()

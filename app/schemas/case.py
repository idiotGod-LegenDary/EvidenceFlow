from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.chat import ActionProposal, ChatMessage
from app.schemas.retrieval import RetrievedChunk

CaseStatus = Literal[
    "pending_review", "executing", "completed", "needs_review", "failed", "cancelled"
]


class CaseEvent(BaseModel):
    kind: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CaseSummary(BaseModel):
    case_id: str
    session_id: str
    title: str
    order_id: str
    status: CaseStatus
    notice: str
    attempts: int
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class CaseDetail(CaseSummary):
    request_text: str
    action: ActionProposal
    knowledge_version: str
    evidence: list[RetrievedChunk]
    order_before: dict[str, Any]
    result: dict[str, Any]
    verification: dict[str, Any]
    events: list[CaseEvent]
    messages: list[ChatMessage]


class CaseCommand(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.dialogue import DialogueSlots
from app.core.enums import IntentLabel
from app.schemas.agent import ToolResult
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.schemas.common import TokenUsage
from app.schemas.retrieval import RetrievedChunk


class ReadCall(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)


class ProposedOperation(BaseModel):
    tool: str
    arguments: dict


class ExecutionPlan(BaseModel):
    reads: list[ReadCall] = Field(default_factory=list, max_length=6)
    proposal: ProposedOperation | None = None
    clarification: str | None = None


class GroundedAnswer(BaseModel):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    needs_review: bool = False


@dataclass(frozen=True)
class RequestRoute:
    kind: Literal["greeting", "policy", "operation", "lookup", "handoff", "refuse"]
    intent: IntentLabel
    order_id: str | None = None
    operation: str | None = None


@dataclass
class TurnContext:
    request: ChatRequest
    session_id: str
    trace_id: str
    db: AsyncSession
    history: list[ChatMessage]
    query: str = ""
    slots: DialogueSlots = field(default_factory=DialogueSlots)
    usage: TokenUsage = field(default_factory=TokenUsage)
    evidence: list[RetrievedChunk] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)

    def account(self, usage: TokenUsage) -> None:
        self.usage.prompt_tokens += usage.prompt_tokens
        self.usage.completion_tokens += usage.completion_tokens
        self.usage.total_tokens += usage.total_tokens


@dataclass(frozen=True)
class Progress:
    phase: str


@dataclass(frozen=True)
class Completed:
    response: ChatResponse

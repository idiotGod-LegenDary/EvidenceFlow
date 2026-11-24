"""Tool execution receipts."""

from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import ToolRisk


class ToolResult(BaseModel):
    name: str
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    risk: ToolRisk = ToolRisk.READ
    truncated: bool = False

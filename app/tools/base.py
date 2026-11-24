from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from app.core.enums import ToolRisk
from app.core.exceptions import AppError, InvalidRequestError, ToolExecutionError
from app.observability.trace import tool_span
from app.schemas.agent import ToolResult


class ToolContext(BaseModel):
    user_id: str = Field(min_length=1)
    session_id: str
    trace_id: str
    confirmed_action_id: str | None = None
    idempotency_key: str | None = None


class BaseTool(Protocol):
    name: str
    risk: ToolRisk
    input_model: type[BaseModel]

    async def run(self, arguments: dict, context: ToolContext) -> ToolResult: ...


class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    risk = ToolRisk.READ

    def parse(self, arguments: dict) -> BaseModel:
        try:
            return self.input_model.model_validate(arguments)
        except ValidationError as error:
            raise InvalidRequestError(f"{self.name} 的参数不符合约束。") from error

    async def run(self, arguments: dict, context: ToolContext) -> ToolResult:
        with tool_span(self.name, arguments) as event:
            payload = self.parse(arguments)
            try:
                receipt = await self.execute(payload, context)
            except AppError:
                raise
            except Exception as error:
                raise ToolExecutionError(f"{self.name} 执行失败。") from error
            event["success"] = receipt.success
            event["output"] = receipt.output
            return receipt

    async def execute(self, parsed: BaseModel, context: ToolContext) -> ToolResult:
        raise NotImplementedError(self.name)

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }

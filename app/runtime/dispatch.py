from __future__ import annotations

import json

from app.core.enums import ToolRisk
from app.core.exceptions import AppError, ToolPermissionError
from app.runtime.contracts import ExecutionPlan, TurnContext
from app.schemas.agent import ToolResult
from app.tools.base import ToolContext
from app.tools.registry import ToolRegistry


class ReadDispatcher:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    async def execute(
        self, plan: ExecutionPlan, turn: TurnContext, *, allow_handoff: bool = False
    ) -> None:
        memo: dict[str, ToolResult] = {}
        context = ToolContext(
            user_id=turn.request.user_id, session_id=turn.session_id, trace_id=turn.trace_id
        )
        for call in plan.reads:
            tool = self.registry.get(call.tool)
            if tool.risk != ToolRisk.READ and not (
                allow_handoff and call.tool == "escalate_to_human"
            ):
                raise ToolPermissionError("本次查询计划不能执行业务写入。")
            fingerprint = call.tool + json.dumps(call.arguments, sort_keys=True)
            if fingerprint not in memo:
                try:
                    memo[fingerprint] = await tool.run(call.arguments, context)
                except AppError as exc:
                    memo[fingerprint] = ToolResult(
                        name=call.tool, success=False, error=exc.error_code.value
                    )
            turn.results.append(memo[fingerprint])

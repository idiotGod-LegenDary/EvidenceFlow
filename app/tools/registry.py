from __future__ import annotations

import re

from app.core.exceptions import InvalidRequestError
from app.tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._catalog: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", tool.name):
            raise ValueError("Invalid tool identifier")
        if tool.name in self._catalog:
            raise ValueError(f"Duplicate tool registration: {tool.name}")
        self._catalog[tool.name] = tool

    def get(self, name: str) -> Tool:
        found = self._catalog.get(name)
        if found is None:
            raise InvalidRequestError(f"未注册的工具：{name}")
        return found

    def names(self) -> list[str]:
        return list(self._catalog.keys())

    def all(self) -> list[Tool]:
        return [self._catalog[name] for name in self.names()]

    def openai_tools(self) -> list[dict]:
        return [entry.openai_schema() for entry in self.all()]

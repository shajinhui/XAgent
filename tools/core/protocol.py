"""工具抽象协议和函数式工具适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from tools.core.types import ToolExecutionContext, ToolMeta, ToolResult


ToolHandler = Callable[[ToolExecutionContext, dict], str | ToolResult]
SchemaProvider = Callable[[], dict]


class Tool(Protocol):
    """registry/runner 共同依赖的最小工具接口。"""

    meta: ToolMeta

    def schema(self) -> dict:
        """返回模型可见的 tool schema。"""

    def run(self, ctx: ToolExecutionContext, payload: dict) -> str | ToolResult:
        """执行工具并返回文本或结构化 ToolResult。"""


@dataclass(frozen=True)
class FunctionTool:
    """把现有函数式工具适配为 Tool 协议。"""

    meta: ToolMeta
    schema_provider: SchemaProvider
    handler: ToolHandler

    def schema(self) -> dict:
        return self.schema_provider()

    def run(self, ctx: ToolExecutionContext, payload: dict) -> str | ToolResult:
        return self.handler(ctx, payload)

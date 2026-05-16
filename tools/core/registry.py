"""纯工具注册表：只负责保存、查询和暴露工具定义。"""

from __future__ import annotations

from dataclasses import asdict

from tools.core.protocol import FunctionTool, SchemaProvider, Tool, ToolHandler
from tools.core.types import ToolMeta


class ToolRegistry:
    """工具注册表，不负责执行、权限判断或异常包装。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register_tool(tool)

    def register_tool(self, tool: Tool) -> None:
        name = tool.meta.name
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = tool

    def register(
        self,
        meta: ToolMeta,
        schema_provider: SchemaProvider,
        handler: ToolHandler,
    ) -> None:
        self.register_tool(FunctionTool(meta, schema_provider, handler))

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def metadata(self) -> dict[str, dict]:
        return {name: asdict(tool.meta) for name, tool in self._tools.items()}

    def names(self) -> list[str]:
        return list(self._tools)

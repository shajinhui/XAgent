"""兼容门面：对外保留 ToolRegistry(project_root, session_id) 构造方式。"""

from __future__ import annotations

from pathlib import Path

from tools.core.catalog import build_default_registry
from tools.core.protocol import SchemaProvider, ToolHandler
from tools.core.registry import ToolRegistry as CoreToolRegistry
from tools.core.runner import ToolRunner, create_tool_context
from tools.core.types import ToolExecutionContext, ToolMeta, ToolResult


class ToolRegistry:
    """兼容旧调用方的工具入口。

    真正的注册表在 `tools.core.registry`，执行逻辑在 `tools.core.runner`。
    这里仅负责把旧的 `schemas/metadata/execute` API 代理到新分层。
    """

    def __init__(self, project_root: Path, session_id: str = "default") -> None:
        self.project_root = project_root.resolve()
        self.ctx: ToolExecutionContext = create_tool_context(self.project_root, session_id)
        self._registry: CoreToolRegistry = build_default_registry()
        self._runner = ToolRunner(self._registry, self.ctx)

    def register(
        self,
        meta: ToolMeta,
        schema_provider: SchemaProvider,
        handler: ToolHandler,
    ) -> None:
        self._registry.register(meta, schema_provider, handler)

    def schemas(self) -> list[dict]:
        return self._registry.schemas()

    def metadata(self) -> dict[str, dict]:
        return self._registry.metadata()

    def execute(self, name: str, arguments: str, approved: bool = False) -> ToolResult:
        return self._runner.execute(name, arguments, approved=approved)

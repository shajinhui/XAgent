from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from sandbox.docker_executor import SecureDockerExecutor
from security.circuit_breaker import CircuitBreaker
from security.policy import SecurityPolicy
from tools import edit_file, grep, read_file, run_command, web_fetch, write_file
from tools.types import ToolExecutionContext, ToolMeta, ToolPermissionError, ToolResult


ToolHandler = Callable[[ToolExecutionContext, dict], str]
SchemaProvider = Callable[[], dict]


@dataclass
class RegisteredTool:
    schema: SchemaProvider
    handler: ToolHandler
    meta: ToolMeta


class ToolRegistry:
    def __init__(self, project_root: Path, session_id: str = "default") -> None:
        self.project_root = project_root.resolve()
        self.ctx = ToolExecutionContext(
            project_root=self.project_root,
            session_id=session_id,
            policy=SecurityPolicy(self.project_root),
            circuit_breaker=CircuitBreaker(threshold=3),
            docker_executor=SecureDockerExecutor(self.project_root),
        )
        self._tools: dict[str, RegisteredTool] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(read_file.META, read_file.schema, read_file.run)
        self.register(write_file.META, write_file.schema, write_file.run)
        self.register(edit_file.META, edit_file.schema, edit_file.run)
        self.register(grep.META, grep.schema, grep.run)
        self.register(run_command.META, run_command.schema, run_command.run)
        self.register(web_fetch.META, web_fetch.schema, web_fetch.run)

    def register(self, meta: ToolMeta, schema_provider: SchemaProvider, handler: ToolHandler) -> None:
        self._tools[meta.name] = RegisteredTool(schema=schema_provider, handler=handler, meta=meta)

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def metadata(self) -> dict[str, dict]:
        return {name: asdict(tool.meta) for name, tool in self._tools.items()}

    def execute(self, name: str, arguments: str, approved: bool = False) -> ToolResult:
        if name not in self._tools:
            return ToolResult(ok=False, content=f"未知工具: {name}")

        try:
            payload = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return ToolResult(ok=False, content=f"工具参数不是合法 JSON: {exc}")

        tool = self._tools[name]
        try:
            if tool.meta.requires_approval and name != "run_command" and not approved:
                raise ToolPermissionError(
                    f"工具需要用户确认: {name}",
                    metadata={
                        "error_type": "permission_required",
                        "permission_action": "ask",
                        "category": "tool_approval",
                        "tool": name,
                    },
                )
            payload["_approved"] = approved
            content = tool.handler(self.ctx, payload)
            return ToolResult(ok=True, content=content, metadata={"tool": name})
        except ToolPermissionError as exc:
            metadata = {"tool": name, "error_type": "permission_denied"}
            metadata.update(exc.metadata)
            return ToolResult(
                ok=False,
                content=f"权限拒绝: {exc}",
                metadata=metadata,
            )
        except PermissionError as exc:
            return ToolResult(
                ok=False,
                content=f"权限拒绝: {exc}",
                metadata={"tool": name, "error_type": "permission_denied"},
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"工具执行失败: {exc}",
                metadata={"tool": name, "error_type": "runtime_error"},
            )

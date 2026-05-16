"""工具执行器：统一参数解析、审批适配、异常包装和结果归一化。"""

from __future__ import annotations

import json
from pathlib import Path

from sandbox.macos_executor import SecureMacOSSandboxExecutor
from security.circuit_breaker import CircuitBreaker
from security.policy import SecurityPolicy
from tools.core.registry import ToolRegistry
from tools.core.types import ToolExecutionContext, ToolPermissionError, ToolResult


def create_tool_context(project_root: Path, session_id: str = "default") -> ToolExecutionContext:
    root = project_root.resolve()
    return ToolExecutionContext(
        project_root=root,
        session_id=session_id,
        policy=SecurityPolicy(root),
        circuit_breaker=CircuitBreaker(threshold=3),
        command_executor=SecureMacOSSandboxExecutor(root),
    )


class ToolRunner:
    """执行 registry 中的工具，不承担工具集合装配职责。"""

    def __init__(self, registry: ToolRegistry, ctx: ToolExecutionContext) -> None:
        self.registry = registry
        self.ctx = ctx

    def execute(self, name: str, arguments: str, approved: bool = False) -> ToolResult:
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(ok=False, content=f"未知工具: {name}")

        try:
            payload = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return ToolResult(ok=False, content=f"工具参数不是合法 JSON: {exc}")

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
            handler_result = tool.run(self.ctx, payload)
            if isinstance(handler_result, ToolResult):
                metadata = {"tool": name}
                metadata.update(handler_result.metadata or {})
                return ToolResult(
                    ok=handler_result.ok,
                    content=handler_result.content,
                    metadata=metadata,
                )

            return ToolResult(ok=True, content=handler_result, metadata={"tool": name})
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

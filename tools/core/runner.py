"""工具执行器：统一参数解析、审批适配、异常包装和结果归一化。"""

from __future__ import annotations

import json
from pathlib import Path

from sandbox.macos_executor import SecureMacOSSandboxExecutor
from security.circuit_breaker import CircuitBreaker
from security.exec_policy import ExecPolicy
from security.permissions import ApprovalPolicy, FileSystemPolicy, NetworkPolicy, PermissionProfile
from security.policy import SecurityPolicy
from tools.core.context import ToolInvocation
from tools.core.registry import ToolRegistry
from tools.core.types import ToolExecutionContext, ToolPermissionError, ToolResult
from workspace import AdditionalRoot


def create_tool_context(
    selected_root: Path,
    session_id: str = "default",
    *,
    project_root: Path | None = None,
    current_dir: Path | None = None,
    additional_roots: tuple[AdditionalRoot, ...] | list[AdditionalRoot] = (),
    filesystem_policy: FileSystemPolicy | None = None,
    exec_policy: ExecPolicy | None = None,
    network_policy: NetworkPolicy = NetworkPolicy.RESTRICTED,
    permission_profile: PermissionProfile = PermissionProfile.WORKSPACE_WRITE,
    approval_policy: ApprovalPolicy = ApprovalPolicy.ASK_BEFORE_MUTATING,
    circuit_breaker: CircuitBreaker | None = None,
) -> ToolExecutionContext:
    selected = selected_root.resolve()
    project = (project_root or selected).resolve()
    current = (current_dir or selected).resolve()
    resolved_filesystem_policy = filesystem_policy or _filesystem_policy_for_profile(
        selected,
        current,
        permission_profile,
        additional_roots,
    )
    policy = SecurityPolicy(
        selected,
        project_root=project,
        current_dir=current,
        filesystem_policy=resolved_filesystem_policy,
        exec_policy=exec_policy,
        permission_profile=permission_profile,
        approval_policy=approval_policy,
    )
    return ToolExecutionContext(
        selected_root=selected,
        project_root=project,
        current_dir=current,
        session_id=session_id,
        policy=policy,
        filesystem_policy=resolved_filesystem_policy,
        network_policy=network_policy,
        permission_profile=permission_profile,
        approval_policy=approval_policy,
        circuit_breaker=circuit_breaker or CircuitBreaker(threshold=3),
        command_executor=SecureMacOSSandboxExecutor(selected),
    )


class ToolRunner:
    """执行 registry 中的工具，不承担工具集合装配职责。"""

    def __init__(self, registry: ToolRegistry, ctx: ToolExecutionContext) -> None:
        self.registry = registry
        self.ctx = ctx

    def execute_invocation(
        self,
        invocation: ToolInvocation,
        approved: bool | None = None,
    ) -> ToolResult:
        """执行已路由的工具调用，并临时挂载 turn 级别的变更跟踪器。"""

        is_approved = invocation.approval.approved if approved is None else approved
        previous_diff_tracker = self.ctx.diff_tracker
        self.ctx.diff_tracker = invocation.diff_tracker
        try:
            result = self.execute(invocation.name, invocation.arguments, approved=is_approved)
        finally:
            self.ctx.diff_tracker = previous_diff_tracker
        if result.ok:
            self._record_invocation_diff(invocation)
        return result

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
                if self.ctx.approval_policy == ApprovalPolicy.NEVER:
                    raise ToolPermissionError(
                        f"当前 approval policy 禁止请求用户批准: {name}",
                        metadata={
                            "error_type": "permission_denied",
                            "permission_action": "deny",
                            "category": "approval_unavailable",
                            "tool": name,
                            **_permission_context_metadata(self.ctx),
                        },
                    )
                if self.ctx.approval_policy != ApprovalPolicy.AUTO:
                    raise ToolPermissionError(
                        f"工具需要用户确认: {name}",
                        metadata={
                            "error_type": "permission_required",
                            "permission_action": "ask",
                            "category": "tool_approval",
                            "tool": name,
                            **_permission_context_metadata(self.ctx),
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

    def _record_invocation_diff(self, invocation: ToolInvocation) -> None:
        """保留变更记录边界；具体 baseline 由 mutating 工具在写入前保存。"""

        tool = self.registry.get(invocation.name)
        if tool is None or not tool.meta.is_mutating:
            return


def _filesystem_policy_for_profile(
    selected_root: Path,
    current_dir: Path,
    permission_profile: PermissionProfile,
    additional_roots: tuple[AdditionalRoot, ...] | list[AdditionalRoot] = (),
) -> FileSystemPolicy:
    if permission_profile == PermissionProfile.READ_ONLY:
        return FileSystemPolicy.read_only(
            selected_root,
            current_dir=current_dir,
            additional_roots=additional_roots,
        )
    if permission_profile == PermissionProfile.DANGER_NO_SANDBOX:
        return FileSystemPolicy.danger_full_access(
            selected_root,
            current_dir=current_dir,
        )
    return FileSystemPolicy.workspace_write(
        selected_root,
        current_dir=current_dir,
        additional_roots=additional_roots,
    )


def _permission_context_metadata(ctx: ToolExecutionContext) -> dict[str, str]:
    """生成前端权限弹窗需要展示的当前运行边界。"""

    return {
        "selected_root": ctx.selected_root.as_posix(),
        "current_dir": ctx.current_dir.as_posix(),
        "permission_profile": ctx.permission_profile.value,
        "approval_policy": ctx.approval_policy.value,
        "network_policy": ctx.network_policy.value,
    }

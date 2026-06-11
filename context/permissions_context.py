"""
用于 turn 的权限与沙箱上下文（中文注释）。

该模块定义了执行工具和命令时需要携带的安全相关快照，包含受保护路径、
审批策略与是否允许网络等配置，便于在生成模型上下文或执行前校验权限。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from workspace import WorkspaceContext
from workspace.project_config import default_project_policy


DEFAULT_PROTECTED_PATHS = (".env", ".git", ".venv", "__pycache__", ".codex-mini")


@dataclass(frozen=True)
class PermissionsContext:
    """表示一次 turn 的安全边界快照（不可变）。

    用于把工作区相关的安全策略以不可变结构传入模型或工具执行层。
    """

    selected_root: Path
    project_root: Path
    additional_roots: tuple[Dict[str, Any], ...]
    protected_paths: tuple[str, ...] = DEFAULT_PROTECTED_PATHS
    approval_policy: str = "ask-before-mutating"
    command_sandbox: str = "macos-seatbelt"
    network_allowed: bool = False

    @classmethod
    def from_workspace(cls, workspace: WorkspaceContext) -> "PermissionsContext":
        project_policy = workspace.project_policy or default_project_policy()
        return cls(
            selected_root=workspace.selected_root,
            project_root=workspace.project_root,
            additional_roots=tuple(root.as_dict() for root in workspace.additional_roots),
            approval_policy=project_policy.approval_policy.value,
            command_sandbox=(
                "none"
                if project_policy.permission_profile.value == "danger_no_sandbox"
                else "macos-seatbelt"
            ),
            network_allowed=project_policy.network_policy.value == "enabled",
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "selected_root": self.selected_root.as_posix(),
            "project_root": self.project_root.as_posix(),
            "additional_roots": list(self.additional_roots),
            "protected_paths": list(self.protected_paths),
            "approval_policy": self.approval_policy,
            "command_sandbox": self.command_sandbox,
            "network_allowed": self.network_allowed,
        }

    def render_fragment(self) -> str:
        """渲染为可读字符串，便于在模型提示或日志中显示权限摘要。"""
        return (
            f"Approval policy: {self.approval_policy}\n"
            f"Command sandbox: {self.command_sandbox}\n"
            f"Network allowed: {str(self.network_allowed).lower()}"
        )

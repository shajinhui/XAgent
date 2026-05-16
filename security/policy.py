"""
路径和命令安全策略。

这里不执行任何工具，只负责判断路径是否越界、写入是否触碰保护目录、
命令是否危险、是否允许执行或需要用户确认。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from security.permissions import ApprovalPolicy, FileSystemPolicy, PermissionProfile


DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r":\(\)\{:\|:&\};:",
    r"mkfs\.",
    r"dd\s+if=",
    r"shutdown\b",
    r"reboot\b",
    r"curl\s+[^|]*\|\s*(sh|bash)",
    r"wget\s+[^|]*\|\s*(sh|bash)",
]

ALLOWED_COMMANDS = {
    "ls",
    "pwd",
    "cat",
    "head",
    "tail",
    "echo",
    "rg",
    "grep",
    "find",
    "python",
    "python3",
    "pytest",
    "pip",
    "npm",
    "node",
    "git",
    "make",
}

PROTECTED_WRITE_PATHS = {
    ".env",
}

PROTECTED_WRITE_PREFIXES = {
    ".git",
    ".venv",
    "__pycache__",
}

PROTECTED_COMMAND_PATTERNS = [
    r"(^|\s)(\.env)(\s|$|[;&|<>])",
    r"(^|\s)(\.git)(/|\s|$|[;&|<>])",
    r"(^|\s)(\.codex-mini)(/|\s|$|[;&|<>])",
]


@dataclass
class CommandDecision:
    """命令策略检查后的动作、分类和说明。"""

    action: Literal["allow", "deny", "ask"]
    category: str
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    @property
    def requires_approval(self) -> bool:
        return self.action == "ask"


class SecurityPolicy:
    """单个 workspace 的文件路径和命令安全策略。"""

    def __init__(
        self,
        selected_root: Path,
        *,
        project_root: Path | None = None,
        current_dir: Path | None = None,
        filesystem_policy: FileSystemPolicy | None = None,
        permission_profile: PermissionProfile = PermissionProfile.WORKSPACE_WRITE,
        approval_policy: ApprovalPolicy = ApprovalPolicy.ASK_BEFORE_MUTATING,
    ) -> None:
        self.selected_root = selected_root.resolve()
        self.project_root = (project_root or self.selected_root).resolve()
        self.current_dir = (current_dir or self.selected_root).resolve()
        self.permission_profile = permission_profile
        self.approval_policy = approval_policy
        self.filesystem_policy = filesystem_policy or _default_filesystem_policy(
            self.selected_root,
            self.current_dir,
            permission_profile,
        )

    def resolve_path(self, raw_path: str) -> Path:
        """解析路径，并确保结果仍在 workspace policy 已知根目录内。"""

        return self.filesystem_policy.resolve_path(raw_path)

    def resolve_read_path(self, raw_path: str) -> Path:
        """解析并确认目标路径可读。"""

        return self.filesystem_policy.resolve_read_path(raw_path)

    def resolve_write_path(self, raw_path: str) -> Path:
        """解析并确认目标路径可写。"""

        return self.filesystem_policy.resolve_write_path(raw_path)

    def resolve_command_cwd(self, raw_cwd: str | None = None) -> Path:
        """解析命令 cwd；cwd 只能是 workspace 内的普通目录。"""

        return self.filesystem_policy.resolve_command_cwd(raw_cwd)

    def ensure_writable_path(self, path: Path) -> None:
        """确认目标路径允许写入。"""

        self.filesystem_policy.ensure_writable_path(path)

    def check_command(self, command: str, approved: bool = False) -> CommandDecision:
        """判断命令是允许、拒绝，还是需要用户确认。"""

        normalized = " ".join(command.strip().split()).lower()

        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, normalized):
                return CommandDecision(
                    action="deny",
                    category="dangerous_shell",
                    reason=f"命中危险模式: {pattern}",
                )

        for pattern in PROTECTED_COMMAND_PATTERNS:
            if re.search(pattern, normalized):
                return CommandDecision(
                    action="deny",
                    category="protected_path",
                    reason=f"命令涉及受保护路径: {pattern}",
                )

        try:
            parts = shlex.split(command)
        except ValueError as exc:
            return CommandDecision("deny", "dangerous_shell", f"命令解析失败: {exc}")

        if not parts:
            return CommandDecision("deny", "dangerous_shell", "命令为空")

        cmd = parts[0]
        if cmd not in ALLOWED_COMMANDS:
            if approved:
                return CommandDecision(
                    action="allow",
                    category="user_approved_command",
                    reason=f"用户已确认执行非白名单命令: {cmd}",
                )
            return CommandDecision(
                action="ask",
                category="command_approval",
                reason=f"命令不在白名单中，需要用户确认: {cmd}",
            )

        return CommandDecision(action="allow", category="allowed")


def _default_filesystem_policy(
    selected_root: Path,
    current_dir: Path,
    permission_profile: PermissionProfile,
) -> FileSystemPolicy:
    if permission_profile == PermissionProfile.READ_ONLY:
        return FileSystemPolicy.read_only(selected_root, current_dir=current_dir)
    return FileSystemPolicy.workspace_write(selected_root, current_dir=current_dir)

"""
路径和命令安全策略。

这里不执行任何工具，只负责判断路径是否越界、写入是否触碰保护目录、
命令是否危险、是否允许执行或需要用户确认。
"""

from __future__ import annotations

import shlex
from pathlib import Path

from security.exec_policy import CommandDecision, ExecPolicy, ExecPolicyRule
from security.permissions import ApprovalPolicy, FileSystemPolicy, PermissionProfile


PROTECTED_WRITE_PATHS = {
    ".env",
}

PROTECTED_WRITE_PREFIXES = {
    ".git",
    ".venv",
    "__pycache__",
}

class SecurityPolicy:
    """单个 workspace 的文件路径和命令安全策略。"""

    def __init__(
        self,
        selected_root: Path,
        *,
        project_root: Path | None = None,
        current_dir: Path | None = None,
        filesystem_policy: FileSystemPolicy | None = None,
        exec_policy: ExecPolicy | None = None,
        permission_profile: PermissionProfile = PermissionProfile.WORKSPACE_WRITE,
        approval_policy: ApprovalPolicy = ApprovalPolicy.ASK_BEFORE_MUTATING,
    ) -> None:
        self.selected_root = selected_root.resolve()
        self.project_root = (project_root or self.selected_root).resolve()
        self.current_dir = (current_dir or self.selected_root).resolve()
        self.permission_profile = permission_profile
        self.approval_policy = approval_policy
        self.exec_policy = exec_policy or ExecPolicy()
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
        """解析命令 cwd；cwd 必须是当前 filesystem policy 允许写入的目录。"""

        return self.filesystem_policy.resolve_command_cwd(raw_cwd)

    def ensure_writable_path(self, path: Path) -> None:
        """确认目标路径允许写入。"""

        self.filesystem_policy.ensure_writable_path(path)

    def check_command(self, command: str, approved: bool = False) -> CommandDecision:
        """判断命令是允许、拒绝，还是需要用户确认。"""

        decision = self.exec_policy.decide(self._policy_command(command), approved=approved)
        if (
            decision.approval_required
            and self.approval_policy == ApprovalPolicy.AUTO
            and decision.matched_prefix_rule is None
        ):
            return CommandDecision(
                action="allow",
                category="auto_approved_command",
                reason=decision.reason or "当前权限模式自动批准命令",
                suggested_prefix_rule=decision.suggested_prefix_rule,
                approval_required=False,
            )
        if decision.requires_approval and self.approval_policy == ApprovalPolicy.NEVER:
            return decision.deny("approval_unavailable", "当前 approval policy 禁止请求用户批准")
        return decision

    def allow_prefix_for_session(self, prefix_rule: tuple[str, ...]) -> None:
        """把用户批准的命令前缀加入当前会话的 allow rules。"""

        self.exec_policy = ExecPolicy(
            (*self.exec_policy.rules, ExecPolicyRule.allow(*prefix_rule)),
            protect_paths=self.exec_policy.protect_paths,
        )

    def _policy_command(self, command: str) -> str:
        """把已验证的 `cd <dir> && command` 还原成真正需要判断的命令。"""

        leading, separator, rest = command.partition("&&")
        if not separator or not rest.strip():
            return command

        try:
            leading_argv = tuple(shlex.split(leading))
        except ValueError:
            return command

        if len(leading_argv) != 2 or leading_argv[0] != "cd":
            return command

        try:
            self.resolve_command_cwd(leading_argv[1])
        except (PermissionError, ValueError):
            return command
        return rest.strip()


def _default_filesystem_policy(
    selected_root: Path,
    current_dir: Path,
    permission_profile: PermissionProfile,
) -> FileSystemPolicy:
    if permission_profile == PermissionProfile.READ_ONLY:
        return FileSystemPolicy.read_only(selected_root, current_dir=current_dir)
    return FileSystemPolicy.workspace_write(selected_root, current_dir=current_dir)

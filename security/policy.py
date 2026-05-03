from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


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
]


@dataclass
class CommandDecision:
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
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def resolve_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        resolved = candidate.resolve()
        if self.project_root not in resolved.parents and resolved != self.project_root:
            raise ValueError(f"路径越界，禁止访问: {resolved}")
        return resolved

    def ensure_writable_path(self, path: Path) -> None:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise PermissionError(f"路径越界，禁止写入: {resolved}") from exc

        relative_text = relative.as_posix()
        first_part = relative.parts[0] if relative.parts else ""
        if relative_text in PROTECTED_WRITE_PATHS or first_part in PROTECTED_WRITE_PREFIXES:
            raise PermissionError(f"受保护路径，禁止写入: {relative_text}")

    def check_command(self, command: str, approved: bool = False) -> CommandDecision:
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

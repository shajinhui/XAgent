"""Command execution policy primitives."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal


CommandAction = Literal["allow", "deny", "ask"]


DANGEROUS_PATTERNS = (
    r"rm\s+-rf\s+/",
    r":\(\)\{:\|:&\};:",
    r"mkfs\.",
    r"dd\s+if=",
    r"shutdown\b",
    r"reboot\b",
    r"curl\s+[^|]*\|\s*(sh|bash)",
    r"wget\s+[^|]*\|\s*(sh|bash)",
    r"sudo\b",
    r"chmod\s+-r\s+777\b",
)

PROTECTED_COMMAND_PATTERNS = (
    r"(^|\s)(\.env)(\s|$|[;&|<>])",
    r"(^|\s)(\.git)(/|\s|$|[;&|<>])",
    r"(^|\s)(\.codex-mini)(/|\s|$|[;&|<>])",
)

KNOWN_SAFE_COMMANDS = frozenset(
    {
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
)

PREFIX_SUGGESTION_DENYLIST = (
    ("bash",),
    ("sh",),
    ("zsh",),
    ("sudo",),
    ("rm",),
    ("mkfs",),
    ("dd",),
    ("curl",),
    ("wget",),
    ("python",),
    ("python3",),
    ("node", "-e"),
    ("node",),
)


@dataclass(frozen=True)
class CommandDecision:
    """命令策略检查后的动作、分类和说明。"""

    action: CommandAction
    category: str
    reason: str = ""
    suggested_prefix_rule: tuple[str, ...] | None = None
    matched_prefix_rule: tuple[str, ...] | None = None
    approval_required: bool = True

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    @property
    def requires_approval(self) -> bool:
        return self.action == "ask"

    def deny(self, category: str, reason: str) -> "CommandDecision":
        return CommandDecision(
            action="deny",
            category=category,
            reason=reason,
            suggested_prefix_rule=self.suggested_prefix_rule,
            matched_prefix_rule=self.matched_prefix_rule,
            approval_required=False,
        )


@dataclass(frozen=True)
class ExecPolicyRule:
    """Prefix-based command policy rule."""

    prefix: tuple[str, ...]
    action: CommandAction
    category: str = "exec_rule"
    reason: str = ""

    @classmethod
    def allow(cls, *prefix: str, category: str = "session_allow", reason: str = "") -> "ExecPolicyRule":
        return cls(tuple(prefix), "allow", category=category, reason=reason)

    @classmethod
    def ask(cls, *prefix: str, category: str = "command_approval", reason: str = "") -> "ExecPolicyRule":
        return cls(tuple(prefix), "ask", category=category, reason=reason)

    @classmethod
    def deny(cls, *prefix: str, category: str = "blocked_command", reason: str = "") -> "ExecPolicyRule":
        return cls(tuple(prefix), "deny", category=category, reason=reason)


class ExecPolicy:
    """Decides whether a shell command is allowed, denied, or needs approval."""

    def __init__(self, rules: tuple[ExecPolicyRule, ...] | list[ExecPolicyRule] = ()) -> None:
        self.rules = tuple(rules)

    @classmethod
    def with_session_allow(cls, prefixes: tuple[tuple[str, ...], ...] | list[tuple[str, ...]]) -> "ExecPolicy":
        return cls([ExecPolicyRule.allow(*prefix) for prefix in prefixes])

    def decide(self, command: str, *, approved: bool = False) -> CommandDecision:
        normalized = " ".join(command.strip().split()).lower()

        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, normalized):
                return CommandDecision(
                    action="deny",
                    category="dangerous_shell",
                    reason=f"命中危险模式: {pattern}",
                    approval_required=False,
                )

        for pattern in PROTECTED_COMMAND_PATTERNS:
            if re.search(pattern, normalized):
                return CommandDecision(
                    action="deny",
                    category="protected_path",
                    reason=f"命令涉及受保护路径: {pattern}",
                    approval_required=False,
                )

        try:
            argv = tuple(shlex.split(command))
        except ValueError as exc:
            return CommandDecision("deny", "dangerous_shell", f"命令解析失败: {exc}", approval_required=False)

        if not argv:
            return CommandDecision("deny", "dangerous_shell", "命令为空", approval_required=False)

        for rule in self.rules:
            if rule.action == "deny" and _matches_prefix(argv, rule.prefix):
                return _decision_for_rule(rule, approval_required=False)

        for rule in self.rules:
            if rule.action == "ask" and _matches_prefix(argv, rule.prefix):
                if approved:
                    return CommandDecision(
                        "allow",
                        "user_approved_command",
                        f"用户已确认执行命令: {' '.join(argv)}",
                        matched_prefix_rule=rule.prefix,
                        approval_required=False,
                    )
                return _decision_for_rule(
                    rule,
                    suggested_prefix_rule=_suggest_prefix_rule(argv),
                    approval_required=True,
                )

        for rule in self.rules:
            if rule.action == "allow" and _matches_prefix(argv, rule.prefix):
                return _decision_for_rule(rule, approval_required=False)

        cmd = argv[0]
        suggestion = _suggest_prefix_rule(argv)
        if cmd in KNOWN_SAFE_COMMANDS:
            return CommandDecision(
                action="allow",
                category="allowed",
                suggested_prefix_rule=suggestion,
                approval_required=True,
            )

        if approved:
            return CommandDecision(
                action="allow",
                category="user_approved_command",
                reason=f"用户已确认执行非白名单命令: {cmd}",
                suggested_prefix_rule=suggestion,
                approval_required=False,
            )

        return CommandDecision(
            action="ask",
            category="command_approval",
            reason=f"命令不在白名单中，需要用户确认: {cmd}",
            suggested_prefix_rule=suggestion,
            approval_required=True,
        )


def _decision_for_rule(
    rule: ExecPolicyRule,
    *,
    suggested_prefix_rule: tuple[str, ...] | None = None,
    approval_required: bool,
) -> CommandDecision:
    return CommandDecision(
        action=rule.action,
        category=rule.category,
        reason=rule.reason or f"命中命令前缀规则: {' '.join(rule.prefix)}",
        suggested_prefix_rule=suggested_prefix_rule,
        matched_prefix_rule=rule.prefix,
        approval_required=approval_required,
    )


def _matches_prefix(argv: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return bool(prefix) and len(argv) >= len(prefix) and argv[: len(prefix)] == prefix


def _suggest_prefix_rule(argv: tuple[str, ...]) -> tuple[str, ...] | None:
    if len(argv) < 2:
        return None

    if argv[0] == "npm" and len(argv) >= 3 and argv[1] == "run":
        candidate = argv[:3]
    elif argv[0] == "git" and len(argv) >= 2:
        candidate = argv[:2]
    elif len(argv) >= 2:
        candidate = argv[:2]
    else:
        return None

    if any(_matches_prefix(candidate, denied) for denied in PREFIX_SUGGESTION_DENYLIST):
        return None
    return candidate

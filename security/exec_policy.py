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
    r"(^|[;&|]\s*|\bxargs\s+)(?:[\w./-]+/)?(kill|killall|pkill)\b",
)

PROTECTED_COMMAND_PATTERNS = (
    r"(^|\s)(\.env)(\s|$|[;&|<>])",
    r"(^|\s)(\.git)(/|\s|$|[;&|<>])",
    r"(^|\s)(\.codex-mini)(/|\s|$|[;&|<>])",
)

SAFE_READ_ONLY_COMMANDS = frozenset(
    {"pwd", "ls", "rg", "grep", "cat", "head", "tail"}
)
SAFE_SORT_OPTIONS = frozenset({"-u"})
SAFE_FIND_VALUE_OPTIONS = frozenset({"-maxdepth", "-mindepth", "-name", "-type"})
SAFE_FIND_FLAG_OPTIONS = frozenset({"-not", "!", "-print", "-print0"})
SAFE_FIND_TYPES = frozenset({"f", "d", "l"})
SAFE_GIT_READ_ONLY_SUBCOMMANDS = frozenset(
    {"status", "log", "diff", "show", "rev-parse", "ls-files"}
)
UNSAFE_GIT_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--output", "-o", "--ext-diff"}
)
UNSAFE_GIT_OPTION_PREFIXES = (
    "--git-dir=",
    "--work-tree=",
    "--namespace=",
    "--exec-path=",
    "--output=",
)

KNOWN_COMMANDS_REQUIRING_APPROVAL = frozenset(
    {"echo", "find", "python", "python3", "pytest", "pip", "npm", "node", "git", "make"}
)

SHELL_META_CHARS = frozenset(";|&<>$`")
UNSAFE_READ_ONLY_OPTIONS = frozenset(
    {
        "-L",
        "--follow",
        "--pre",
        "--pre-glob",
        "-r",
        "-R",
        "--recursive",
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

    def __init__(
        self,
        rules: tuple[ExecPolicyRule, ...] | list[ExecPolicyRule] = (),
        *,
        protect_paths: bool = True,
    ) -> None:
        self.rules = tuple(rules)
        self.protect_paths = protect_paths

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

        if self.protect_paths:
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
        if _is_simple_read_only_command(command, argv) or _is_safe_read_only_pipeline(command):
            return CommandDecision(
                action="allow",
                category="safe_read_only",
                suggested_prefix_rule=None,
                approval_required=False,
            )

        if cmd in KNOWN_COMMANDS_REQUIRING_APPROVAL:
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


def _is_simple_read_only_command(command: str, argv: tuple[str, ...]) -> bool:
    """只给简单只读探索命令免审批，避免 shell 组合命令借壳执行写操作。"""

    if any(char in command for char in SHELL_META_CHARS):
        return False
    return _is_safe_read_only_argv(argv)


def _is_safe_read_only_pipeline(command: str) -> bool:
    """允许由只读探索命令组成的窄管道，例如目录枚举后接 sort。"""

    if "|" not in command:
        return False
    if any(char in command for char in SHELL_META_CHARS - {"|"}):
        return False

    segments = command.split("|")
    if len(segments) < 2 or any(not segment.strip() for segment in segments):
        return False

    for segment in segments:
        try:
            argv = tuple(shlex.split(segment))
        except ValueError:
            return False
        if not _is_safe_read_only_argv(argv):
            return False
    return True


def _is_safe_read_only_argv(argv: tuple[str, ...]) -> bool:
    """按命令类型判断 argv 是否属于无需审批的只读探索。"""

    if not argv:
        return False
    if argv[0] in SAFE_READ_ONLY_COMMANDS:
        return all(_is_safe_read_only_arg(arg) for arg in argv[1:])
    if argv[0] == "sort":
        return all(arg in SAFE_SORT_OPTIONS for arg in argv[1:])
    if argv[0] == "find":
        return _is_safe_find_args(argv)
    if argv[0] == "git":
        return _is_safe_git_args(argv)
    return False


def _is_safe_find_args(argv: tuple[str, ...]) -> bool:
    """只放行当前工作区内的浅层 find 查询，拒绝 exec/delete/外部路径。"""

    if len(argv) < 2:
        return False

    root = argv[1]
    if root.startswith("-") or _is_external_or_parent_path(root):
        return False

    index = 2
    while index < len(argv):
        option = argv[index]
        if option in SAFE_FIND_FLAG_OPTIONS:
            index += 1
            continue
        if option not in SAFE_FIND_VALUE_OPTIONS or index + 1 >= len(argv):
            return False

        value = argv[index + 1]
        if option in {"-maxdepth", "-mindepth"}:
            if not value.isdigit():
                return False
        elif option == "-type":
            if value not in SAFE_FIND_TYPES:
                return False
        elif option == "-name":
            if not value or "/" in value or _is_external_or_parent_path(value):
                return False
        index += 2
    return True


def _is_safe_git_args(argv: tuple[str, ...]) -> bool:
    """只放行不会改变仓库状态的 Git 查询子命令。"""

    if len(argv) < 2 or argv[1] not in SAFE_GIT_READ_ONLY_SUBCOMMANDS:
        return False

    for arg in argv[2:]:
        if (
            not arg
            or any(char in arg for char in SHELL_META_CHARS)
            or arg in UNSAFE_GIT_OPTIONS
            or arg.startswith(UNSAFE_GIT_OPTION_PREFIXES)
            or _is_external_or_parent_path(arg)
        ):
            return False
    return True


def _is_safe_read_only_arg(arg: str) -> bool:
    """限制自动执行时的参数形态，外部路径和递归/跟随选项继续走审批。"""

    if not arg:
        return False
    if arg in UNSAFE_READ_ONLY_OPTIONS:
        return False
    if _is_external_or_parent_path(arg):
        return False
    return True


def _suggest_prefix_rule(argv: tuple[str, ...]) -> tuple[str, ...] | None:
    if len(argv) < 2:
        return None
    if any(any(char in part for char in SHELL_META_CHARS) for part in argv):
        return None
    if any(_is_external_or_parent_path(part) for part in argv):
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


def _is_external_or_parent_path(arg: str) -> bool:
    return (
        arg.startswith("/")
        or arg.startswith("~")
        or arg == ".."
        or arg.startswith("../")
        or "/../" in arg
        or arg.endswith("/..")
    )

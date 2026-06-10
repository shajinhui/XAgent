"""从 transcript 恢复本会话命令 allowlist。"""

from __future__ import annotations

from typing import Any

from security.exec_policy import ExecPolicyRule
from session.models import TranscriptEvent
from workspace import WorkspaceValidationError


def recover_session_allow_rules(
    events: list[TranscriptEvent],
    current_workspace: dict[str, Any],
) -> tuple[ExecPolicyRule, ...]:
    """恢复与当前 workspace 快照完全一致的 session-scoped 命令 allow 规则。"""

    rules: list[ExecPolicyRule] = []
    seen: set[tuple[str, ...]] = set()
    for event in events:
        if event.type != "permission_decision":
            continue

        payload = event.payload
        if not _is_session_command_allow(payload):
            continue

        event_workspace = payload.get("workspace")
        if not isinstance(event_workspace, dict):
            raise WorkspaceValidationError("permission_decision 缺少 workspace snapshot")
        if event_workspace != current_workspace:
            # 权限批准只在当时的 workspace 边界内有效；恢复时不跨 cwd/additional roots/trust 继承。
            continue

        prefix = _parse_prefix_rule(payload.get("prefix_rule"))
        if prefix in seen:
            continue
        seen.add(prefix)
        rules.append(ExecPolicyRule.allow(*prefix))

    return tuple(rules)


def _is_session_command_allow(payload: dict[str, Any]) -> bool:
    """只恢复 run_command 的 session scope 批准，其他权限决定不参与命令策略。"""

    return (
        payload.get("tool") == "run_command"
        and payload.get("approved") is True
        and payload.get("scope") == "session"
        and payload.get("prefix_rule") is not None
    )


def _parse_prefix_rule(raw_prefix: Any) -> tuple[str, ...]:
    """解析当前 schema 的 prefix_rule，缺失或脏数据直接让恢复失败。"""

    if not isinstance(raw_prefix, list):
        raise WorkspaceValidationError("permission_decision prefix_rule 必须是字符串列表")

    parts: list[str] = []
    for item in raw_prefix:
        if not isinstance(item, str) or not item.strip():
            raise WorkspaceValidationError("permission_decision prefix_rule 包含无效片段")
        parts.append(item.strip())

    if not parts:
        raise WorkspaceValidationError("permission_decision prefix_rule 不能为空")
    return tuple(parts)

"""受 trust gate 保护的项目本地策略配置。

只有用户侧 trust store 标记为 trusted 的项目，才允许读取
`.codex-mini/config.toml`。配置只接受当前明确支持的白名单字段，敏感项和
过宽的 allow 规则会直接拒绝，避免项目文件反向扩大 runtime 权限。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from security.exec_policy import ExecPolicy, ExecPolicyRule
from security.permissions import ApprovalPolicy, NetworkPolicy, PermissionProfile
from workspace.models import WorkspaceTrust, WorkspaceValidationError


PROJECT_CONFIG_RELATIVE_PATH = Path(".codex-mini") / "config.toml"
_ALLOWED_TOP_LEVEL_KEYS = {"permissions", "exec"}
_SENSITIVE_KEYS = {
    "api",
    "api_base",
    "api_key",
    "credential",
    "credential_path",
    "credentials",
    "disable_sandbox",
    "endpoint",
    "hook",
    "hooks",
    "model",
    "model_name",
    "model_provider",
    "models",
    "notifier",
    "notifier_command",
    "provider",
    "sandbox",
}
_BROAD_ALLOW_PREFIX_HEADS = {
    "bash",
    "curl",
    "dd",
    "mkfs",
    "node",
    "python",
    "python3",
    "rm",
    "sh",
    "sudo",
    "wget",
    "zsh",
}


@dataclass(frozen=True)
class ProjectPolicyConfig:
    """从 system defaults 或 trusted project config 得到的运行策略。"""

    source: Literal["defaults", "project_config"] = "defaults"
    config_path: Path | None = None
    permission_profile: PermissionProfile = PermissionProfile.WORKSPACE_WRITE
    approval_policy: ApprovalPolicy = ApprovalPolicy.ASK_BEFORE_MUTATING
    network_policy: NetworkPolicy = NetworkPolicy.RESTRICTED
    exec_policy: ExecPolicy = ExecPolicy()

    def as_dict(self) -> dict[str, Any]:
        """返回可放入 workspace payload / session metadata 的安全摘要。"""

        return {
            "source": self.source,
            "config_path": self.config_path.as_posix() if self.config_path else None,
            "permission_profile": self.permission_profile.value,
            "approval_policy": self.approval_policy.value,
            "network_policy": self.network_policy.value,
            "exec_rule_count": len(self.exec_policy.rules),
        }


def default_project_policy() -> ProjectPolicyConfig:
    """未 trusted 或无配置文件时使用的默认策略。"""

    return ProjectPolicyConfig()


def load_project_policy(project_root: Path, trust: WorkspaceTrust) -> ProjectPolicyConfig:
    """在 trust gate 后读取项目本地策略配置。"""

    if not trust.project_config_enabled:
        return default_project_policy()

    config_path = (project_root / PROJECT_CONFIG_RELATIVE_PATH).resolve()
    if not config_path.exists():
        return default_project_policy()
    if not config_path.is_file():
        raise WorkspaceValidationError(f"项目配置必须是文件: {config_path.as_posix()}")

    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceValidationError(f"项目配置 TOML 解析失败: {config_path.as_posix()}") from exc

    if not isinstance(raw, dict):
        raise WorkspaceValidationError(f"项目配置格式无效: {config_path.as_posix()}")
    _validate_top_level_keys(raw, config_path)
    _reject_sensitive_keys(raw, config_path)

    permissions = _section(raw, "permissions", config_path)
    exec_section = _section(raw, "exec", config_path)
    return ProjectPolicyConfig(
        source="project_config",
        config_path=config_path,
        permission_profile=_parse_permission_profile(permissions.get("profile"), config_path),
        approval_policy=_parse_approval_policy(permissions.get("approval_policy"), config_path),
        network_policy=_parse_network_policy(permissions.get("network"), config_path),
        exec_policy=ExecPolicy(_parse_exec_rules(exec_section.get("rules", []), config_path)),
    )


def _validate_top_level_keys(raw: dict[str, Any], config_path: Path) -> None:
    for key in raw:
        if key not in _ALLOWED_TOP_LEVEL_KEYS:
            raise WorkspaceValidationError(
                f"项目配置包含不支持的顶层字段 {key!r}: {config_path.as_posix()}"
            )


def _reject_sensitive_keys(value: Any, config_path: Path, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                location = ".".join((*path, str(key)))
                raise WorkspaceValidationError(
                    f"项目配置禁止声明敏感字段 {location}: {config_path.as_posix()}"
                )
            _reject_sensitive_keys(child, config_path, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive_keys(item, config_path, (*path, str(index)))


def _section(raw: dict[str, Any], key: str, config_path: Path) -> dict[str, Any]:
    value = raw.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WorkspaceValidationError(f"项目配置 section 必须是对象: {key} ({config_path.as_posix()})")
    return value


def _parse_permission_profile(value: Any, config_path: Path) -> PermissionProfile:
    if value is None:
        return PermissionProfile.WORKSPACE_WRITE
    try:
        profile = PermissionProfile(str(value).strip())
    except ValueError as exc:
        raise WorkspaceValidationError(f"项目配置 permission profile 无效: {value}") from exc
    if profile == PermissionProfile.DANGER_NO_SANDBOX:
        raise WorkspaceValidationError(
            f"项目配置禁止关闭 sandbox: {config_path.as_posix()}"
        )
    return profile


def _parse_approval_policy(value: Any, config_path: Path) -> ApprovalPolicy:
    if value is None:
        return ApprovalPolicy.ASK_BEFORE_MUTATING
    try:
        return ApprovalPolicy(str(value).strip())
    except ValueError as exc:
        raise WorkspaceValidationError(f"项目配置 approval policy 无效: {value}") from exc


def _parse_network_policy(value: Any, config_path: Path) -> NetworkPolicy:
    if value is None:
        return NetworkPolicy.RESTRICTED
    try:
        policy = NetworkPolicy(str(value).strip())
    except ValueError as exc:
        raise WorkspaceValidationError(f"项目配置 network policy 无效: {value}") from exc
    if policy != NetworkPolicy.RESTRICTED:
        raise WorkspaceValidationError(
            f"项目配置不能静默开启网络权限: {config_path.as_posix()}"
        )
    return policy


def _parse_exec_rules(raw_rules: Any, config_path: Path) -> tuple[ExecPolicyRule, ...]:
    if raw_rules in (None, ""):
        return ()
    if not isinstance(raw_rules, list):
        raise WorkspaceValidationError(f"项目配置 exec.rules 必须是列表: {config_path.as_posix()}")

    rules: list[ExecPolicyRule] = []
    for index, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            raise WorkspaceValidationError(f"项目配置 exec.rules[{index}] 必须是对象")
        prefix = _parse_prefix(raw_rule.get("prefix"), config_path, index)
        action = str(raw_rule.get("action") or "ask").strip()
        category = str(raw_rule.get("category") or f"project_config_{action}").strip()
        reason = str(raw_rule.get("reason") or "").strip()
        if action == "allow":
            _validate_allow_prefix(prefix, config_path)
            rules.append(ExecPolicyRule.allow(*prefix, category=category, reason=reason))
        elif action == "ask":
            rules.append(ExecPolicyRule.ask(*prefix, category=category, reason=reason))
        elif action == "deny":
            rules.append(ExecPolicyRule.deny(*prefix, category=category, reason=reason))
        else:
            raise WorkspaceValidationError(f"项目配置 exec rule action 无效: {action}")
    return tuple(rules)


def _parse_prefix(raw_prefix: Any, config_path: Path, index: int) -> tuple[str, ...]:
    if not isinstance(raw_prefix, list):
        raise WorkspaceValidationError(f"项目配置 exec.rules[{index}].prefix 必须是字符串列表")
    prefix: list[str] = []
    for item in raw_prefix:
        if not isinstance(item, str) or not item.strip():
            raise WorkspaceValidationError(f"项目配置 exec.rules[{index}].prefix 包含无效片段")
        prefix.append(item.strip())
    if not prefix:
        raise WorkspaceValidationError(f"项目配置 exec.rules[{index}].prefix 不能为空")
    return tuple(prefix)


def _validate_allow_prefix(prefix: tuple[str, ...], config_path: Path) -> None:
    # trusted project 也不能给 shell wrapper、解释器或单 token 命令直接放行。
    if len(prefix) < 2 or prefix[0] in _BROAD_ALLOW_PREFIX_HEADS:
        raise WorkspaceValidationError(
            f"项目配置包含过宽 exec allow 规则 {' '.join(prefix)}: {config_path.as_posix()}"
        )
    if prefix[:2] == ("npm", "run") and len(prefix) < 3:
        raise WorkspaceValidationError(
            f"项目配置 npm run allow 规则必须具体到脚本名: {config_path.as_posix()}"
        )

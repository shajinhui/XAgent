"""前端权限模式到运行时安全策略的映射。"""

from __future__ import annotations

from security.exec_policy import ExecPolicy
from security.permissions import ApprovalPolicy, NetworkPolicy, PermissionProfile
from workspace.models import PermissionMode, WorkspaceValidationError
from workspace.project_config import ProjectPolicyConfig


def policy_for_permission_mode(
    mode: PermissionMode,
    project_policy: ProjectPolicyConfig,
) -> ProjectPolicyConfig:
    """按用户选择生成当前连接生效的权限策略。"""

    if mode == PermissionMode.REQUEST_APPROVAL:
        return ProjectPolicyConfig(
            source="runtime_mode",
            permission_mode=mode,
            permission_profile=PermissionProfile.WORKSPACE_WRITE,
            approval_policy=ApprovalPolicy.ASK_BEFORE_MUTATING,
            network_policy=NetworkPolicy.RESTRICTED,
            exec_policy=ExecPolicy(),
        )

    if mode == PermissionMode.AUTO_APPROVE:
        return ProjectPolicyConfig(
            source="runtime_mode",
            permission_mode=mode,
            permission_profile=PermissionProfile.WORKSPACE_WRITE,
            approval_policy=ApprovalPolicy.AUTO,
            network_policy=NetworkPolicy.RESTRICTED,
            exec_policy=ExecPolicy(),
        )

    if mode == PermissionMode.FULL_ACCESS:
        return ProjectPolicyConfig(
            source="runtime_mode",
            permission_mode=mode,
            permission_profile=PermissionProfile.DANGER_NO_SANDBOX,
            approval_policy=ApprovalPolicy.AUTO,
            network_policy=NetworkPolicy.ENABLED,
            exec_policy=ExecPolicy(protect_paths=False),
        )

    if mode == PermissionMode.CUSTOM:
        if project_policy.source != "project_config":
            raise WorkspaceValidationError("当前项目没有可用的 config.toml 权限配置")
        return project_policy

    raise WorkspaceValidationError(f"不支持的权限模式: {mode.value}")

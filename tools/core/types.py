"""工具系统共享类型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from processes import ManagedProcessManager
from sandbox.macos_executor import SecureMacOSSandboxExecutor
from security.circuit_breaker import CircuitBreaker
from security.permissions import ApprovalPolicy, FileSystemPolicy, NetworkPolicy, PermissionProfile
from security.policy import SecurityPolicy


@dataclass
class ToolResult:
    """工具执行后的统一返回值。"""

    ok: bool
    content: str
    metadata: Dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolMeta:
    """工具能力元信息，供模型、前端和调度逻辑共同使用。"""

    name: str
    is_read_only: bool
    is_mutating: bool
    supports_parallel: bool
    requires_approval: bool = False


class ToolPermissionError(PermissionError):
    """工具因权限策略无法继续时抛出的结构化异常。"""

    def __init__(self, message: str, metadata: Dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.metadata = metadata or {}


@dataclass
class ToolExecutionContext:
    """每次工具执行共享的项目根目录、安全策略和执行器。"""

    selected_root: Path
    project_root: Path
    current_dir: Path
    session_id: str
    policy: SecurityPolicy
    filesystem_policy: FileSystemPolicy
    network_policy: NetworkPolicy
    permission_profile: PermissionProfile
    approval_policy: ApprovalPolicy
    circuit_breaker: CircuitBreaker
    command_executor: SecureMacOSSandboxExecutor
    process_manager: ManagedProcessManager
    turn_id: str | None = None
    diff_tracker: Any | None = None
    skill_resource_resolver: Any | None = None
    default_test_command: str | None = None
    default_test_source: str | None = None
    default_test_timeout: int = 60

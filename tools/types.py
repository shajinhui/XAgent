from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from security.circuit_breaker import CircuitBreaker
from security.policy import SecurityPolicy
from sandbox.docker_executor import SecureDockerExecutor


@dataclass
class ToolResult:
    ok: bool
    content: str
    metadata: Dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolMeta:
    name: str
    is_read_only: bool
    is_mutating: bool
    supports_parallel: bool
    requires_approval: bool = False


class ToolPermissionError(PermissionError):
    def __init__(self, message: str, metadata: Dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.metadata = metadata or {}


@dataclass
class ToolExecutionContext:
    project_root: Path
    session_id: str
    policy: SecurityPolicy
    circuit_breaker: CircuitBreaker
    docker_executor: SecureDockerExecutor

"""Permission and sandbox context for a turn."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from workspace import WorkspaceContext


DEFAULT_PROTECTED_PATHS = (".env", ".git", ".venv", "__pycache__", ".codex-mini")


@dataclass(frozen=True)
class PermissionsContext:
    """Snapshot of the safety envelope active for a turn."""

    workspace_root: Path
    allowed_roots: tuple[Path, ...]
    protected_paths: tuple[str, ...] = DEFAULT_PROTECTED_PATHS
    approval_policy: str = "ask-before-mutating"
    command_sandbox: str = "macos-seatbelt"
    network_allowed: bool = False

    @classmethod
    def from_workspace(cls, workspace: WorkspaceContext) -> "PermissionsContext":
        return cls(
            workspace_root=workspace.root,
            allowed_roots=tuple(workspace.allowed_roots),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "workspace_root": self.workspace_root.as_posix(),
            "allowed_roots": [path.as_posix() for path in self.allowed_roots],
            "protected_paths": list(self.protected_paths),
            "approval_policy": self.approval_policy,
            "command_sandbox": self.command_sandbox,
            "network_allowed": self.network_allowed,
        }

    def render_fragment(self) -> str:
        return (
            f"Approval policy: {self.approval_policy}\n"
            f"Command sandbox: {self.command_sandbox}\n"
            f"Network allowed: {str(self.network_allowed).lower()}"
        )

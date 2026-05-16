"""Workspace and environment context visible to a turn."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from workspace import WorkspaceContext


@dataclass(frozen=True)
class EnvironmentContext:
    """Stable workspace snapshot for model input and tool execution."""

    root: Path
    current_dir: Path
    display_name: str
    git_root: Path | None
    allowed_roots: tuple[Path, ...]

    @classmethod
    def from_workspace(cls, workspace: WorkspaceContext) -> "EnvironmentContext":
        return cls(
            root=workspace.root,
            current_dir=workspace.current_dir,
            display_name=workspace.display_name,
            git_root=workspace.git_root,
            allowed_roots=tuple(workspace.allowed_roots),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root.as_posix(),
            "current_dir": self.current_dir.as_posix(),
            "display_name": self.display_name,
            "git_root": self.git_root.as_posix() if self.git_root else None,
            "allowed_roots": [path.as_posix() for path in self.allowed_roots],
        }

    def render_fragment(self) -> str:
        """Render a compact model-visible environment summary."""

        git_root = self.git_root.as_posix() if self.git_root else "none"
        return (
            f"Workspace root: {self.root.as_posix()}\n"
            f"Current dir: {self.current_dir.as_posix()}\n"
            f"Git root: {git_root}"
        )

from __future__ import annotations

from pathlib import Path

from session import SessionStore
from workspace.models import WorkspaceContext
from workspace.validator import validate_workspace_path


class WorkspaceManager:
    def __init__(self, default_root: Path) -> None:
        self.default_root = validate_workspace_path(default_root)

    def open(self, raw_path: str | Path | None = None) -> WorkspaceContext:
        root = validate_workspace_path(raw_path or self.default_root)
        return WorkspaceContext(
            root=root,
            current_dir=root,
            display_name=root.name or root.as_posix(),
            git_root=_find_git_root(root),
            allowed_roots=[],
            session_store=SessionStore(project_root=root),
        )


def _find_git_root(path: Path) -> Path | None:
    current = path
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent

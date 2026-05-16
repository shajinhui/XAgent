"""workspace 打开与运行态绑定。"""

from __future__ import annotations

from pathlib import Path

from session import SessionStore
from workspace.models import WorkspaceContext, WorkspaceTrust
from workspace.validator import validate_workspace_path


class WorkspaceManager:
    """负责校验 workspace，并为其创建 SessionStore。"""

    def __init__(self, default_root: Path) -> None:
        self.default_root = validate_workspace_path(default_root)

    def open(self, raw_path: str | Path | None = None) -> WorkspaceContext:
        """打开指定 workspace；未指定时打开默认 workspace。"""

        selected_root = validate_workspace_path(raw_path or self.default_root)
        git_root = _find_git_root(selected_root)
        project_root = git_root or selected_root
        return WorkspaceContext(
            selected_root=selected_root,
            project_root=project_root,
            current_dir=selected_root,
            display_name=selected_root.name or selected_root.as_posix(),
            git_root=git_root,
            trust=WorkspaceTrust.session_only(project_root),
            additional_roots=[],
            session_store=SessionStore(project_root=project_root),
        )


def _find_git_root(path: Path) -> Path | None:
    """向上查找最近的 Git 根目录。"""

    current = path
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent

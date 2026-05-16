"""
对 turn 可见的工作区与环境上下文（中文注释）。

`EnvironmentContext` 是一个不可变的工作区快照，包含路径、显示名称
以及允许的根路径等信息，供模型提示或工具执行时使用。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from workspace import WorkspaceContext


@dataclass(frozen=True)
class EnvironmentContext:
    """用于模型输入和工具执行的稳定工作区快照（不可变）。"""

    selected_root: Path
    project_root: Path
    current_dir: Path
    display_name: str
    git_root: Path | None
    additional_roots: tuple[Dict[str, Any], ...]

    @classmethod
    def from_workspace(cls, workspace: WorkspaceContext) -> "EnvironmentContext":
        return cls(
            selected_root=workspace.selected_root,
            project_root=workspace.project_root,
            current_dir=workspace.current_dir,
            display_name=workspace.display_name,
            git_root=workspace.git_root,
            additional_roots=tuple(root.as_dict() for root in workspace.additional_roots),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "selected_root": self.selected_root.as_posix(),
            "project_root": self.project_root.as_posix(),
            "current_dir": self.current_dir.as_posix(),
            "display_name": self.display_name,
            "git_root": self.git_root.as_posix() if self.git_root else None,
            "additional_roots": list(self.additional_roots),
        }

    def render_fragment(self) -> str:
        """渲染为简洁的模型可见环境片段字符串。"""

        git_root = self.git_root.as_posix() if self.git_root else "none"
        return (
            f"Selected root: {self.selected_root.as_posix()}\n"
            f"Project root: {self.project_root.as_posix()}\n"
            f"Current dir: {self.current_dir.as_posix()}\n"
            f"Git root: {git_root}"
        )

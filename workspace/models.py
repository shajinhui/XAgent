"""workspace 上下文的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from session import SessionStore


class WorkspaceValidationError(ValueError):
    """请求的 workspace 不是安全项目根目录时抛出。"""


@dataclass
class WorkspaceContext:
    """当前 runtime 绑定的工作区边界和运行态依赖。"""

    root: Path
    current_dir: Path
    display_name: str
    git_root: Path | None = None
    allowed_roots: List[Path] = field(default_factory=list)
    session_store: SessionStore | None = None

    def as_dict(self) -> Dict[str, Any]:
        """转换为可发给前端的 workspace payload。"""

        return {
            "root": self.root.as_posix(),
            "current_dir": self.current_dir.as_posix(),
            "display_name": self.display_name,
            "git_root": self.git_root.as_posix() if self.git_root else None,
            "allowed_roots": [path.as_posix() for path in self.allowed_roots],
        }

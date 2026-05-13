from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from session import SessionStore


class WorkspaceValidationError(ValueError):
    """Raised when a requested workspace path is not a safe project root."""


@dataclass
class WorkspaceContext:
    root: Path
    current_dir: Path
    display_name: str
    git_root: Path | None = None
    allowed_roots: List[Path] = field(default_factory=list)
    session_store: SessionStore | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root.as_posix(),
            "current_dir": self.current_dir.as_posix(),
            "display_name": self.display_name,
            "git_root": self.git_root.as_posix() if self.git_root else None,
            "allowed_roots": [path.as_posix() for path in self.allowed_roots],
        }

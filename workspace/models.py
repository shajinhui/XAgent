"""workspace 上下文的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Dict, Literal

from session import SessionStore


class WorkspaceValidationError(ValueError):
    """请求的 workspace 不是安全项目根目录时抛出。"""


class TrustLevel(StrEnum):
    """当前 workspace 的信任级别。"""

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    SESSION_ONLY = "session_only"


@dataclass(frozen=True)
class WorkspaceTrust:
    """项目本地配置与高风险扩展能力的信任状态。"""

    level: TrustLevel
    trust_key: str
    source: Literal["default", "user_config", "session"] = "session"
    project_config_enabled: bool = False

    @classmethod
    def session_only(cls, project_root: Path) -> "WorkspaceTrust":
        """创建第一版默认 trust：允许本会话使用，不加载项目本地高风险配置。"""

        return cls(
            level=TrustLevel.SESSION_ONLY,
            trust_key=project_root.resolve().as_posix(),
            source="session",
            project_config_enabled=False,
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "trust_key": self.trust_key,
            "source": self.source,
            "project_config_enabled": self.project_config_enabled,
        }


@dataclass(frozen=True)
class AdditionalRoot:
    """显式加入 workspace 权限上下文的额外目录。"""

    path: Path
    access: Literal["read", "write"]
    source: Literal["user", "session", "config"] = "user"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path.resolve().as_posix(),
            "access": self.access,
            "source": self.source,
        }


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """可写入 transcript/session metadata 的 workspace 快照。"""

    selected_root: Path
    project_root: Path
    current_dir: Path
    display_name: str
    git_root: Path | None
    trust: WorkspaceTrust
    additional_roots: tuple[AdditionalRoot, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        additional_roots = [root.as_dict() for root in self.additional_roots]
        return {
            "selected_root": self.selected_root.as_posix(),
            "project_root": self.project_root.as_posix(),
            "current_dir": self.current_dir.as_posix(),
            "display_name": self.display_name,
            "git_root": self.git_root.as_posix() if self.git_root else None,
            "trust": self.trust.as_dict(),
            "additional_roots": additional_roots,
        }


@dataclass
class WorkspaceContext:
    """当前 runtime 绑定的工作区边界、项目身份和运行态依赖。"""

    selected_root: Path
    project_root: Path
    current_dir: Path
    display_name: str
    git_root: Path | None = None
    trust: WorkspaceTrust | None = None
    additional_roots: list[AdditionalRoot] = field(default_factory=list)
    session_store: SessionStore | None = None

    def __post_init__(self) -> None:
        self.selected_root = self.selected_root.resolve()
        self.project_root = self.project_root.resolve()
        self.current_dir = self.current_dir.resolve()
        self.git_root = self.git_root.resolve() if self.git_root else None
        if self.trust is None:
            self.trust = WorkspaceTrust.session_only(self.project_root)
        self.additional_roots = list(self.additional_roots)
        if self.selected_root not in self.current_dir.parents and self.current_dir != self.selected_root:
            raise WorkspaceValidationError(f"当前目录必须位于所选工作区内: {self.current_dir}")

    def snapshot(self) -> WorkspaceSnapshot:
        """生成可持久化的 workspace 快照。"""

        assert self.trust is not None
        return WorkspaceSnapshot(
            selected_root=self.selected_root,
            project_root=self.project_root,
            current_dir=self.current_dir,
            display_name=self.display_name,
            git_root=self.git_root,
            trust=self.trust,
            additional_roots=tuple(self.additional_roots),
        )

    def as_dict(self) -> Dict[str, Any]:
        """转换为可发给前端的 workspace payload。"""

        return self.snapshot().as_dict()

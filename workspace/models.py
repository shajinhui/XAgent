"""workspace 上下文的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Literal

from session import SessionStore

if TYPE_CHECKING:
    from workspace.project_config import ProjectPolicyConfig


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

    @classmethod
    def from_dict(cls, raw: Any, expected_project_root: Path) -> "WorkspaceTrust":
        """从当前 workspace snapshot 解析信任状态，缺失或旧格式直接失败。"""

        if not isinstance(raw, dict):
            raise WorkspaceValidationError("workspace snapshot 缺少字段: trust")

        try:
            level = TrustLevel(str(raw.get("level") or "").strip())
        except ValueError as exc:
            raise WorkspaceValidationError("workspace snapshot trust.level 无效") from exc

        trust_key = str(raw.get("trust_key") or "").strip()
        expected_key = expected_project_root.resolve().as_posix()
        if trust_key != expected_key:
            raise WorkspaceValidationError(
                f"workspace snapshot trust_key 与 project_root 不一致: {trust_key} != {expected_key}"
            )

        source = str(raw.get("source") or "").strip()
        if source not in {"default", "user_config", "session"}:
            raise WorkspaceValidationError("workspace snapshot trust.source 无效")

        enabled = raw.get("project_config_enabled")
        if not isinstance(enabled, bool):
            raise WorkspaceValidationError("workspace snapshot trust.project_config_enabled 必须是布尔值")

        return cls(
            level=level,
            trust_key=trust_key,
            source=source,  # type: ignore[arg-type]
            project_config_enabled=enabled,
        )


@dataclass(frozen=True)
class AdditionalRoot:
    """显式加入 workspace 权限上下文的额外目录。"""

    path: Path
    access: Literal["read", "write"]
    source: Literal["user", "session", "config"] = "user"

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.expanduser().resolve())

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
    project_policy: "ProjectPolicyConfig | None" = None

    def as_dict(self) -> Dict[str, Any]:
        additional_roots = [root.as_dict() for root in self.additional_roots]
        payload = {
            "selected_root": self.selected_root.as_posix(),
            "project_root": self.project_root.as_posix(),
            "current_dir": self.current_dir.as_posix(),
            "display_name": self.display_name,
            "git_root": self.git_root.as_posix() if self.git_root else None,
            "trust": self.trust.as_dict(),
            "additional_roots": additional_roots,
        }
        if self.project_policy is not None:
            payload["policy"] = self.project_policy.as_dict()
        return payload


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
    project_policy: "ProjectPolicyConfig | None" = None

    def __post_init__(self) -> None:
        self.selected_root = self.selected_root.resolve()
        self.project_root = self.project_root.resolve()
        self.current_dir = self.current_dir.resolve()
        self.git_root = self.git_root.resolve() if self.git_root else None
        if self.trust is None:
            self.trust = WorkspaceTrust.session_only(self.project_root)
        self.additional_roots = [
            AdditionalRoot(root.path, root.access, root.source) for root in self.additional_roots
        ]
        self._ensure_authorized_directory(self.current_dir, field_name="当前目录")

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
            project_policy=self.project_policy,
        )

    def as_dict(self) -> Dict[str, Any]:
        """转换为可发给前端的 workspace payload。"""

        return self.snapshot().as_dict()

    def change_current_dir(self, raw_path: str | Path) -> Path:
        """把当前执行目录切到已授权根目录内的真实目录。"""

        target = self._resolve_path(raw_path)
        self._ensure_authorized_directory(target, field_name="当前目录")
        self.current_dir = target
        return self.current_dir

    def add_additional_root(
        self,
        raw_path: str | Path,
        access: Literal["read", "write"],
    ) -> AdditionalRoot:
        """显式加入额外目录，并用最新用户选择覆盖同一路径的旧权限。"""

        if access not in ("read", "write"):
            raise WorkspaceValidationError(f"额外目录权限无效: {access}")

        target = self._resolve_path(raw_path)
        if not target.exists():
            raise WorkspaceValidationError(f"额外目录不存在: {target}")
        if not target.is_dir():
            raise WorkspaceValidationError(f"额外目录必须是目录: {target}")
        if _is_within(target, self.selected_root):
            raise WorkspaceValidationError(f"额外目录已在所选工作区内，无需重复授权: {target}")

        root = AdditionalRoot(target, access, "user")
        self.additional_roots = [
            existing for existing in self.additional_roots if existing.path != root.path
        ]
        self.additional_roots.append(root)
        self._ensure_authorized_directory(self.current_dir, field_name="当前目录")
        return root

    def _resolve_path(self, raw_path: str | Path) -> Path:
        raw_text = str(raw_path).strip()
        if not raw_text:
            raise WorkspaceValidationError("目录路径不能为空")

        candidate = Path(raw_text).expanduser()
        if not candidate.is_absolute():
            candidate = self.current_dir / candidate
        return candidate.resolve()

    def _ensure_authorized_directory(self, path: Path, *, field_name: str) -> None:
        """workspace current_dir 可以进入 selected root 或显式 additional roots。"""

        if not path.exists():
            raise WorkspaceValidationError(f"{field_name}不存在: {path}")
        if not path.is_dir():
            raise WorkspaceValidationError(f"{field_name}必须是目录: {path}")
        if not any(_is_within(path, root) for root in self._authorized_roots()):
            raise WorkspaceValidationError(f"{field_name}必须位于已授权目录内: {path}")

    def _authorized_roots(self) -> tuple[Path, ...]:
        return (self.selected_root, *(root.path for root in self.additional_roots))


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents

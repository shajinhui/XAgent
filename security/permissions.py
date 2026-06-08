"""Workspace filesystem and approval policy primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workspace.models import AdditionalRoot


class PermissionProfile(StrEnum):
    """Coarse-grained runtime capability profile."""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    DANGER_NO_SANDBOX = "danger_no_sandbox"


class ApprovalPolicy(StrEnum):
    """When mutating or risky actions must ask the user."""

    ASK_BEFORE_MUTATING = "ask-before-mutating"
    NEVER = "never"


class NetworkPolicy(StrEnum):
    """Whether sandboxed command execution may use network APIs."""

    RESTRICTED = "restricted"
    ENABLED = "enabled"


DEFAULT_DENY_READ_NAMES = (".env",)
DEFAULT_DENY_WRITE_NAMES = (".env", ".git", ".codex-mini", ".venv", "__pycache__")


@dataclass(frozen=True)
class FileSystemPolicy:
    """Policy for resolving and checking paths against workspace roots."""

    selected_root: Path
    current_dir: Path | None = None
    readable_roots: tuple[Path, ...] = field(default_factory=tuple)
    writable_roots: tuple[Path, ...] = field(default_factory=tuple)
    deny_read_names: tuple[str, ...] = DEFAULT_DENY_READ_NAMES
    deny_write_names: tuple[str, ...] = DEFAULT_DENY_WRITE_NAMES

    def __post_init__(self) -> None:
        selected_root = self.selected_root.expanduser().resolve()
        current_dir = (self.current_dir or selected_root).expanduser().resolve()
        readable_roots = tuple(_dedupe_paths((selected_root, *self.readable_roots)))
        writable_roots = tuple(_dedupe_paths(self.writable_roots))

        if not _is_within_any(current_dir, readable_roots):
            raise ValueError(f"当前目录必须位于可读根目录内: {current_dir}")

        object.__setattr__(self, "selected_root", selected_root)
        object.__setattr__(self, "current_dir", current_dir)
        object.__setattr__(self, "readable_roots", readable_roots)
        object.__setattr__(self, "writable_roots", writable_roots)

    @classmethod
    def workspace_write(
        cls,
        selected_root: Path,
        *,
        current_dir: Path | None = None,
        additional_roots: tuple[AdditionalRoot, ...] | list[AdditionalRoot] = (),
    ) -> "FileSystemPolicy":
        """Allow reading and writing the selected workspace, plus explicit roots."""

        readable_roots: list[Path] = []
        writable_roots: list[Path] = []
        for root in additional_roots:
            readable_roots.append(root.path)
            if root.access == "write":
                writable_roots.append(root.path)
        return cls(
            selected_root=selected_root,
            current_dir=current_dir,
            readable_roots=tuple(readable_roots),
            writable_roots=(selected_root, *tuple(writable_roots)),
        )

    @classmethod
    def read_only(
        cls,
        selected_root: Path,
        *,
        current_dir: Path | None = None,
        additional_roots: tuple[AdditionalRoot, ...] | list[AdditionalRoot] = (),
    ) -> "FileSystemPolicy":
        """Allow reads only; all writes are denied."""

        readable_roots = tuple(root.path for root in additional_roots)
        return cls(
            selected_root=selected_root,
            current_dir=current_dir,
            readable_roots=readable_roots,
            writable_roots=(),
        )

    def resolve_path(self, raw_path: str | Path) -> Path:
        """Resolve a raw path against current_dir and ensure it is in a known root."""

        raw_text = str(raw_path).strip()
        if not raw_text:
            raw_text = "."
        candidate = Path(raw_text).expanduser()
        if not candidate.is_absolute():
            candidate = self.current_dir / candidate
        resolved = candidate.resolve()
        if not _is_within_any(resolved, self.accessible_roots):
            raise ValueError(f"路径越界，禁止访问: {resolved}")
        return resolved

    def resolve_read_path(self, raw_path: str | Path) -> Path:
        """Resolve and verify a readable path."""

        resolved = self.resolve_path(raw_path)
        self.ensure_readable_path(resolved)
        return resolved

    def resolve_write_path(self, raw_path: str | Path) -> Path:
        """Resolve and verify a writable path."""

        resolved = self.resolve_path(raw_path)
        self.ensure_writable_path(resolved)
        return resolved

    def resolve_command_cwd(self, raw_cwd: str | Path | None = None) -> Path:
        """Resolve and verify a shell working directory."""

        resolved = (
            self.current_dir
            if raw_cwd is None or not str(raw_cwd).strip()
            else self.resolve_path(raw_cwd)
        )
        if not resolved.exists():
            raise ValueError(f"命令工作目录不存在: {resolved}")
        if not resolved.is_dir():
            raise ValueError(f"命令工作目录必须是目录: {resolved}")
        self.ensure_writable_path(resolved, operation="命令工作目录")
        return resolved

    def ensure_readable_path(self, path: Path) -> None:
        """Raise if the resolved path is not readable under this policy."""

        resolved = path.resolve()
        if not _is_within_any(resolved, self.readable_roots):
            raise PermissionError(f"路径不在可读范围内: {resolved}")
        if self._matches_denied_name(resolved, self.deny_read_names):
            raise PermissionError(f"受保护路径，禁止读取: {_display_relative(resolved, self.accessible_roots)}")

    def ensure_writable_path(self, path: Path, *, operation: str = "写入") -> None:
        """Raise if the resolved path is not writable under this policy."""

        resolved = path.resolve()
        if not _is_within_any(resolved, self.writable_roots):
            raise PermissionError(f"路径不在可写范围内: {resolved}")
        if self._matches_denied_name(resolved, self.deny_write_names):
            raise PermissionError(
                f"受保护路径，禁止{operation}: {_display_relative(resolved, self.accessible_roots)}"
            )

    @property
    def accessible_roots(self) -> tuple[Path, ...]:
        return tuple(_dedupe_paths((*self.readable_roots, *self.writable_roots)))

    def _matches_denied_name(self, path: Path, denied_names: tuple[str, ...]) -> bool:
        for root in self.accessible_roots:
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            return any(part in denied_names for part in relative.parts)
        return False


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _is_within_any(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(_is_within(path, root) for root in roots)


def _dedupe_paths(paths: tuple[Path, ...]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved not in result:
            result.append(resolved)
    return result


def _display_relative(path: Path, roots: tuple[Path, ...]) -> str:
    for root in roots:
        try:
            return path.relative_to(root).as_posix() or "."
        except ValueError:
            continue
    return path.as_posix()

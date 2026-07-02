"""Patch review 使用的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from collections.abc import Iterable
from typing import Any


class PatchStatus(StrEnum):
    """Patch proposal 在 review 流程中的状态。"""

    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class PatchChangeType(StrEnum):
    """单个文件变更类型。"""

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class PatchFileChange:
    """一个 patch proposal 中的单文件变更。"""

    path: str
    change_type: PatchChangeType
    before: str
    after: str
    unified_diff: str
    additions: int = 0
    deletions: int = 0
    move_path: str | None = None

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("patch file change path is required")
        object.__setattr__(self, "path", self.path.strip())
        object.__setattr__(self, "change_type", PatchChangeType(self.change_type))
        if self.additions < 0 or self.deletions < 0:
            raise ValueError("patch additions/deletions must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        """序列化为可写入 transcript / JSON 文件的结构。"""

        return {
            "path": self.path,
            "change_type": self.change_type.value,
            "before": self.before,
            "after": self.after,
            "unified_diff": self.unified_diff,
            "additions": self.additions,
            "deletions": self.deletions,
            "move_path": self.move_path,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PatchFileChange":
        """从 JSON 结构恢复单文件变更。"""

        return cls(
            path=str(raw["path"]),
            change_type=PatchChangeType(str(raw["change_type"])),
            before=str(raw.get("before", "")),
            after=str(raw.get("after", "")),
            unified_diff=str(raw.get("unified_diff", "")),
            additions=int(raw.get("additions", 0)),
            deletions=int(raw.get("deletions", 0)),
            move_path=str(raw["move_path"]) if raw.get("move_path") is not None else None,
        )


@dataclass(frozen=True)
class PatchProposal:
    """等待用户 review 的 patch proposal。"""

    patch_id: str
    session_id: str
    turn_id: str
    cwd: str
    created_at: float
    status: PatchStatus = PatchStatus.PROPOSED
    summary: str | None = None
    changes: tuple[PatchFileChange, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.patch_id.strip():
            raise ValueError("patch_id is required")
        if not self.session_id.strip():
            raise ValueError("session_id is required")
        if not self.turn_id.strip():
            raise ValueError("turn_id is required")
        if not self.cwd.strip():
            raise ValueError("cwd is required")
        changes = tuple(
            change if isinstance(change, PatchFileChange) else PatchFileChange.from_dict(change)
            for change in self.changes
        )
        object.__setattr__(self, "patch_id", self.patch_id.strip())
        object.__setattr__(self, "session_id", self.session_id.strip())
        object.__setattr__(self, "turn_id", self.turn_id.strip())
        object.__setattr__(self, "cwd", self.cwd.strip())
        object.__setattr__(self, "status", PatchStatus(self.status))
        object.__setattr__(self, "changes", changes)
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def changed_paths(self) -> list[str]:
        """返回用户 review 面板需要展示的文件路径列表。"""

        return [change.path for change in self.changes]

    @property
    def unified_diff(self) -> str:
        """聚合所有文件的 unified diff。"""

        return "".join(change.unified_diff for change in self.changes)

    @property
    def additions(self) -> int:
        """聚合新增行数。"""

        return sum(change.additions for change in self.changes)

    @property
    def deletions(self) -> int:
        """聚合删除行数。"""

        return sum(change.deletions for change in self.changes)

    def with_status(self, status: PatchStatus, **metadata: Any) -> "PatchProposal":
        """返回状态更新后的 proposal，原对象保持不可变。"""

        return self.with_state(status=status, **metadata)

    def with_changes(
        self,
        changes: Iterable[PatchFileChange],
        **metadata: Any,
    ) -> "PatchProposal":
        """返回文件变更集合更新后的 proposal，原对象保持不可变。"""

        return self.with_state(changes=changes, **metadata)

    def with_state(
        self,
        *,
        status: PatchStatus | None = None,
        changes: Iterable[PatchFileChange] | None = None,
        **metadata: Any,
    ) -> "PatchProposal":
        """同时更新状态、变更集合和 metadata。"""

        next_metadata = dict(self.metadata)
        next_metadata.update(metadata)
        return PatchProposal(
            patch_id=self.patch_id,
            session_id=self.session_id,
            turn_id=self.turn_id,
            cwd=self.cwd,
            created_at=self.created_at,
            status=self.status if status is None else status,
            summary=self.summary,
            changes=self.changes if changes is None else tuple(changes),
            metadata=next_metadata,
        )

    def as_dict(self) -> dict[str, Any]:
        """序列化为可持久化结构。"""

        return {
            "patch_id": self.patch_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "status": self.status.value,
            "summary": self.summary,
            "changes": [change.as_dict() for change in self.changes],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PatchProposal":
        """从 JSON 结构恢复 patch proposal。"""

        return cls(
            patch_id=str(raw["patch_id"]),
            session_id=str(raw["session_id"]),
            turn_id=str(raw["turn_id"]),
            cwd=str(raw["cwd"]),
            created_at=float(raw["created_at"]),
            status=PatchStatus(str(raw.get("status", PatchStatus.PROPOSED.value))),
            summary=str(raw["summary"]) if raw.get("summary") is not None else None,
            changes=tuple(PatchFileChange.from_dict(item) for item in raw.get("changes", [])),
            metadata=dict(raw.get("metadata") or {}),
        )

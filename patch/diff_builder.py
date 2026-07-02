"""Patch proposal 的 diff 构造工具。"""

from __future__ import annotations

import difflib
import time
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from patch.models import PatchChangeType, PatchFileChange, PatchProposal


def build_unified_diff(
    path: str,
    before: str,
    after: str,
    *,
    existed_before: bool = True,
    exists_after: bool = True,
    move_path: str | None = None,
) -> str:
    """根据 before/after 文本生成 review 用 unified diff。"""

    fromfile, tofile = _diff_labels(path, existed_before, exists_after, move_path)
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )


def build_file_change(
    path: str,
    before: str,
    after: str,
    *,
    existed_before: bool = True,
    exists_after: bool = True,
    move_path: str | None = None,
) -> PatchFileChange:
    """构造单文件变更，并计算增删行统计。"""

    clean_path = _normalize_display_path(path)
    change_type = _change_type(existed_before, exists_after)
    unified_diff = build_unified_diff(
        clean_path,
        before,
        after,
        existed_before=existed_before,
        exists_after=exists_after,
        move_path=move_path,
    )
    additions, deletions = count_changed_lines(unified_diff)
    return PatchFileChange(
        path=clean_path,
        change_type=change_type,
        before=before,
        after=after,
        unified_diff=unified_diff,
        additions=additions,
        deletions=deletions,
        move_path=_normalize_display_path(move_path) if move_path else None,
    )


def build_patch_proposal(
    *,
    session_id: str,
    turn_id: str,
    cwd: str | Path,
    changes: Iterable[PatchFileChange],
    patch_id: str | None = None,
    summary: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    created_at: float | None = None,
) -> PatchProposal:
    """从单文件变更集合构造待 review 的 patch proposal。"""

    materialized_changes = tuple(changes)
    if not materialized_changes:
        raise ValueError("patch proposal must contain at least one file change")
    return PatchProposal(
        patch_id=patch_id or str(uuid.uuid4()),
        session_id=session_id,
        turn_id=turn_id,
        cwd=Path(cwd).as_posix(),
        created_at=time.time() if created_at is None else created_at,
        summary=summary,
        changes=materialized_changes,
        metadata=dict(metadata or {}),
    )


def count_changed_lines(unified_diff: str) -> tuple[int, int]:
    """从 unified diff 中统计新增/删除行，忽略文件头。"""

    additions = 0
    deletions = 0
    for line in unified_diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def _change_type(existed_before: bool, exists_after: bool) -> PatchChangeType:
    if not existed_before and exists_after:
        return PatchChangeType.ADD
    if existed_before and not exists_after:
        return PatchChangeType.DELETE
    if existed_before and exists_after:
        return PatchChangeType.UPDATE
    raise ValueError("patch change cannot represent a file absent before and after")


def _diff_labels(
    path: str,
    existed_before: bool,
    exists_after: bool,
    move_path: str | None,
) -> tuple[str, str]:
    # 统一成类 git 标签，前端无需理解绝对路径也能稳定展示。
    before_label = f"a/{path}" if existed_before else "/dev/null"
    after_target = _normalize_display_path(move_path) if move_path else path
    after_label = f"b/{after_target}" if exists_after else "/dev/null"
    return before_label, after_label


def _normalize_display_path(path: str | Path | None) -> str:
    value = Path(path or "").as_posix().strip()
    if value in {"", "."}:
        raise ValueError("patch file path is required")
    while value.startswith("./"):
        value = value[2:]
    return value

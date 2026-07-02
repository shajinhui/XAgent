"""应用或拒绝 pending patch proposal 的工具。"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, Field

from patch import PatchChangeType, PatchFileChange, PatchProposal, PatchStatus, PatchStore
from security.permissions import PermissionProfile
from tools.core.types import ToolExecutionContext, ToolMeta, ToolResult


APPLY_META = ToolMeta(
    name="apply_patch",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


REJECT_META = ToolMeta(
    name="reject_patch",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


ROLLBACK_META = ToolMeta(
    name="rollback_patch",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


class ApplyPatchArgs(BaseModel):
    """apply_patch 工具参数。"""

    patch_id: str = Field(..., description="要应用的 pending patch proposal id")
    selected_paths: list[str] | None = Field(
        default=None,
        description="可选，只应用这些文件路径；省略时应用整个 patch proposal",
    )
    test_command: str | None = Field(
        default=None,
        description="可选，patch 成功应用后运行的测试命令；省略时不会自动猜测或运行测试",
    )
    test_timeout: int | None = Field(
        default=None,
        ge=1,
        le=600,
        description="可选测试命令超时时间（秒）；省略时使用项目配置或默认 60 秒",
    )


class RejectPatchArgs(BaseModel):
    """reject_patch 工具参数。"""

    patch_id: str = Field(..., description="要拒绝的 pending patch proposal id")
    reason: str = Field("", description="拒绝原因，可选")


class RollbackPatchArgs(BaseModel):
    """rollback_patch 工具参数。"""

    patch_id: str = Field(..., description="要回滚的 patch proposal id")


def apply_schema() -> dict:
    """返回 apply_patch 的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": APPLY_META.name,
            "description": "应用一个已保存的 patch proposal",
            "parameters": ApplyPatchArgs.model_json_schema(),
        },
    }


def reject_schema() -> dict:
    """返回 reject_patch 的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": REJECT_META.name,
            "description": "拒绝一个已保存的 patch proposal",
            "parameters": RejectPatchArgs.model_json_schema(),
        },
    }


def rollback_schema() -> dict:
    """返回 rollback_patch 的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": ROLLBACK_META.name,
            "description": "回滚一个已应用或部分应用过的 patch proposal",
            "parameters": RollbackPatchArgs.model_json_schema(),
        },
    }


def apply_run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    """应用 pending patch，并把状态写回 patch store。"""

    args = ApplyPatchArgs(**payload)
    store = _patch_store(ctx)
    proposal = _load_proposed_patch(store, args.patch_id)

    try:
        selected_changes, remaining_changes = _select_changes(proposal, args.selected_paths)
    except Exception as exc:
        return ToolResult(
            ok=False,
            content=f"应用 patch 失败: {exc}",
            metadata={
                "patch_id": proposal.patch_id,
                "patch_status": proposal.status.value,
            },
        )

    changed_paths = [change.path for change in selected_changes]
    written_paths: list[str] = []
    try:
        resolved_changes = _resolve_changes(ctx, selected_changes)
        _preflight_git_apply(ctx, selected_changes)
        for index, (change, target_path, source_path) in enumerate(resolved_changes):
            try:
                if change.change_type in {PatchChangeType.ADD, PatchChangeType.UPDATE}:
                    _write_change(ctx, change, target_path, source_path)
                elif change.change_type == PatchChangeType.DELETE:
                    _delete_change(ctx, target_path)
                else:
                    raise ValueError(f"unsupported patch change type: {change.change_type}")
            except Exception as exc:
                return _apply_failure_result(
                    store,
                    proposal,
                    exc,
                    changed_paths=changed_paths,
                    written_paths=written_paths,
                    failed_path=_change_runtime_path(change),
                    remaining_paths=[
                        _change_runtime_path(pending_change)
                        for pending_change, _target_path, _source_path in resolved_changes[index + 1 :]
                    ],
                )
            written_paths.append(_change_runtime_path(change))
    except Exception as exc:
        return _apply_failure_result(
            store,
            proposal,
            exc,
            changed_paths=changed_paths,
            written_paths=written_paths,
        )

    applied_paths = [change.path for change in selected_changes]
    if remaining_changes:
        original_changes = _original_changes(proposal)
        remaining = proposal.with_changes(
            remaining_changes,
            partial_apply=True,
            partially_applied_at=time.time(),
            applied_paths=_merge_paths(proposal.metadata.get("applied_paths"), applied_paths),
            remaining_paths=[change.path for change in remaining_changes],
            applied_changes=_merge_patch_changes(
                proposal.metadata.get("applied_changes"),
                selected_changes,
                original_changes=original_changes,
            ),
            original_changes=[change.as_dict() for change in original_changes],
        )
        store.save(remaining)
        metadata = {
            "patch_id": remaining.patch_id,
            "patch_status": remaining.status.value,
            "partial_apply": True,
            "applied_paths": applied_paths,
            "remaining_paths": remaining.changed_paths,
            "changed_paths": remaining.changed_paths,
            "additions": remaining.additions,
            "deletions": remaining.deletions,
        }
        test_result = _run_post_apply_test(ctx, args, proposal.patch_id, applied_paths)
        if test_result is not None:
            remaining = remaining.with_changes(
                remaining.changes,
                test_results=[
                    *list(remaining.metadata.get("test_results") or []),
                    test_result,
                ],
            )
            store.save(remaining)
            metadata.update(_test_metadata(test_result))

        return ToolResult(
            ok=True,
            content=_append_test_summary(
                _format_partial_patch_result(proposal.patch_id, applied_paths, remaining),
                test_result,
            ),
            metadata=metadata,
        )

    applied_changes = _original_changes(proposal)
    test_result = _run_post_apply_test(ctx, args, proposal.patch_id, applied_paths)
    applied = proposal.with_state(
        status=PatchStatus.APPLIED,
        changes=applied_changes,
        applied_at=time.time(),
        applied_paths=_merge_paths(proposal.metadata.get("applied_paths"), applied_paths),
        remaining_paths=[],
        partial_apply=False,
        applied_changes=[change.as_dict() for change in applied_changes],
        original_changes=[change.as_dict() for change in applied_changes],
        **({"test_result": test_result} if test_result is not None else {}),
    )
    store.save(applied)
    metadata = {
        "patch_id": applied.patch_id,
        "patch_status": applied.status.value,
        "changed_paths": applied.changed_paths,
        "additions": applied.additions,
        "deletions": applied.deletions,
    }
    if test_result is not None:
        metadata.update(_test_metadata(test_result))
    return ToolResult(
        ok=True,
        content=_append_test_summary(_format_patch_result("已应用 patch", applied), test_result),
        metadata=metadata,
    )


def reject_run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    """拒绝 pending patch，并保留拒绝原因。"""

    args = RejectPatchArgs(**payload)
    store = _patch_store(ctx)
    proposal = _load_proposed_patch(store, args.patch_id)
    rejected = proposal.with_status(
        PatchStatus.REJECTED,
        rejected_at=time.time(),
        reason=args.reason.strip(),
    )
    store.save(rejected)
    return ToolResult(
        ok=True,
        content=_format_patch_result("已拒绝 patch", rejected),
        metadata={
            "patch_id": rejected.patch_id,
            "patch_status": rejected.status.value,
            "changed_paths": rejected.changed_paths,
        },
    )


def rollback_run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    """回滚已应用或部分应用过的 patch。"""

    args = RollbackPatchArgs(**payload)
    store = _patch_store(ctx)
    proposal = _load_rollback_candidate(store, args.patch_id)
    rollback_changes, restore_status, restored_changes = _rollback_plan(proposal)

    rolled_back_changes: list[PatchFileChange] = []
    rolled_back_paths: list[str] = []
    try:
        resolved_changes = _resolve_rollback_changes(ctx, rollback_changes)
        _preflight_git_apply(ctx, rollback_changes, reverse=True)
        for index, (change, restore_path, current_path) in enumerate(resolved_changes):
            try:
                _rollback_change(ctx, change, restore_path, current_path)
            except Exception as exc:
                return _rollback_failure_result(
                    store,
                    proposal,
                    exc,
                    rolled_back_paths=rolled_back_paths,
                    rollback_paths=[_change_runtime_path(change) for change in rollback_changes],
                    failed_path=_change_runtime_path(change),
                    remaining_paths=[
                        _change_runtime_path(pending_change)
                        for pending_change, _restore_path, _current_path in resolved_changes[index + 1 :]
                    ],
                    rolled_back_changes=rolled_back_changes,
                    rollback_changes=rollback_changes,
                )
            rolled_back_changes.append(change)
            rolled_back_paths.append(_change_runtime_path(change))
    except Exception as exc:
        return _rollback_failure_result(
            store,
            proposal,
            exc,
            rolled_back_paths=rolled_back_paths,
            rollback_paths=[_change_runtime_path(change) for change in rollback_changes],
            rolled_back_changes=rolled_back_changes,
            rollback_changes=rollback_changes,
        )

    metadata = {
        "rolled_back": True,
        "rolled_back_at": time.time(),
        "rolled_back_paths": [change.move_path or change.path for change in rollback_changes],
        "partial_apply": False,
        "applied_paths": [],
        "remaining_paths": [change.path for change in restored_changes],
        "applied_changes": [],
        "rollback_source_status": proposal.status.value,
    }
    if restore_status == PatchStatus.PROPOSED:
        metadata["original_changes"] = [change.as_dict() for change in restored_changes]

    rolled_back = proposal.with_state(
        status=restore_status,
        changes=restored_changes,
        **metadata,
    )
    store.save(rolled_back)
    return ToolResult(
        ok=True,
        content=_format_patch_result(
            "已回滚 patch 并恢复待审查" if restore_status == PatchStatus.PROPOSED else "已回滚 patch",
            rolled_back,
        ),
        metadata={
            "patch_id": rolled_back.patch_id,
            "patch_status": rolled_back.status.value,
            "changed_paths": rolled_back.changed_paths,
            "rolled_back": True,
            "rolled_back_paths": metadata["rolled_back_paths"],
            "rollback_source_status": proposal.status.value,
            "partial_reopen": restore_status == PatchStatus.PROPOSED,
            "additions": rolled_back.additions,
            "deletions": rolled_back.deletions,
        },
    )


def _patch_store(ctx: ToolExecutionContext) -> PatchStore:
    return PatchStore(ctx.project_root / ".codex-mini" / "patches")


def _load_proposed_patch(store: PatchStore, patch_id: str) -> PatchProposal:
    proposal = store.load(patch_id)
    if proposal is None:
        raise FileNotFoundError(f"patch proposal 不存在: {patch_id}")
    if proposal.status != PatchStatus.PROPOSED:
        raise ValueError(f"patch proposal 当前状态不是 proposed: {proposal.status.value}")
    return proposal


def _load_rollback_candidate(store: PatchStore, patch_id: str) -> PatchProposal:
    proposal = store.load(patch_id)
    if proposal is None:
        raise FileNotFoundError(f"patch proposal 不存在: {patch_id}")
    if proposal.status == PatchStatus.APPLIED:
        return proposal
    if proposal.status == PatchStatus.PROPOSED and _is_partial_apply_proposal(proposal):
        return proposal
    if proposal.status == PatchStatus.ROLLED_BACK:
        raise ValueError(f"patch proposal 已回滚: {proposal.patch_id}")
    raise ValueError(f"patch proposal 当前状态不支持 rollback: {proposal.status.value}")


def _apply_failure_result(
    store: PatchStore,
    proposal: PatchProposal,
    exc: Exception,
    *,
    changed_paths: list[str],
    written_paths: list[str] | None = None,
    failed_path: str | None = None,
    remaining_paths: list[str] | None = None,
) -> ToolResult:
    stage = _failure_stage_for_exception(exc)
    safe_written_paths = [path for path in (written_paths or []) if str(path).strip()]
    safe_remaining_paths = (
        [path for path in remaining_paths if str(path).strip()]
        if remaining_paths is not None
        else list(changed_paths)
    )
    partially_written = bool(safe_written_paths) or (stage == "write" and bool(failed_path))
    store.save(
        proposal.with_status(
            PatchStatus.FAILED,
            failed_at=time.time(),
            failure=str(exc),
            failure_stage=stage,
            failed_paths=list(changed_paths),
            written_paths=safe_written_paths,
            failed_path=failed_path,
            remaining_paths=safe_remaining_paths,
            partially_written=partially_written,
        )
    )
    return ToolResult(
        ok=False,
        content=_format_patch_failure_result(
            "应用 patch 失败",
            exc,
            failure_stage=stage,
            completed_label="已写入",
            completed_paths=safe_written_paths,
            failed_path=failed_path,
            remaining_paths=safe_remaining_paths,
        ),
        metadata={
            "patch_id": proposal.patch_id,
            "patch_status": PatchStatus.FAILED.value,
            "failure_stage": stage,
            "changed_paths": list(changed_paths),
            "written_paths": safe_written_paths,
            "failed_path": failed_path,
            "remaining_paths": safe_remaining_paths,
            "partially_written": partially_written,
        },
    )


def _rollback_failure_result(
    store: PatchStore,
    proposal: PatchProposal,
    exc: Exception,
    *,
    rolled_back_paths: list[str],
    rollback_paths: list[str],
    failed_path: str | None = None,
    remaining_paths: list[str] | None = None,
    rolled_back_changes: list[PatchFileChange] | None = None,
    rollback_changes: list[PatchFileChange] | None = None,
) -> ToolResult:
    stage = _failure_stage_for_exception(exc)
    safe_rolled_back_paths = [path for path in rolled_back_paths if str(path).strip()]
    safe_remaining_paths = (
        [path for path in remaining_paths if str(path).strip()]
        if remaining_paths is not None
        else list(rollback_paths)
    )
    partially_rolled_back = bool(safe_rolled_back_paths) or (stage == "write" and bool(failed_path))
    next_proposal = proposal
    if proposal.status == PatchStatus.PROPOSED and _is_partial_apply_proposal(proposal):
        next_proposal = _reconcile_partial_apply_after_rollback_failure(
            proposal,
            rolled_back_changes=rolled_back_changes or [],
            rollback_changes=rollback_changes or [],
        )
    store.save(
        next_proposal.with_state(
            rollback_failed_at=time.time(),
            rollback_failure=str(exc),
            rollback_failure_stage=stage,
            rollback_paths=safe_rolled_back_paths,
            rollback_failed_path=failed_path,
            rollback_remaining_paths=safe_remaining_paths,
            rollback_partially_written=partially_rolled_back,
        )
    )
    return ToolResult(
        ok=False,
        content=_format_patch_failure_result(
            "回滚 patch 失败",
            exc,
            failure_stage=stage,
            completed_label="已回滚",
            completed_paths=safe_rolled_back_paths,
            failed_path=failed_path,
            remaining_paths=safe_remaining_paths,
        ),
        metadata={
            "patch_id": proposal.patch_id,
            "patch_status": next_proposal.status.value,
            "rolled_back_paths": safe_rolled_back_paths,
            "failure_stage": stage,
            "failed_path": failed_path,
            "remaining_paths": safe_remaining_paths,
            "partially_written": partially_rolled_back,
        },
    )


def _failure_stage_for_exception(exc: Exception) -> str:
    stage = getattr(exc, "failure_stage", None)
    if isinstance(stage, str) and stage.strip():
        return stage
    return "write"


def _is_partial_apply_proposal(proposal: PatchProposal) -> bool:
    return bool(proposal.metadata.get("partial_apply")) and bool(
        _metadata_patch_changes(proposal.metadata.get("applied_changes"))
    )


def _reconcile_partial_apply_after_rollback_failure(
    proposal: PatchProposal,
    *,
    rolled_back_changes: list[PatchFileChange],
    rollback_changes: list[PatchFileChange],
) -> PatchProposal:
    """回滚 partial apply 中途失败后，重建仍可审查/仍已应用的 proposal 状态。"""

    if not rolled_back_changes:
        return proposal

    original_changes = _original_changes(proposal)
    reopened_review = _metadata_patch_changes(
        _merge_patch_changes(
            [change.as_dict() for change in proposal.changes],
            rolled_back_changes,
            original_changes=original_changes,
        )
    )
    rolled_back_keys = {_patch_change_key(change) for change in rolled_back_changes}
    remaining_applied = [
        change for change in rollback_changes if _patch_change_key(change) not in rolled_back_keys
    ]
    return proposal.with_state(
        changes=reopened_review,
        partial_apply=bool(remaining_applied),
        applied_paths=[change.path for change in remaining_applied],
        remaining_paths=[change.path for change in reopened_review],
        applied_changes=[change.as_dict() for change in remaining_applied],
        original_changes=[change.as_dict() for change in original_changes],
    )


def _rollback_plan(
    proposal: PatchProposal,
) -> tuple[list[PatchFileChange], PatchStatus, tuple[PatchFileChange, ...]]:
    if proposal.status == PatchStatus.APPLIED:
        return list(proposal.changes), PatchStatus.ROLLED_BACK, proposal.changes

    applied_changes = _metadata_patch_changes(proposal.metadata.get("applied_changes"))
    if not applied_changes:
        raise ValueError("patch proposal 没有可回滚的已应用变更")
    original_changes = _original_changes(proposal)
    return applied_changes, PatchStatus.PROPOSED, original_changes


def _original_changes(proposal: PatchProposal) -> tuple[PatchFileChange, ...]:
    materialized = _metadata_patch_changes(proposal.metadata.get("original_changes"))
    return materialized or proposal.changes


def _metadata_patch_changes(value: object) -> tuple[PatchFileChange, ...]:
    if not isinstance(value, list):
        return ()
    changes: list[PatchFileChange] = []
    for item in value:
        if isinstance(item, PatchFileChange):
            changes.append(item)
        elif isinstance(item, dict):
            changes.append(PatchFileChange.from_dict(item))
    return tuple(changes)


def _resolve_changes(
    ctx: ToolExecutionContext,
    changes: list[PatchFileChange],
) -> list[tuple[PatchFileChange, Path, Path | None]]:
    resolved = []
    for change in changes:
        target_raw = change.move_path or change.path
        _ensure_supported_patch_path(ctx, target_raw, stage="validate")
        target_path = ctx.policy.resolve_write_path(target_raw)
        source_path = None
        if change.move_path:
            _ensure_supported_patch_path(ctx, change.path, stage="validate")
            source_path = ctx.policy.resolve_write_path(change.path)
            if source_path != target_path and target_path.exists():
                _raise_patch_error("validate", f"move_path 目标文件已存在，不能覆盖: {target_path}")
        if target_path.exists() and target_path.is_dir():
            raise IsADirectoryError(f"目标路径是目录，不能应用 patch: {target_path}")
        if source_path is not None and source_path.exists() and source_path.is_dir():
            raise IsADirectoryError(f"源路径是目录，不能应用 patch: {source_path}")
        _ensure_text_file_supported(target_path, stage="validate")
        if source_path is not None:
            _ensure_text_file_supported(source_path, stage="validate")
        resolved.append((change, target_path, source_path))
    return resolved


def _resolve_rollback_changes(
    ctx: ToolExecutionContext,
    changes: list[PatchFileChange],
) -> list[tuple[PatchFileChange, Path, Path | None]]:
    resolved = []
    for change in changes:
        _ensure_supported_patch_path(ctx, change.path, stage="rollback_validate")
        restore_path = ctx.policy.resolve_write_path(change.path)
        current_path = None
        if change.move_path:
            _ensure_supported_patch_path(ctx, change.move_path, stage="rollback_validate")
            current_path = ctx.policy.resolve_write_path(change.move_path)
            if current_path != restore_path and restore_path.exists():
                _raise_patch_error(
                    "rollback_validate",
                    f"回滚源路径已存在，不能覆盖: {restore_path}",
                )
        if restore_path.exists() and restore_path.is_dir():
            raise IsADirectoryError(f"目标路径是目录，不能回滚 patch: {restore_path}")
        if current_path is not None and current_path.exists() and current_path.is_dir():
            raise IsADirectoryError(f"当前路径是目录，不能回滚 patch: {current_path}")
        _ensure_text_file_supported(restore_path, stage="rollback_validate")
        if current_path is not None:
            _ensure_text_file_supported(current_path, stage="rollback_validate")
        resolved.append((change, restore_path, current_path))
    return resolved


def _select_changes(
    proposal: PatchProposal,
    selected_paths: list[str] | None,
) -> tuple[list[PatchFileChange], list[PatchFileChange]]:
    """按 selected_paths 划分本次要应用的变更和继续等待 review 的变更。"""

    if selected_paths is None:
        return list(proposal.changes), []

    normalized_selection = {_normalize_patch_path(path) for path in selected_paths}
    if not normalized_selection:
        raise ValueError("selected_paths 不能为空")

    selected_changes: list[PatchFileChange] = []
    remaining_changes: list[PatchFileChange] = []
    matched_paths: set[str] = set()
    for change in proposal.changes:
        keys = {change.path}
        if change.move_path:
            keys.add(change.move_path)
        matched = keys & normalized_selection
        if matched:
            selected_changes.append(change)
            matched_paths.update(matched)
        else:
            remaining_changes.append(change)

    missing_paths = sorted(normalized_selection - matched_paths)
    if missing_paths:
        raise ValueError("selected_paths 包含不属于 patch 的文件: " + ", ".join(missing_paths))
    if not selected_changes:
        raise ValueError("selected_paths 未匹配任何 patch 文件")
    return selected_changes, remaining_changes


def _preflight_git_apply(
    ctx: ToolExecutionContext,
    changes: list[PatchFileChange],
    *,
    reverse: bool = False,
) -> None:
    diff_text = "".join(change.unified_diff for change in changes)
    if not diff_text.strip():
        return
    if any(Path(change.path).is_absolute() or (change.move_path and Path(change.move_path).is_absolute()) for change in changes):
        return

    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ctx.project_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return

    if probe.returncode != 0:
        return

    command = ["git", "apply", "--check", "--verbose"]
    if reverse:
        command.append("--reverse")
    result = subprocess.run(
        command,
        cwd=ctx.project_root,
        check=False,
        input=diff_text,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return

    output = "\n".join(
        part.strip()
        for part in (result.stdout, result.stderr)
        if isinstance(part, str) and part.strip()
    ).strip()
    _raise_patch_error(
        "preflight",
        f"git apply --check 失败{': ' + output if output else ''}",
    )


def _ensure_supported_patch_path(
    ctx: ToolExecutionContext,
    raw_path: str,
    *,
    stage: str,
) -> None:
    candidate = _candidate_workspace_path(ctx, raw_path)
    stop_roots = {
        root.resolve()
        for root in (
            *ctx.filesystem_policy.accessible_roots,
            ctx.project_root,
            ctx.selected_root,
        )
    }
    for path in (candidate, *candidate.parents):
        if path == path.parent:
            break
        if not path.exists():
            continue
        if path.is_symlink():
            _raise_patch_error(stage, f"patch 不支持 symlink 路径: {candidate}")
        if path.resolve() in stop_roots:
            break


def _candidate_workspace_path(ctx: ToolExecutionContext, raw_path: str) -> Path:
    candidate = Path(str(raw_path or "").strip()).expanduser()
    if not candidate.is_absolute():
        candidate = ctx.current_dir / candidate
    return candidate


def _ensure_text_file_supported(path: Path, *, stage: str) -> None:
    if not path.exists() or path.is_dir():
        return
    raw = path.read_bytes()
    if b"\x00" in raw:
        _raise_patch_error(stage, f"patch 不支持 binary 文件: {path}")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        _raise_patch_error(stage, f"patch 只支持 UTF-8 文本文件: {path}")


def _raise_patch_error(stage: str, message: str) -> None:
    exc = ValueError(message)
    setattr(exc, "failure_stage", stage)
    raise exc


def _rollback_change(
    ctx: ToolExecutionContext,
    change: PatchFileChange,
    restore_path: Path,
    current_path: Path | None,
) -> None:
    active_path = current_path or restore_path
    if change.change_type == PatchChangeType.ADD:
        _delete_existing_file(ctx, active_path, missing_ok=False)
        return

    if change.change_type == PatchChangeType.DELETE:
        _write_text(ctx, restore_path, change.before, extra_baselines=[current_path])
        return

    if change.change_type == PatchChangeType.UPDATE:
        _write_text(ctx, restore_path, change.before, extra_baselines=[current_path])
        if current_path is not None and current_path != restore_path and current_path.exists():
            _delete_existing_file(ctx, current_path, missing_ok=False)
        return

    raise ValueError(f"unsupported patch change type: {change.change_type}")


def _write_change(
    ctx: ToolExecutionContext,
    change: PatchFileChange,
    target_path: Path,
    source_path: Path | None,
) -> None:
    if ctx.diff_tracker:
        ctx.diff_tracker.save_baseline(str(target_path))
        if source_path is not None:
            ctx.diff_tracker.save_baseline(str(source_path))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(change.after, encoding="utf-8")
    if source_path is not None and source_path != target_path and source_path.exists():
        source_path.unlink()


def _write_text(
    ctx: ToolExecutionContext,
    target_path: Path,
    content: str,
    *,
    extra_baselines: list[Path | None] | None = None,
) -> None:
    if ctx.diff_tracker:
        ctx.diff_tracker.save_baseline(str(target_path))
        for extra_path in extra_baselines or []:
            if extra_path is not None:
                ctx.diff_tracker.save_baseline(str(extra_path))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")


def _delete_change(ctx: ToolExecutionContext, target_path: Path) -> None:
    _delete_existing_file(ctx, target_path, missing_ok=False)


def _delete_existing_file(ctx: ToolExecutionContext, target_path: Path, *, missing_ok: bool) -> None:
    if not target_path.exists():
        if missing_ok:
            return
        raise FileNotFoundError(f"要删除的文件不存在: {target_path}")
    if target_path.is_dir():
        raise IsADirectoryError(f"目标路径是目录，不能删除: {target_path}")
    if ctx.diff_tracker:
        ctx.diff_tracker.save_baseline(str(target_path))
    target_path.unlink()


def _format_patch_result(prefix: str, proposal: PatchProposal) -> str:
    paths = "\n".join(f"- {path}" for path in proposal.changed_paths)
    return f"{prefix}: {proposal.patch_id}\n{paths}"


def _format_partial_patch_result(
    patch_id: str,
    applied_paths: list[str],
    remaining: PatchProposal,
) -> str:
    applied = "\n".join(f"- {path}" for path in applied_paths)
    pending = "\n".join(f"- {path}" for path in remaining.changed_paths)
    return f"已应用部分 patch: {patch_id}\n已应用:\n{applied}\n仍待审查:\n{pending}"


def _run_post_apply_test(
    ctx: ToolExecutionContext,
    args: ApplyPatchArgs,
    patch_id: str,
    changed_paths: list[str],
) -> dict | None:
    """在 patch 成功写入后运行显式测试命令；默认完全不猜测。"""

    explicit_command = (args.test_command or "").strip()
    configured_command = (ctx.default_test_command or "").strip()
    command = explicit_command or configured_command
    if not command:
        return None
    timeout = args.test_timeout or ctx.default_test_timeout or 60
    source = "explicit" if explicit_command else (ctx.default_test_source or "project_config")

    started_at = time.time()
    base_result = {
        "patch_id": patch_id,
        "command": command,
        "timeout": timeout,
        "source": source,
        "changed_paths": list(changed_paths),
        "started_at": started_at,
    }

    # apply_patch 的批准覆盖这条显式测试命令，但危险命令/受保护路径仍由 ExecPolicy 拒绝。
    decision = ctx.policy.check_command(command, approved=True)
    if not decision.allowed:
        return {
            **base_result,
            "ok": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "output": decision.reason or "测试命令被安全策略拒绝",
            "duration_seconds": time.time() - started_at,
            "error": decision.reason or "测试命令被安全策略拒绝",
            "blocked": True,
            "category": decision.category,
        }

    try:
        result = ctx.command_executor.run(
            command,
            filesystem_policy=ctx.filesystem_policy,
            network_policy=ctx.network_policy,
            timeout_seconds=timeout,
            cwd=ctx.current_dir,
            sandbox_enabled=ctx.permission_profile != PermissionProfile.DANGER_NO_SANDBOX,
        )
    except Exception as exc:
        return {
            **base_result,
            "ok": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "output": str(exc),
            "duration_seconds": time.time() - started_at,
            "error": str(exc),
            "blocked": False,
            "category": "test_execution_error",
        }

    stdout = _truncate_test_output(result.stdout)
    stderr = _truncate_test_output(result.stderr)
    output = _truncate_test_output((result.stdout + result.stderr).strip())
    return {
        **base_result,
        "ok": bool(result.ok),
        "exit_code": result.exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "output": output,
        "duration_seconds": time.time() - started_at,
        "blocked": False,
        "category": "test_command",
    }


def _test_metadata(test_result: dict) -> dict:
    """生成 transcript / patch lifecycle 事件里便于前端读取的测试摘要。"""

    return {
        "test_result": test_result,
        "test_command": test_result.get("command"),
        "test_ok": test_result.get("ok"),
        "test_exit_code": test_result.get("exit_code"),
        "test_output": test_result.get("output"),
        "test_source": test_result.get("source"),
    }


def _append_test_summary(content: str, test_result: dict | None) -> str:
    """把测试结果追加到工具返回文本中，便于 final answer 直接引用。"""

    if test_result is None:
        return content

    ok = bool(test_result.get("ok"))
    lines = [
        content,
        "",
        "测试:",
        f"- 命令: {test_result.get('command')}",
        f"- 结果: {'✓ 通过' if ok else '✗ 失败'}",
        f"- 退出码: {test_result.get('exit_code')}",
    ]
    output = str(test_result.get("output") or "").strip()
    if not ok and output:
        lines.extend(["- 失败输出:", output])
    return "\n".join(lines)


def _truncate_test_output(value: str, limit: int = 12000) -> str:
    """限制测试输出进入 metadata/transcript 的体积。"""

    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return value[:limit] + f"\n...[truncated {omitted} chars]"


def _normalize_patch_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("selected_paths 不能包含空路径")
    value = Path(raw).as_posix()
    while value.startswith("./"):
        value = value[2:]
    return value


def _merge_patch_changes(
    existing: object,
    changes: list[PatchFileChange],
    *,
    original_changes: tuple[PatchFileChange, ...] | None = None,
) -> list[dict]:
    """合并 partial apply 已应用变更，保持原始 patch 顺序。"""

    merged: dict[str, PatchFileChange] = {}
    for change in _metadata_patch_changes(existing):
        merged[_patch_change_key(change)] = change
    for change in changes:
        merged[_patch_change_key(change)] = change

    if original_changes:
        ordered: list[PatchFileChange] = []
        for change in original_changes:
            key = _patch_change_key(change)
            if key in merged:
                ordered.append(merged.pop(key))
        ordered.extend(merged.values())
    else:
        ordered = list(merged.values())
    return [change.as_dict() for change in ordered]


def _patch_change_key(change: PatchFileChange) -> str:
    return f"{change.path}->{change.move_path or ''}"


def _change_runtime_path(change: PatchFileChange) -> str:
    return change.move_path or change.path


def _merge_paths(existing: object, paths: list[str]) -> list[str]:
    """合并历史已应用路径和本次路径，保持顺序且去重。"""

    merged: list[str] = []
    if isinstance(existing, list):
        merged.extend(str(path) for path in existing if str(path).strip())
    merged.extend(paths)
    return list(dict.fromkeys(merged))


def _format_patch_failure_result(
    prefix: str,
    exc: Exception,
    *,
    failure_stage: str,
    completed_label: str,
    completed_paths: list[str],
    failed_path: str | None,
    remaining_paths: list[str],
) -> str:
    lines = [f"{prefix}: {exc}", f"失败阶段: {failure_stage}"]
    if completed_paths:
        lines.append(f"{completed_label}:")
        lines.extend(f"- {path}" for path in completed_paths)
    if failed_path:
        lines.append("失败文件:")
        lines.append(f"- {failed_path}")
    if remaining_paths:
        lines.append("未触及:")
        lines.extend(f"- {path}" for path in remaining_paths)
    return "\n".join(lines)

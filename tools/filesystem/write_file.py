"""写入 workspace 内文件的 mutating 工具。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from patch import PatchStore, build_file_change, build_patch_proposal
from tools.core.types import ToolExecutionContext, ToolMeta, ToolResult


META = ToolMeta(
    name="write_file",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


class WriteFileArgs(BaseModel):
    """write_file 工具入参。"""

    path: str = Field(..., description="要写入的文件路径")
    content: str = Field(..., description="写入内容")
    append: bool = Field(False, description="是否以追加模式写入")
    dry_run: bool = Field(True, description="默认只生成预览 diff；显式为 false 时才直接写入")


def schema() -> dict:
    """返回供模型调用的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "生成文件写入预览（支持覆盖或追加）；默认不直接写入",
            "parameters": WriteFileArgs.model_json_schema(),
        },
    }


def run(ctx: ToolExecutionContext, payload: dict) -> str | ToolResult:
    """写入文件；调用方必须先通过工具审批。"""

    args = WriteFileArgs(**payload)
    path = ctx.policy.resolve_write_path(args.path)
    if path.exists() and path.is_dir():
        raise IsADirectoryError(f"目标路径是目录，不能写入文件: {path}")

    existed_before = path.exists()
    before = path.read_text(encoding="utf-8", errors="replace") if existed_before else ""
    after = f"{before}{args.content}" if args.append else args.content
    if args.dry_run:
        change = build_file_change(
            _display_path(ctx, path),
            before,
            after,
            existed_before=existed_before,
            exists_after=True,
        )
        proposal = build_patch_proposal(
            session_id=ctx.session_id,
            turn_id=ctx.turn_id or "system",
            cwd=ctx.current_dir,
            changes=[change],
            summary=f"write_file preview: {change.path}",
            metadata={"tool": META.name, "append": args.append},
        )
        PatchStore(ctx.project_root / ".codex-mini" / "patches").save(proposal)
        return ToolResult(
            ok=True,
            content=change.unified_diff or f"无变更: {path}",
            metadata={
                "dry_run": True,
                "patch_id": proposal.patch_id,
                "patch_status": proposal.status.value,
                "path": path.as_posix(),
                "append": args.append,
                "change_type": change.change_type.value,
                "additions": change.additions,
                "deletions": change.deletions,
            },
        )

    # baseline 必须在写入前保存，turn 结束后才能准确展示本轮变更。
    if ctx.diff_tracker:
        ctx.diff_tracker.save_baseline(str(path))

    path.parent.mkdir(parents=True, exist_ok=True)

    mode = "a" if args.append else "w"
    with path.open(mode, encoding="utf-8") as f:
        f.write(args.content)

    return f"已写入文件: {path}"


def _display_path(ctx: ToolExecutionContext, path: Path) -> str:
    """尽量使用项目相对路径，让 diff header 对桌面端更友好。"""

    try:
        return path.relative_to(ctx.project_root).as_posix()
    except ValueError:
        return path.as_posix()

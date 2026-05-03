from __future__ import annotations

from pydantic import BaseModel, Field

from tools.types import ToolExecutionContext, ToolMeta


META = ToolMeta(
    name="edit_file",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


class EditFileArgs(BaseModel):
    path: str = Field(..., description="要编辑的文件路径")
    start_line: int = Field(..., ge=1, description="起始行（1-based）")
    end_line: int = Field(..., ge=1, description="结束行（1-based, 包含）")
    replacement: str = Field(..., description="替换文本")


def schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "按行范围替换文件内容",
            "parameters": EditFileArgs.model_json_schema(),
        },
    }


def run(ctx: ToolExecutionContext, payload: dict) -> str:
    args = EditFileArgs(**payload)
    path = ctx.policy.resolve_path(args.path)
    ctx.policy.ensure_writable_path(path)
    if not path.exists() or path.is_dir():
        raise FileNotFoundError(f"文件不存在或不可编辑: {path}")

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    if args.start_line > args.end_line:
        raise ValueError("start_line 不能大于 end_line")
    if args.end_line > len(lines):
        raise ValueError(f"行范围越界: 文件总行数 {len(lines)}")

    replacement_lines = [line + "\n" for line in args.replacement.split("\n")]
    lines[args.start_line - 1 : args.end_line] = replacement_lines
    path.write_text("".join(lines), encoding="utf-8")

    return f"已编辑文件: {path} (lines {args.start_line}-{args.end_line})"

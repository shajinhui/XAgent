from __future__ import annotations

from pydantic import BaseModel, Field

from tools.types import ToolExecutionContext, ToolMeta


META = ToolMeta(
    name="write_file",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


class WriteFileArgs(BaseModel):
    path: str = Field(..., description="要写入的文件路径")
    content: str = Field(..., description="写入内容")
    append: bool = Field(False, description="是否以追加模式写入")


def schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入文件内容（支持覆盖或追加）",
            "parameters": WriteFileArgs.model_json_schema(),
        },
    }


def run(ctx: ToolExecutionContext, payload: dict) -> str:
    args = WriteFileArgs(**payload)
    path = ctx.policy.resolve_path(args.path)
    ctx.policy.ensure_writable_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    mode = "a" if args.append else "w"
    with path.open(mode, encoding="utf-8") as f:
        f.write(args.content)

    return f"已写入文件: {path}"

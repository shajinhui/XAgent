from __future__ import annotations

from pydantic import BaseModel, Field

from tools.types import ToolExecutionContext, ToolMeta


META = ToolMeta(
    name="read_file",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=True,
)


class ReadFileArgs(BaseModel):
    path: str = Field(..., description="要读取的文件路径（相对项目根目录或绝对路径）")


def schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定文件内容",
            "parameters": ReadFileArgs.model_json_schema(),
        },
    }


def run(ctx: ToolExecutionContext, payload: dict) -> str:
    args = ReadFileArgs(**payload)
    path = ctx.policy.resolve_path(args.path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if path.is_dir():
        raise IsADirectoryError(f"目标是目录，不是文件: {path}")
    return path.read_text(encoding="utf-8", errors="replace")

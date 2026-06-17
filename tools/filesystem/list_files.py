"""列出目录文件工具。"""
from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, Field
from tools.core.types import ToolExecutionContext, ToolMeta


META = ToolMeta(
    name="list_files",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=True,
)


class ListFilesArgs(BaseModel):
    """list_files 工具入参。"""

    directory: str = Field(
        default=".",
        description="目录路径，默认当前目录",
    )
    pattern: str = Field(
        default="**/*",
        description="文件匹配模式。示例: *.py (当前目录Python文件), **/*.js (所有JS文件), src/**/*.ts (src下所有TS文件)",
    )
    max_files: int = Field(
        default=100,
        description="最多返回文件数，默认 100",
    )


def schema() -> dict:
    """返回供模型调用的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": META.name,
            "description": "列出 workspace 内指定目录下的文件",
            "parameters": ListFilesArgs.model_json_schema(),
        },
    }


def run(ctx: ToolExecutionContext, payload: dict) -> str:
    """列出目录下的文件。

    Args:
        ctx: 工具执行上下文，负责 workspace 边界校验
        payload: 模型传入的工具参数

    Returns:
        文件列表，每行一个文件路径
    """
    args = ListFilesArgs(**payload)
    try:
        path = ctx.policy.resolve_read_path(args.directory)
        if not path.exists():
            return f"错误: 目录不存在: {args.directory}"

        if not path.is_dir():
            return f"错误: 不是目录: {args.directory}"

        files = [str(f.relative_to(path)) for f in path.glob(args.pattern) if f.is_file()]

        if not files:
            return f"未找到匹配 '{args.pattern}' 的文件"

        if len(files) > args.max_files:
            return (
                f"找到 {len(files)} 个文件，只显示前 {args.max_files} 个:\n\n"
                + "\n".join(files[: args.max_files])
                + f"\n\n... 还有 {len(files) - args.max_files} 个文件未显示"
            )

        return "\n".join(files)

    except Exception as e:
        return f"错误: {str(e)}"

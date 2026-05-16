"""在 workspace 内执行文本搜索的只读工具。"""

from __future__ import annotations

import subprocess

from pydantic import BaseModel, Field

from tools.core.types import ToolExecutionContext, ToolMeta


META = ToolMeta(
    name="grep",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=True,
)


class GrepArgs(BaseModel):
    """grep 工具入参。"""

    pattern: str = Field(..., description="搜索关键词或正则")
    path: str = Field(".", description="搜索路径（默认项目根）")
    max_count: int = Field(200, ge=1, le=1000, description="最大返回行数")


def schema() -> dict:
    """返回供模型调用的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "在项目内执行文本搜索（优先使用 ripgrep）",
            "parameters": GrepArgs.model_json_schema(),
        },
    }


def run(ctx: ToolExecutionContext, payload: dict) -> str:
    """优先使用 ripgrep 搜索，缺失时回退到系统 grep。"""

    args = GrepArgs(**payload)
    search_root = ctx.policy.resolve_path(args.path)

    rg_cmd = ["rg", "-n", "--max-count", str(args.max_count), args.pattern, str(search_root)]
    grep_cmd = ["grep", "-R", "-n", args.pattern, str(search_root)]

    try:
        proc = subprocess.run(rg_cmd, capture_output=True, text=True, cwd=ctx.project_root)
    except FileNotFoundError:
        # 一些极简环境没有 rg，保留 grep 回退保证工具可用。
        proc = subprocess.run(grep_cmd, capture_output=True, text=True, cwd=ctx.project_root)

    out = proc.stdout.strip()
    err = proc.stderr.strip()
    return (
        f"exit_code: {proc.returncode}\n"
        f"stdout:\n{out or '(empty)'}\n"
        f"stderr:\n{err or '(empty)'}"
    )

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Tuple

from pydantic import BaseModel, Field


class ReadFileArgs(BaseModel):
    """读取文件工具参数。"""

    path: str = Field(..., description="要读取的文件路径（相对项目根目录或绝对路径）")


class RunCommandArgs(BaseModel):
    """执行命令工具参数。"""

    command: str = Field(..., description="要执行的 shell 命令")
    timeout: int = Field(15, ge=1, le=120, description="超时时间（秒）")


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def read_file(project_root: Path, path: str) -> str:
    """读取项目内文件内容，禁止越界访问。"""

    resolved = _resolve_path(project_root, path)
    if project_root not in resolved.parents and resolved != project_root:
        raise ValueError(f"路径越界，禁止访问: {resolved}")
    if not resolved.exists():
        raise FileNotFoundError(f"文件不存在: {resolved}")
    if resolved.is_dir():
        raise IsADirectoryError(f"目标是目录，不是文件: {resolved}")

    return resolved.read_text(encoding="utf-8", errors="replace")


def run_command(project_root: Path, command: str, timeout: int = 15) -> str:
    """在项目根目录执行命令并返回输出。"""

    result = subprocess.run(
        ["sh", "-lc", command],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    return (
        f"exit_code: {result.returncode}\n"
        f"stdout:\n{stdout or '(empty)'}\n"
        f"stderr:\n{stderr or '(empty)'}"
    )


def get_tool_schemas() -> list[dict[str, Any]]:
    """生成符合 OpenAI tools 格式的工具 schema。"""

    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取指定文件内容",
                "parameters": ReadFileArgs.model_json_schema(),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "在项目目录执行 shell 命令",
                "parameters": RunCommandArgs.model_json_schema(),
            },
        },
    ]


def execute_tool_call(project_root: Path, name: str, arguments: str) -> Tuple[bool, str]:
    """执行单次工具调用，返回 (是否成功, 输出文本)。"""

    try:
        payload = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        return False, f"工具参数不是合法 JSON: {exc}"

    try:
        if name == "read_file":
            args = ReadFileArgs(**payload)
            content = read_file(project_root=project_root, path=args.path)
            return True, content
        if name == "run_command":
            args = RunCommandArgs(**payload)
            output = run_command(
                project_root=project_root,
                command=args.command,
                timeout=args.timeout,
            )
            return True, output
        return False, f"未知工具: {name}"
    except Exception as exc:  # pragma: no cover
        return False, f"工具执行失败: {exc}"

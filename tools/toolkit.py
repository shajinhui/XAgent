"""兼容层：阶段 2 起推荐使用 tools.registry.ToolRegistry。"""

from __future__ import annotations

from pathlib import Path

from tools.registry import ToolRegistry


def get_tool_schemas(project_root: Path | None = None) -> list[dict]:
    root = project_root or Path(__file__).resolve().parents[1]
    return ToolRegistry(root).schemas()


def execute_tool_call(project_root: Path, name: str, arguments: str) -> tuple[bool, str]:
    result = ToolRegistry(project_root).execute(name=name, arguments=arguments)
    return result.ok, result.content

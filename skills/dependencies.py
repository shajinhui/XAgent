"""Skill dependencies 展示检查。"""

from __future__ import annotations

from typing import Any


def dependency_status(dependencies: Any, available_tools: set[str]) -> dict[str, Any] | None:
    """生成只用于展示的依赖状态；不会安装或启用任何工具。"""

    tools = _tool_dependencies(dependencies)
    if not tools:
        return None
    entries = [{"name": name, "available": name in available_tools} for name in tools]
    missing = [entry["name"] for entry in entries if not entry["available"]]
    return {
        "tools": entries,
        "missing_tools": missing,
    }


def _tool_dependencies(dependencies: Any) -> tuple[str, ...]:
    if not isinstance(dependencies, dict):
        return ()
    raw_tools = dependencies.get("tools")
    if not isinstance(raw_tools, list):
        return ()
    tools: list[str] = []
    for value in raw_tools:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if name and name not in tools:
            tools.append(name)
    return tuple(tools)

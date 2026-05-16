"""模型 tool call 到内部工具调用的轻量路由解析。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolInvocation:
    name: str
    arguments: str
    call_id: str


class ToolRouter:
    """把 OpenAI/LiteLLM 风格 tool_call 转为内部 ToolInvocation。"""

    @staticmethod
    def build_tool_invocation(tool_call: dict[str, Any]) -> ToolInvocation:
        try:
            fn = tool_call["function"]
            name = str(fn["name"])
        except KeyError as exc:
            raise ValueError("tool_call missing function.name") from exc

        call_id = str(tool_call.get("id") or name)
        arguments = fn.get("arguments", "{}")
        if not isinstance(arguments, str):
            raise ValueError("tool_call function.arguments must be a JSON string")

        return ToolInvocation(name=name, arguments=arguments, call_id=call_id)

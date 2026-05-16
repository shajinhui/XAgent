"""模型 tool call 到内部工具调用的轻量路由解析。"""

from __future__ import annotations

from typing import Any

from tools.core.context import ToolInvocation


class ToolRouter:
    """把 OpenAI/LiteLLM 风格 tool_call 转为内部 ToolInvocation。"""

    @staticmethod
    def build_tool_invocation(
        tool_call: dict[str, Any],
        turn_context: Any | None = None,
    ) -> ToolInvocation:
        try:
            fn = tool_call["function"]
            name = str(fn["name"])
        except KeyError as exc:
            raise ValueError("tool_call missing function.name") from exc

        call_id = str(tool_call.get("id") or name)
        arguments = fn.get("arguments", "{}")
        if not isinstance(arguments, str):
            raise ValueError("tool_call function.arguments must be a JSON string")

        return ToolInvocation.from_model_call(
            name=name,
            arguments=arguments,
            call_id=call_id,
            turn_context=turn_context,
        )

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
        """把模型返回的 `tool_call` 字典转换为内部的 `ToolInvocation` 实例。

        说明：模型层的 tool_call 结构通常包含 `function.name` 与 `function.arguments`，
        其中 `arguments` 在规范中应为 JSON 字符串。本方法校验必需字段并抛出
        可识别的异常，便于上层捕获并记录错误。使用 `@staticmethod` 是因为该函数
        不依赖类或实例状态，仅为一个纯转换工具。
        """

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

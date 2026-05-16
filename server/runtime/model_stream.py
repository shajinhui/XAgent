"""LiteLLM streaming 响应的兼容解析工具。"""

from __future__ import annotations

from typing import Any, Dict, List

from server.protocol.serialization import object_to_dict


def extract_stream_delta(chunk: Any) -> Dict[str, Any]:
    """从不同 SDK 对象形态中提取 choices[0].delta。"""

    chunk_dict = object_to_dict(chunk)
    choices = chunk_dict.get("choices")
    if choices is None:
        choices = getattr(chunk, "choices", [])
    if not choices:
        return {}

    choice = choices[0]
    choice_dict = object_to_dict(choice)
    delta = choice_dict.get("delta")
    if delta is None:
        delta = getattr(choice, "delta", None)
    return object_to_dict(delta)


def merge_tool_call_delta(buffers: Dict[int, Dict[str, Any]], delta: Dict[str, Any]) -> None:
    """把流式 tool_call 分片按 index 拼回完整工具调用。"""

    index = int(delta.get("index", len(buffers)))
    current = buffers.setdefault(
        index,
        {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        },
    )

    if delta.get("id"):
        current["id"] = delta["id"]
    if delta.get("type"):
        current["type"] = delta["type"]

    fn_delta = object_to_dict(delta.get("function"))
    if fn_delta.get("name"):
        current["function"]["name"] += fn_delta["name"]
    if "arguments" in fn_delta:
        current["function"]["arguments"] += fn_delta.get("arguments") or ""


def build_assistant_message(
    content: str,
    tool_call_buffers: Dict[int, Dict[str, Any]],
    reasoning_content: str = "",
) -> Dict[str, Any]:
    """把流式收集结果转换成标准 assistant message。"""

    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    if tool_call_buffers:
        tool_calls: List[Dict[str, Any]] = []
        for index, tool_call in sorted(tool_call_buffers.items()):
            if not tool_call.get("id"):
                tool_call["id"] = f"tool_call_{index}"
            tool_calls.append(tool_call)
        message["tool_calls"] = tool_calls
    return message


def clear_historical_reasoning_content(messages: List[Dict[str, Any]]) -> None:
    """清理无需回传的历史 reasoning_content。

    DeepSeek 工具调用场景要求带 tool_calls 的 assistant reasoning_content 留在上下文中；
    普通 assistant 回复的 reasoning_content 不需要恢复或回传。
    """

    for message in messages:
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            message.pop("reasoning_content", None)

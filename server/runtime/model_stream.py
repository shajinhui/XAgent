"""
LiteLLM 流式响应兼容解析工具（中文注释）。

该模块负责把来自不同 SDK（如 LiteLLM、OpenAI 等）的流式响应统一解析为
可处理的增量 delta、重组流式的工具调用分片，并将收集到的内容构造成标准的
`assistant` 消息字典。目标是提高对多种 SDK 响应形态的容错性与可读性，便于后续
的 turn 逻辑处理与转录记录。
"""

from __future__ import annotations

from typing import Any, Dict, List

from server.protocol.serialization import object_to_dict


def extract_stream_delta(chunk: Any) -> Dict[str, Any]:
    """从流式响应中安全提取 `choices[0].delta` 字段。

    不同 SDK 的响应对象可能是原生 dict、dataclass、pydantic 模型或 SDK 自定义对象，
    因此先使用 `object_to_dict` 做一次通用转换；若字段缺失，再回退到属性访问以提高
    兼容性。最终返回一个 dict（若未找到 delta，则返回空 dict）。
    """

    chunk_dict = object_to_dict(chunk)
    # 优先从 dict 结构读取 choices
    choices = chunk_dict.get("choices")
    # 如果 object_to_dict 没有正确转换（某些 SDK 对象），尝试属性访问备选路径
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
    """把模型流式输出的 `tool_call` 分片按 `index` 合并回完整调用结构。

    模型在流式输出工具调用时可能把 `function.name` 和 `function.arguments` 分片
    发送。本函数维护一个按 index 索引的缓冲字典，并对字段做累加拼接。

    参数:
    - buffers: 共享缓冲区，键是 index，值是部分合成的 tool_call dict
    - delta: 当前接收到的 delta 分片
    """

    index = int(delta.get("index", len(buffers)))
    current = buffers.setdefault(
        index,
        {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        },
    )

    # 更新基本字段（后到的值覆盖先到的非累积字段）
    if delta.get("id"):
        current["id"] = delta["id"]
    if delta.get("type"):
        current["type"] = delta["type"]

    # function 字段通常以增量字符串到达，逐片拼接 name 与 arguments
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
    """把已收集的流式内容构造成标准的 `assistant` 消息字典。

    - `content`：拼接得到的回复文本
    - `tool_call_buffers`：按 index 排序后转换为 `tool_calls` 列表
    - `reasoning_content`：可选的内部推理内容，只在需要保留时传入
    """

    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    if tool_call_buffers:
        tool_calls: List[Dict[str, Any]] = []
        # 保证按 index 顺序输出 tool_calls
        for index, tool_call in sorted(tool_call_buffers.items()):
            if not tool_call.get("id"):
                tool_call["id"] = f"tool_call_{index}"
            tool_calls.append(tool_call)
        message["tool_calls"] = tool_calls
    return message


def clear_historical_reasoning_content(messages: List[Dict[str, Any]]) -> None:
    """清理历史中不应回传的 `reasoning_content` 字段。

    说明：DeepSeek/工具调用场景需要保留带 `tool_calls` 的 assistant 消息的
    `reasoning_content`，以便模型在后续步骤能理解工具选择的上下文；而普通的
    assistant 回复的 `reasoning_content` 不应回传以节省上下文并避免暴露内部思路。
    """

    for message in messages:
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            message.pop("reasoning_content", None)

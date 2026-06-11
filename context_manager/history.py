"""
模型可见的对话历史管理器（中文注释）。

该模块为每次 turn 提供一个轻量的历史容器，用于构建模型输入上下文、
记录用户/助手/工具消息，并提供快照、替换与清理历史中不应回传的
推理内容（`reasoning_content`）的辅助方法。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


class ContextManager:
    """模型可见消息的简单历史管理器。

    该类封装了一系列消息（字典形式），并提供便捷的构造器、追加方法和
    快照导出，用于在 turn 执行中逐步构建与修改模型上下文。
    """

    def __init__(self, messages: Iterable[Dict[str, Any]] | None = None) -> None:
        # 使用浅拷贝减少开销，调用方需确保不修改原始 message
        self._messages: List[Dict[str, Any]] = list(messages) if messages else []

    @classmethod
    def with_system_prompt(cls, system_prompt: str) -> "ContextManager":
        """使用系统提示创建一个带初始 system 消息的 ContextManager。"""
        return cls([{"role": "system", "content": system_prompt}])

    @classmethod
    def from_messages(cls, messages: Iterable[Dict[str, Any]]) -> "ContextManager":
        """从已有消息序列创建 ContextManager（会复制消息）。"""
        return cls(messages)

    @property
    def messages(self) -> List[Dict[str, Any]]:
        """返回内部消息列表（直接引用，注意修改会影响容器）。"""
        return self._messages

    def replace(self, messages: Iterable[Dict[str, Any]]) -> None:
        """用新消息序列替换当前历史（浅拷贝以提升性能）。"""
        self._messages = list(messages)

    def replace_system_prompt(self, system_prompt: str) -> None:
        """更新首条 system 消息；没有 system 消息时插入到历史开头。"""

        if self._messages and self._messages[0].get("role") == "system":
            self._messages[0] = {"role": "system", "content": system_prompt}
            return
        self._messages.insert(0, {"role": "system", "content": system_prompt})

    def append_user_message(self, content: str) -> None:
        """追加一条用户消息。"""
        self._messages.append({"role": "user", "content": content})

    def append_assistant_message(self, message: Dict[str, Any]) -> None:
        """追加一条来自 assistant 的完整消息（允许包含 tool_calls、reasoning_content 等）。"""
        self._messages.append(message)

    def append_tool_result(self, call_id: str, name: str, content: str) -> None:
        """将工具调用的返回结果追加为一条 tool 消息。

        这用于在模型上下文中注入工具的输出，便于模型在后续消息中引用。
        """
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": content,
            }
        )

    def clear_historical_reasoning_content(self) -> None:
        """清理历史消息中不应回传给模型的 `reasoning_content` 字段。

        仅保留带有 `tool_calls` 的 assistant 消息的 `reasoning_content`，
        以便工具调用场景保留必要的推理线索，其他情形下删除以节省上下文。
        """
        clear_historical_reasoning_content(self._messages)

    def snapshot(self) -> List[Dict[str, Any]]:
        """返回当前历史的浅复制快照（每条消息被复制为新字典）。"""
        return [dict(message) for message in self._messages]


def clear_historical_reasoning_content(messages: List[Dict[str, Any]]) -> None:
    """删除不应回传的 `reasoning_content` 字段。

    函数会遍历消息列表并移除所有来自 assistant 且不包含 `tool_calls` 的
    `reasoning_content`，以避免将普通助手的内部推理内容回传给模型。
    """

    for message in messages:
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            message.pop("reasoning_content", None)

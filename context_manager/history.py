"""Model-visible history container."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


class ContextManager:
    """Small history manager for model-visible messages."""

    def __init__(self, messages: Iterable[Dict[str, Any]] | None = None) -> None:
        self._messages: List[Dict[str, Any]] = [dict(message) for message in messages or []]

    @classmethod
    def with_system_prompt(cls, system_prompt: str) -> "ContextManager":
        return cls([{"role": "system", "content": system_prompt}])

    @classmethod
    def from_messages(cls, messages: Iterable[Dict[str, Any]]) -> "ContextManager":
        return cls(messages)

    @property
    def messages(self) -> List[Dict[str, Any]]:
        return self._messages

    def replace(self, messages: Iterable[Dict[str, Any]]) -> None:
        self._messages = [dict(message) for message in messages]

    def append_user_message(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def append_assistant_message(self, message: Dict[str, Any]) -> None:
        self._messages.append(message)

    def append_tool_result(self, call_id: str, name: str, content: str) -> None:
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": content,
            }
        )

    def clear_historical_reasoning_content(self) -> None:
        clear_historical_reasoning_content(self._messages)

    def snapshot(self) -> List[Dict[str, Any]]:
        return [dict(message) for message in self._messages]


def clear_historical_reasoning_content(messages: List[Dict[str, Any]]) -> None:
    """Remove reasoning_content that should not be sent back to the model."""

    for message in messages:
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            message.pop("reasoning_content", None)

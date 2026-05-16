"""Small mutation helpers for ContextManager."""

from __future__ import annotations

from context_manager.history import ContextManager


def append_user_turn(history: ContextManager, content: str) -> None:
    history.append_user_message(content)


def append_tool_result(history: ContextManager, call_id: str, name: str, content: str) -> None:
    history.append_tool_result(call_id, name, content)

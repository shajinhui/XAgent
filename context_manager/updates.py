"""
ContextManager 的小型变更辅助函数（中文注释）。

封装对 ContextManager 的常用变更操作，便于在不同模块中以函数形式调用而
不是直接操作对象方法，从而保持调用处代码简洁。
"""

from __future__ import annotations

from context_manager.history import ContextManager


def append_user_turn(history: ContextManager, content: str) -> None:
    """把用户输入追加到历史中。"""
    history.append_user_message(content)


def append_tool_result(history: ContextManager, call_id: str, name: str, content: str) -> None:
    """把工具结果追加为一条 tool 消息。"""
    history.append_tool_result(call_id, name, content)

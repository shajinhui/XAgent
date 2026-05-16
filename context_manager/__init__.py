"""
模型上下文历史管理模块（中文注释）。

聚合并导出与模型上下文历史管理相关的公共 API，包括 `ContextManager`、
更新辅助函数与截断工具等。
"""

from context_manager.history import ContextManager
from context_manager.history import clear_historical_reasoning_content
from context_manager.truncation import truncate_text
from context_manager.updates import append_tool_result
from context_manager.updates import append_user_turn

__all__ = [
    "ContextManager",
    "append_tool_result",
    "append_user_turn",
    "clear_historical_reasoning_content",
    "truncate_text",
]

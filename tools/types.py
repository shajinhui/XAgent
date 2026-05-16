"""兼容层：工具共享类型已迁移到 tools.core.types。"""

from tools.core.types import (
    ToolExecutionContext,
    ToolMeta,
    ToolPermissionError,
    ToolResult,
)

__all__ = [
    "ToolExecutionContext",
    "ToolMeta",
    "ToolPermissionError",
    "ToolResult",
]

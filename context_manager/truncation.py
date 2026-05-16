"""
上下文截断辅助工具（中文注释）。

在将长文本放入模型上下文时，可能需要对其进行截断以满足长度限制。
此模块提供简单的按字符截断函数，并在截断位置添加提示文本。
"""

from __future__ import annotations


def truncate_text(text: str, max_chars: int, notice: str = "\n...[truncated]") -> str:
    """截断给定文本到 `max_chars` 长度（含 notice 长度），并在末尾追加 notice。"""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(notice))
    return f"{text[:keep]}{notice}"

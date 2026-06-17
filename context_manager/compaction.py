"""上下文压缩模块（中文注释）。

当对话历史超过阈值时，自动压缩前 80% 的内容为摘要，保留最近 20% 的完整对话。
"""
from __future__ import annotations

from typing import Any, Dict, List


def should_compact(messages: List[Dict[str, Any]], threshold_tokens: int = 256000) -> bool:
    """检查是否需要压缩。

    Args:
        messages: 消息列表
        threshold_tokens: 阈值（默认 256k tokens）
    """
    estimated_tokens = sum(_estimate_tokens(msg.get("content", "")) for msg in messages)
    return estimated_tokens > threshold_tokens


def compact_messages(
    messages: List[Dict[str, Any]],
    keep_ratio: float = 0.2,
) -> tuple[List[Dict[str, Any]], str]:
    """压缩消息历史。

    Args:
        messages: 原始消息列表
        keep_ratio: 保留最近消息的比例（默认 0.2 即 20%）

    Returns:
        (压缩后的消息列表, 需要总结的内容)
    """
    if len(messages) <= 2:  # system + 一条消息，不压缩
        return messages, ""

    # 找到 system prompt
    system_msg = messages[0] if messages[0].get("role") == "system" else None
    work_messages = messages[1:] if system_msg else messages

    # 计算分割点：保留最近 20%，压缩前 80%
    split_idx = max(1, int(len(work_messages) * (1 - keep_ratio)))
    to_summarize = work_messages[:split_idx]
    to_keep = work_messages[split_idx:]

    # 构建待总结的内容
    summary_text = _build_summary_text(to_summarize)

    # 构建新消息列表：system + 占位符 + 保留的消息
    new_messages = []
    if system_msg:
        new_messages.append(system_msg)

    # 添加占位符，稍后替换为摘要
    new_messages.append({
        "role": "system",
        "content": "[SUMMARY_PLACEHOLDER]"  # 稍后替换
    })

    new_messages.extend(to_keep)

    return new_messages, summary_text


def _build_summary_text(messages: List[Dict[str, Any]]) -> str:
    """构建待总结的文本。"""
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "user":
            parts.append(f"用户: {content[:500]}")
        elif role == "assistant":
            # 过滤掉 tool_calls，只保留文本回复
            if not msg.get("tool_calls") and content:
                parts.append(f"助手: {content[:500]}")
        elif role == "tool":
            tool_name = msg.get("name", "unknown")
            parts.append(f"工具 {tool_name}: {content[:200]}")

    return "\n\n".join(parts)


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数量。"""
    if not isinstance(text, str):
        text = str(text)
    return len(text) // 4

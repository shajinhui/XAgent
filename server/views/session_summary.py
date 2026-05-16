"""
把 durable session 数据投影成前端历史会话视图。

此模块将持久化会话记录（SessionRecord）与转录事件（TranscriptEvent）转
换为前端可展示的会话摘要与消息列表。主要职责：
- 生成会话摘要（标题、最后更新时间、消息数等）
- 从转录事件中抽取用于前端展示的 user/assistant 消息
- 对澄清问题/回答做格式化输出
"""

from __future__ import annotations

from typing import Any, Dict, List

from server.processors.title_processor import sanitize_conversation_title
from session import SessionRecord, SessionStore, TranscriptEvent


def summarize_session_record(
    record: SessionRecord,
    events: List[TranscriptEvent],
) -> Dict[str, Any]:
    """从 session record 和 transcript events 生成历史列表摘要。"""

    title = record.title or _derive_session_title(events)
    last_message = _derive_last_user_message(events)
    last_message_at = _last_model_message_timestamp(events) or record.updated_at
    return {
        "session_id": record.session_id,
        "title": title,
        "created_at": record.created_at,
        "updated_at": last_message_at,
        "last_turn_id": record.last_turn_id,
        "message_count": _count_model_messages(events),
        "last_message": last_message,
    }


def session_display_messages(events: List[TranscriptEvent]) -> List[Dict[str, Any]]:
    """提取恢复会话时前端可直接展示的 user/assistant 消息。"""

    messages: List[Dict[str, Any]] = []
    for event in events:
        display_message = _display_message_for_event(event)
        if display_message is None:
            continue
        messages.append(display_message)

    return messages


def _display_message_for_event(event: TranscriptEvent) -> Dict[str, Any] | None:
    """将单个转录事件映射为前端可展示的消息结构或返回 None。

    支持的事件类型：
    - `user_message` / `assistant_message`: 直接使用 payload.content
    - `clarification_request`: 把澄清问题作为 assistant 消息显示
    - `clarification_response`: 将澄清回答格式化为 user 消息（支持跳过、选项 id/索引）
    """

    if event.type in {"user_message", "assistant_message"}:
        role = "user" if event.type == "user_message" else "assistant"
        content = str(event.payload.get("content") or "").strip()
        if not content:
            return None
        return {
            "role": role,
            "content": content,
            "timestamp": event.timestamp,
        }

    if event.type == "clarification_request":
        # 澄清请求展示为 assistant 的问题文本
        question = str(event.payload.get("question") or "").strip()
        if not question:
            return None
        return {
            "role": "assistant",
            "content": question,
            "timestamp": event.timestamp,
        }

    if event.type == "clarification_response":
        # 澄清回答需要特殊格式化（支持 skipped / content / choice_id / option_index）
        content = _format_clarification_response(event.payload)
        if not content:
            return None
        return {
            "role": "user",
            "content": content,
            "timestamp": event.timestamp,
        }

    return None


def list_session_summaries(store: SessionStore, limit: int = 20) -> List[Dict[str, Any]]:
    """列出有真实对话内容的 session，避免空会话进入历史列表。"""

    safe_limit = max(1, min(limit, 50))
    summaries: List[Dict[str, Any]] = []
    for record in store.list_sessions(limit=safe_limit, with_turns=True):
        summary = summarize_session_record(record, store.load_events(record.session_id))
        if summary["message_count"] > 0:
            summaries.append(summary)
    summaries.sort(key=lambda summary: (summary["updated_at"], summary["session_id"]), reverse=True)
    return summaries[:safe_limit]


def _derive_session_title(events: List[TranscriptEvent]) -> str:
    """从转录事件尝试推导会话标题：

    优先级：最近的 `conversation_title` -> 首条用户消息内容（sanitize） -> 默认 “新对话”
    """

    for event in reversed(events):
        if event.type != "conversation_title":
            continue
        title = str(event.payload.get("title") or "").strip()
        if title:
            return title

    for event in events:
        if event.type != "user_message":
            continue
        content = str(event.payload.get("content") or "").strip()
        if content:
            return sanitize_conversation_title(content)

    return "新对话"


def _derive_last_user_message(events: List[TranscriptEvent]) -> str:
    """获取最近的一条用户可展示消息（优先澄清回答），并截断到 160 字符用于摘要显示。"""

    for event in reversed(events):
        if event.type == "clarification_response":
            content = _format_clarification_response(event.payload)
            if content:
                return content[:160]
            continue
        if event.type != "user_message":
            continue
        content = str(event.payload.get("content") or "").strip()
        if content:
            return content[:160]
    return ""


def _count_model_messages(events: List[TranscriptEvent]) -> int:
    """统计可展示的消息数（user/assistant/clarification）。"""
    return sum(
        1
        for event in events
        if event.type
        in {"user_message", "assistant_message", "clarification_request", "clarification_response"}
        and _display_message_for_event(event) is not None
    )


def _last_model_message_timestamp(events: List[TranscriptEvent]) -> float | None:
    """返回最近一条模型相关消息的时间戳（或 None）。"""

    for event in reversed(events):
        if event.type in {
            "user_message",
            "assistant_message",
            "clarification_request",
            "clarification_response",
        }:
            return event.timestamp
    return None


def _format_clarification_response(payload: Dict[str, Any]) -> str:
    """把澄清回答的 payload 转换为可显示文本。处理顺序：
    - 如果标记为 skipped，返回占位文本
    - 使用 content 字段
    - 使用 choice_id
    - 使用 option_index
    - 否则返回空字符串
    """
    if bool(payload.get("skipped")):
        return "已跳过澄清问题"

    content = str(payload.get("content") or "").strip()
    if content:
        return content

    choice_id = str(payload.get("choice_id") or "").strip()
    if choice_id:
        return choice_id

    option_index = payload.get("option_index")
    if option_index is not None:
        return f"选择 {option_index}"

    return ""

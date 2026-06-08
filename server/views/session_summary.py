"""
把 durable session 数据投影成前端历史会话视图。

此模块将持久化会话记录（SessionRecord）与转录事件（TranscriptEvent）转
换为前端可展示的会话摘要与消息列表。主要职责：
- 生成会话摘要（标题、最后更新时间、消息数等）
- 从转录事件中抽取用于前端展示的 user/assistant 消息
- 对澄清问题/回答做格式化输出
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    """提取恢复会话时前端可直接展示的消息。

    除了 user/assistant 文本，这里也会从 transcript 里的工具事件重建
    “已处理 + 工具调用轨迹”。注意这里不恢复模型私有 reasoning_content，
    只展示已持久化的状态摘要和真实工具调用结果。
    """

    messages: List[Dict[str, Any]] = []
    activity_groups: Dict[str, _ActivityGroupState] = {}
    for event in events:
        if _is_activity_event(event):
            _upsert_activity_event(messages, activity_groups, event)
            continue

        display_message = _display_message_for_event(event)
        if display_message is None:
            continue
        if event.type == "assistant_message":
            activity_key = _activity_group_key(event)
            if activity_key in activity_groups:
                display_message["activity_key"] = activity_key
        messages.append(display_message)

    _finish_activity_groups(activity_groups)
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


@dataclass
class _ActivityGroupState:
    """恢复历史消息时临时维护一次 turn 的工具轨迹。"""

    key: str
    message: Dict[str, Any]
    event_indices: Dict[str, int] = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0


def _is_activity_event(event: TranscriptEvent) -> bool:
    """判断事件是否应该恢复为前端 activity 轨迹。"""

    return event.type in {
        "tool_call_started",
        "permission_request",
        "permission_decision",
        "tool_call_result",
    }


def _upsert_activity_event(
    messages: List[Dict[str, Any]],
    groups: Dict[str, _ActivityGroupState],
    event: TranscriptEvent,
) -> None:
    """按 request_id 模拟前端实时 upsert，避免恢复后显示重复的运行中状态。"""

    group = _ensure_activity_group(messages, groups, event)
    activity_message = _activity_event_message(group.key, event)
    if activity_message is None:
        return

    request_id = activity_message["step"].get("requestId") or event.event_id
    existing_index = group.event_indices.get(request_id)
    if existing_index is None:
        group.event_indices[request_id] = len(messages)
        messages.append(activity_message)
    else:
        messages[existing_index] = activity_message

    group.finished_at = max(group.finished_at, event.timestamp)


def _ensure_activity_group(
    messages: List[Dict[str, Any]],
    groups: Dict[str, _ActivityGroupState],
    event: TranscriptEvent,
) -> _ActivityGroupState:
    """为同一个 turn 创建或复用一条可折叠的 activity 汇总消息。"""

    key = _activity_group_key(event)
    existing = groups.get(key)
    if existing is not None:
        return existing

    started_at_ms = int(event.timestamp * 1000)
    message: Dict[str, Any] = {
        "role": "activity",
        "content": "已处理",
        "collapsed": True,
        "activity_key": key,
        "startedAt": started_at_ms,
        "finishedAt": started_at_ms,
        "timestamp": event.timestamp,
    }
    group = _ActivityGroupState(
        key=key,
        message=message,
        started_at=event.timestamp,
        finished_at=event.timestamp,
    )
    groups[key] = group
    messages.append(message)
    return group


def _finish_activity_groups(groups: Dict[str, _ActivityGroupState]) -> None:
    """给恢复出的 activity 汇总补上耗时文本和结束时间。"""

    for group in groups.values():
        finished_at = max(group.finished_at, group.started_at)
        group.message["finishedAt"] = int(finished_at * 1000)
        group.message["content"] = f"已处理 {_format_elapsed_time(group.started_at, finished_at)}"


def _activity_group_key(event: TranscriptEvent) -> str:
    """生成前后端都能使用的稳定 activity 分组键。"""

    turn_id = str(event.payload.get("turn_id") or "").strip()
    if turn_id:
        return f"turn:{turn_id}"

    request_id = str(event.payload.get("request_id") or "").strip()
    if request_id:
        return f"request:{request_id}"

    return f"event:{event.event_id}"


def _activity_event_message(activity_key: str, event: TranscriptEvent) -> Dict[str, Any] | None:
    """把工具/权限 transcript 事件转换成前端 activity_event 消息。"""

    payload = event.payload
    tool_name = str(payload.get("tool") or "").strip() or "tool"
    request_id = str(payload.get("request_id") or event.event_id).strip()

    if event.type == "tool_call_started":
        arguments = payload.get("arguments")
        label = _format_tool_start_label(tool_name, arguments)
        status = "running"
        kind = _tool_kind(tool_name)
        detail = _format_detail(arguments)
    elif event.type == "permission_request":
        label = f"等待权限确认：{tool_name}"
        status = "waiting"
        kind = "permission"
        detail = str(payload.get("detail") or "").strip() or _format_detail(payload.get("arguments"))
    elif event.type == "permission_decision":
        approved = bool(payload.get("approved"))
        label = "权限已允许" if approved else "权限已拒绝"
        status = "success" if approved else "error"
        kind = "permission"
        detail = str(payload.get("feedback") or "").strip()
    elif event.type == "tool_call_result":
        ok = bool(payload.get("ok"))
        label = _format_tool_result_label(tool_name, ok)
        status = "success" if ok else "error"
        kind = _tool_kind(tool_name)
        detail = str(payload.get("content") or "").strip()
    else:
        return None

    step: Dict[str, Any] = {
        "label": label,
        "status": status,
        "kind": kind,
        "requestId": request_id,
    }
    if detail:
        step["detail"] = detail

    return {
        "role": "activity_event",
        "content": label,
        "activity_key": activity_key,
        "step": step,
        "timestamp": event.timestamp,
    }


def _tool_kind(tool_name: str) -> str:
    """将后端工具名映射到前端已支持的 activity 图标类型。"""

    if tool_name == "grep":
        return "search"
    if tool_name == "read_file":
        return "read"
    if tool_name in {"write_file", "edit_file"}:
        return "edit"
    if tool_name == "run_command":
        return "command"
    if tool_name == "web_fetch":
        return "web"
    return "tool"


def _format_tool_start_label(tool_name: str, raw_arguments: Any) -> str:
    """复用桌面端文案规则，恢复历史时不再显示裸工具名。"""

    args = _parse_arguments(raw_arguments)
    path = args.get("path") if isinstance(args.get("path"), str) else ""

    if tool_name == "grep":
        return f"正在探索 {path}" if path else "正在探索项目"
    if tool_name == "read_file":
        return f"正在读取 {path}" if path else "正在读取文件"
    if tool_name in {"write_file", "edit_file"}:
        return f"正在编辑 {path}" if path else "正在编辑文件"
    if tool_name == "run_command":
        return "正在准备运行命令"
    if tool_name == "web_fetch":
        return "正在获取网页"
    return f"正在调用 {tool_name}"


def _format_tool_result_label(tool_name: str, ok: bool) -> str:
    """恢复历史工具结果时使用与实时 UI 一致的中文摘要。"""

    if not ok:
        return {
            "grep": "探索失败",
            "read_file": "读取失败",
            "write_file": "编辑失败",
            "edit_file": "编辑失败",
            "run_command": "命令失败",
            "web_fetch": "获取失败",
        }.get(tool_name, f"工具失败：{tool_name}")

    return {
        "grep": "已探索 1 组结果",
        "read_file": "已读取 1 个文件",
        "write_file": "已编辑 1 个文件",
        "edit_file": "已编辑 1 个文件",
        "run_command": "已运行 1 条命令",
        "web_fetch": "已获取 1 个网页",
    }.get(tool_name, f"已完成 {tool_name}")


def _parse_arguments(value: Any) -> Dict[str, Any]:
    """解析工具参数，坏数据只影响展示，不阻断历史会话恢复。"""

    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}

    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _format_detail(value: Any) -> str | None:
    """把参数或结果整理成 details 中可读的文本。"""

    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return value.strip() or None
    return json.dumps(value, ensure_ascii=False, indent=2)


def _format_elapsed_time(started_at: float, finished_at: float) -> str:
    """按前端当前规则格式化耗时，避免历史恢复后继续显示“运行中”。"""

    elapsed_seconds = max(1, round(finished_at - started_at))
    minutes = elapsed_seconds // 60
    seconds = elapsed_seconds % 60
    if not minutes:
        return f"{seconds}s"
    return f"{minutes}m {seconds}s"


def list_session_summaries(store: SessionStore, limit: int = 20) -> List[Dict[str, Any]]:
    """列出有真实对话内容的 session，避免空会话进入历史列表。"""

    safe_limit = max(1, min(limit, 50))
    summaries: List[Dict[str, Any]] = []
    for record in store.list_sessions(limit=safe_limit, with_turns=True):
        summaries.append(summarize_session_record(record, store.load_events(record.session_id)))
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

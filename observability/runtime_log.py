"""本地结构化运行日志。

日志按 workspace 写入 `.codex-mini/logs/runtime-YYYY-MM-DD.jsonl`，用于查看
user -> agent -> model -> tool 的真实交互边界。日志是诊断副本，不参与 session
恢复，也不能反向扩大 workspace 或工具权限。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping


RUNTIME_LOG_SCHEMA_VERSION = 2
RUNTIME_LOG_ENABLED_ENV = "CODEX_MINI_RUNTIME_LOG_ENABLED"
_LOG_LOCK = threading.Lock()
_DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
_SUMMARY_MAX_CHARS = 240
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "token",
    "cookie",
    "cookies",
    "password",
    "secret",
}
_SKILL_TOOL_NAMES = {"read_skill", "read_skill_resource"}


def runtime_log_path(project_root: Path, *, now: datetime | None = None) -> Path:
    """返回当前日期对应的 workspace 运行日志路径。"""

    timestamp = now or datetime.now().astimezone()
    filename = f"runtime-{timestamp.date().isoformat()}.jsonl"
    return project_root.resolve() / ".codex-mini" / "logs" / filename


def write_runtime_log(
    project_root: Path,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
    request_id: str | None = None,
    source: str = "runtime",
    actor: str | None = None,
    now: datetime | None = None,
) -> Path | None:
    """追加一条 JSONL 日志；日志失败不得中断主运行链路。"""

    if not _runtime_log_enabled():
        return None

    try:
        timestamp = now or datetime.now().astimezone()
        normalized_payload = _sanitize_value(dict(payload or {}))
        resolved_turn_id = turn_id or _optional_text(normalized_payload.get("turn_id"))
        resolved_request_id = request_id or _optional_text(normalized_payload.get("request_id"))
        payload_json = json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True)
        record = {
            "schema_version": RUNTIME_LOG_SCHEMA_VERSION,
            "log_id": str(uuid.uuid4()),
            "timestamp": timestamp.isoformat(timespec="milliseconds"),
            "timestamp_unix": timestamp.timestamp(),
            "level": _event_level(event_type, normalized_payload),
            "event": str(event_type),
            "summary": _event_summary(event_type, normalized_payload),
            "source": source,
            "actor": actor or _actor_for_event(event_type),
            "session_id": session_id,
            "turn_id": resolved_turn_id,
            "request_id": resolved_request_id,
            "payload_bytes": len(payload_json.encode("utf-8")),
            "payload": normalized_payload,
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        path = runtime_log_path(project_root, now=timestamp)

        # 单行 append 在进程内加锁，避免并发 WebSocket 回合把 JSON 行交错写坏。
        with _LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                path.parent.chmod(0o700)
            except OSError:
                pass
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(line)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        return path
    except (OSError, TypeError, ValueError):
        return None


def sanitize_model_messages(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """生成可落盘的模型消息快照，排除隐藏推理和完整 Skill 内容。"""

    sanitized: list[dict[str, Any]] = []
    for raw_message in messages:
        message = _sanitize_value(dict(raw_message))
        role = str(message.get("role") or "")
        name = str(message.get("name") or "")
        content = message.get("content")

        if role == "tool" and name in _SKILL_TOOL_NAMES:
            message["content"] = "[skill content omitted from runtime log]"
        elif role == "user" and isinstance(content, str) and _looks_like_skill_injection(content):
            message["content"] = "[selected skill injection omitted from runtime log]"
        sanitized.append(message)
    return sanitized


def build_model_message_snapshot(
    messages: Iterable[Mapping[str, Any]],
    previous_messages: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """生成首轮完整、后续增量的模型消息日志快照。"""

    sanitized = sanitize_model_messages(messages)
    if previous_messages is None:
        common_prefix_count = 0
        mode = "full"
    else:
        common_prefix_count = _common_prefix_count(previous_messages, sanitized)
        mode = "delta" if common_prefix_count else "full"

    snapshot_messages = sanitized[common_prefix_count:] if mode == "delta" else sanitized
    snapshot = {
        "mode": mode,
        "message_count": len(sanitized),
        "base_message_count": common_prefix_count if mode == "delta" else 0,
        "messages": snapshot_messages,
    }
    return snapshot, sanitized


def _runtime_log_enabled() -> bool:
    raw_value = os.getenv(RUNTIME_LOG_ENABLED_ENV, "1").strip().lower()
    return raw_value not in _DISABLED_VALUES


def _common_prefix_count(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> int:
    count = 0
    for left, right in zip(first, second):
        if left != right:
            break
        count += 1
    return count


def _sanitize_value(value: Any) -> Any:
    """递归转换为 JSON 安全值，并移除不应进入持久日志的字段。"""

    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_item in value.items():
            key = str(raw_key)
            key_lower = key.lower()
            if key_lower == "reasoning_content":
                continue
            if key_lower in _SENSITIVE_KEYS:
                normalized[key] = "[REDACTED]"
                continue
            normalized[key] = _sanitize_value(raw_item)
        return normalized
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _sanitize_value(asdict(value))
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _looks_like_skill_injection(content: str) -> bool:
    stripped = content.lstrip()
    return stripped.startswith("<skill>") and "<name>" in stripped and "</skill>" in stripped


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _event_level(event_type: str, payload: Mapping[str, Any]) -> str:
    """为常用筛选提供稳定的日志级别。"""

    if event_type == "tool_call_result" and payload.get("ok") is False:
        return "error"
    if event_type in {
        "runtime_error",
        "patch_apply_failed",
        "patch_rollback_failed",
    }:
        return "error"
    if event_type in {
        "permission_request",
        "session_suspended",
        "turn_cancelled",
    }:
        return "warning"
    return "info"


def _event_summary(event_type: str, payload: Mapping[str, Any]) -> str:
    """生成单行摘要，让 tail/rg 无需展开完整 payload。"""

    if event_type == "model_request":
        snapshot = payload.get("message_snapshot")
        if isinstance(snapshot, Mapping):
            return _truncate_summary(
                "model request "
                f"iteration={payload.get('iteration', '?')} "
                f"messages={snapshot.get('message_count', '?')} "
                f"mode={snapshot.get('mode', '?')}"
            )
    if event_type == "model_response":
        tool_calls = payload.get("tool_calls")
        tool_count = len(tool_calls) if isinstance(tool_calls, list) else 0
        detail = _summary_detail(payload)
        base = f"model response iteration={payload.get('iteration', '?')} tool_calls={tool_count}"
        return _truncate_summary(f"{base}: {detail}" if detail else base)
    if event_type in {"tool_call_started", "tool_call_result"}:
        tool_name = payload.get("tool") or payload.get("name") or "unknown"
        if event_type == "tool_call_started":
            return _truncate_summary(f"tool started {tool_name}")
        status = "ok" if payload.get("ok") is True else "failed"
        detail = _summary_detail(payload)
        base = f"tool {status} {tool_name}"
        return _truncate_summary(f"{base}: {detail}" if detail else base)
    if event_type in {"user_message", "final_answer", "runtime_error"}:
        detail = _summary_detail(payload)
        return _truncate_summary(f"{event_type}: {detail}" if detail else event_type)
    return _truncate_summary(event_type.replace("_", " "))


def _summary_detail(payload: Mapping[str, Any]) -> str:
    for key in ("content", "detail", "error", "message"):
        value = payload.get(key)
        if value is None:
            continue
        text = " ".join(str(value).split())
        if text:
            return text
    return ""


def _truncate_summary(value: str) -> str:
    if len(value) <= _SUMMARY_MAX_CHARS:
        return value
    return value[: _SUMMARY_MAX_CHARS - 1].rstrip() + "…"


def _actor_for_event(event_type: str) -> str:
    if event_type in {"user_message", "clarification_response", "permission_decision"}:
        return "user"
    if event_type in {"model_response", "assistant_message", "final_answer"}:
        return "model"
    if event_type in {"model_request", "tool_call_started", "permission_request"}:
        return "agent"
    if event_type == "tool_call_result":
        return "tool"
    return "runtime"

"""从 durable transcript 恢复模型上下文。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from session.models import TranscriptEvent
from session.store import SessionStore


def recover_messages(
    system_prompt: str,
    events: Iterable[TranscriptEvent],
) -> List[Dict[str, Any]]:
    """把 transcript events 重建为 LiteLLM/OpenAI 风格 messages。"""

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for event in events:
        payload = event.payload
        if event.type == "user_message":
            content = str(payload.get("content") or "")
            if content:
                messages.append({"role": "user", "content": content})
            continue

        if event.type == "assistant_message":
            message = _assistant_message_from_payload(payload)
            if message:
                messages.append(message)
            continue

        if event.type == "tool_call_result":
            message = _tool_message_from_payload(payload)
            if message:
                messages.append(message)

    return messages


def recover_session_messages(
    store: SessionStore,
    session_id: str,
    system_prompt: str,
) -> List[Dict[str, Any]]:
    """从 SessionStore 读取指定 session 并恢复模型上下文。"""

    return recover_messages(system_prompt, store.load_events(session_id))


def _assistant_message_from_payload(payload: Dict[str, Any]) -> Dict[str, Any] | None:
    """恢复 assistant 消息，但不恢复 reasoning_content。"""

    content = str(payload.get("content") or "")
    tool_calls = payload.get("tool_calls")
    if not content and not tool_calls:
        return None

    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if isinstance(tool_calls, list) and tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _tool_message_from_payload(payload: Dict[str, Any]) -> Dict[str, Any] | None:
    """把工具结果事件恢复成模型可继续消费的 tool message。"""

    request_id = payload.get("request_id")
    tool_name = payload.get("tool")
    if not request_id or not tool_name:
        return None

    content = str(payload.get("content") or "")
    if not bool(payload.get("ok", False)):
        content = f"[ERROR] {content}"

    return {
        "role": "tool",
        "tool_call_id": str(request_id),
        "name": str(tool_name),
        "content": content,
    }

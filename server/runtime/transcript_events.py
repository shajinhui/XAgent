"""Transcript 事件写入和工具拒绝结果的共享 helpers。"""

from __future__ import annotations

from typing import Any, Dict

from session import SessionStore
from tools.core.types import ToolResult


def record_transcript_event(
    store: SessionStore,
    session_id: str,
    event_type: str,
    payload: Dict[str, Any] | None = None,
) -> None:
    """向 session store 追加 transcript event。"""

    store.append_event(session_id, event_type, payload or {})


def assistant_transcript_payload(message: Dict[str, Any], turn_id: str) -> Dict[str, Any]:
    """生成可持久化的 assistant payload，并排除 reasoning_content。"""

    payload: Dict[str, Any] = {
        "turn_id": turn_id,
        "content": message.get("content") or "",
    }
    if message.get("tool_calls"):
        payload["tool_calls"] = message["tool_calls"]
    return payload


def denied_tool_result(
    tool_name: str,
    command_or_detail: str,
    metadata: Dict[str, Any],
    user_feedback: str | None = None,
) -> ToolResult:
    """把用户拒绝工具执行转成模型可读、前端可展示的 ToolResult。"""

    denied_metadata = {
        "tool": tool_name,
        "error_type": "permission_denied",
        "permission_action": "deny",
        "category": metadata.get("category", "user_denied"),
        "user_denied": True,
    }
    if metadata.get("command"):
        denied_metadata["command"] = metadata["command"]
    if user_feedback:
        denied_metadata["user_feedback"] = user_feedback

    content = f"用户拒绝执行工具 {tool_name}: {command_or_detail}"
    if user_feedback:
        content += f"\n用户希望你这样调整方案: {user_feedback}"

    return ToolResult(
        ok=False,
        content=content,
        metadata=denied_metadata,
    )


def answered_clarification_result(
    tool_name: str,
    request_metadata: Dict[str, Any],
    response: Dict[str, Any],
) -> ToolResult:
    question = str(request_metadata.get("question") or "").strip()
    selected_option = _selected_clarification_option(request_metadata, response)
    answer = str(response.get("content") or "").strip()
    skipped = bool(response.get("skipped"))

    result_metadata: Dict[str, Any] = {
        "tool": tool_name,
        "category": "clarification",
        "user_interaction_action": "answered",
        "question": question,
        "skipped": skipped,
    }
    if selected_option:
        result_metadata["selected_option"] = selected_option
    if answer:
        result_metadata["answer"] = answer

    if skipped:
        content = "用户跳过了澄清问题。请基于已有上下文继续，并明确说明你的假设。"
        if question:
            content = f"{content}\n问题：{question}"
        return ToolResult(ok=True, content=content, metadata=result_metadata)

    lines = ["用户回答了澄清问题。"]
    if question:
        lines.append(f"问题：{question}")
    if selected_option:
        option_text = selected_option["label"]
        description = selected_option.get("description")
        if description:
            option_text = f"{option_text} - {description}"
        lines.append(f"选择：{option_text}")
    if answer:
        lines.append(f"补充：{answer}")
    if not selected_option and not answer:
        lines.append("回答：用户没有提供具体内容，请基于当前上下文继续。")

    return ToolResult(ok=True, content="\n".join(lines), metadata=result_metadata)


def _selected_clarification_option(
    request_metadata: Dict[str, Any],
    response: Dict[str, Any],
) -> Dict[str, Any] | None:
    options = request_metadata.get("options") or []
    if not isinstance(options, list):
        return None

    choice_id = str(response.get("choice_id") or "").strip()
    if choice_id:
        for option in options:
            if isinstance(option, dict) and str(option.get("id") or "") == choice_id:
                return option

    option_index = response.get("option_index")
    if isinstance(option_index, int) and 0 <= option_index < len(options):
        option = options[option_index]
        return option if isinstance(option, dict) else None

    return None

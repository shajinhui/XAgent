"""从 transcript 生成会话摘要。"""

from __future__ import annotations

from typing import Any, Dict, List

from session.models import TranscriptEvent


def summarize_session(events: List[TranscriptEvent], use_model: bool = False) -> str:
    """从 transcript 事件生成会话摘要。

    Args:
        events: transcript 事件列表
        use_model: 是否使用小模型生成摘要（更准确但更慢）
    """
    if use_model:
        return _summarize_with_model(events)
    return _summarize_with_rules(events)


def _summarize_with_rules(events: List[TranscriptEvent]) -> str:

    user_messages = []
    assistant_messages = []
    tool_calls = []
    files_modified = set()
    commands_run = []

    for event in events:
        if event.type == "user_message":
            content = event.payload.get("content", "")
            if content:
                user_messages.append(content)

        elif event.type == "assistant_message":
            content = event.payload.get("content", "")
            if content:
                assistant_messages.append(content)

        elif event.type == "tool_call_started":
            tool_name = _tool_name(event.payload)
            if tool_name:
                tool_calls.append(tool_name)
                args = _tool_arguments(event.payload)

                if tool_name in ("write_file", "edit_file"):
                    file_path = _first_string_arg(args, "file_path", "path")
                    if file_path:
                        files_modified.add(file_path)

                elif tool_name in ("run_command", "execute_command"):
                    command = _first_string_arg(args, "command", "cmd")
                    if command:
                        commands_run.append(command)

    summary_parts = []

    if user_messages:
        summary_parts.append(f"## 用户意图\n\n{user_messages[0][:200]}")

    if tool_calls:
        tool_stats = {}
        for tool in tool_calls:
            tool_stats[tool] = tool_stats.get(tool, 0) + 1
        summary_parts.append("## 工具使用\n\n" + "\n".join(f"- {tool}: {count}次" for tool, count in tool_stats.items()))

    if files_modified:
        summary_parts.append(f"## 修改文件\n\n" + "\n".join(f"- {f}" for f in sorted(files_modified)[:20]))

    if commands_run:
        summary_parts.append(f"## 执行命令\n\n" + "\n".join(f"- {cmd}" for cmd in commands_run[:10]))

    return "\n\n".join(summary_parts) if summary_parts else "空会话"


def _summarize_with_model(events: List[TranscriptEvent]) -> str:
    """用小模型生成摘要。"""
    try:
        import litellm

        # 构造简化的对话文本
        text_parts = []
        for event in events[:50]:  # 只取前50个事件
            if event.type == "user_message":
                content = event.payload.get("content", "")
                if content:
                    text_parts.append(f"用户: {content[:200]}")
            elif event.type == "assistant_message":
                content = event.payload.get("content", "")
                if content:
                    text_parts.append(f"助手: {content[:200]}")

        if not text_parts:
            return "空会话"

        conversation_text = "\n".join(text_parts[:20])  # 最多20轮

        response = litellm.completion(
            model="claude-3-haiku-20240307",
            messages=[
                {
                    "role": "user",
                    "content": f"用3-5个要点总结这次对话的目标、做了什么、遇到什么问题、下一步计划：\n\n{conversation_text}",
                }
            ],
            max_tokens=300,
            timeout=10,
        )
        return response.choices[0].message.content or "摘要生成失败"
    except Exception:
        # 失败时回退到规则方法
        return _summarize_with_rules(events)


def extract_task_state(events: List[TranscriptEvent]) -> Dict:
    """从 transcript 提取任务状态。"""

    state = {
        "goal": "",
        "attempted_approaches": [],
        "modified_files": [],
        "executed_commands": [],
        "blockers": [],
        "next_steps": [],
    }

    for event in events:
        if event.type == "user_message":
            content = event.payload.get("content", "")
            if not state["goal"] and content:
                state["goal"] = content[:300]

        elif event.type == "tool_call_started":
            tool_name = _tool_name(event.payload)
            args = _tool_arguments(event.payload)

            if tool_name in ("write_file", "edit_file"):
                file_path = _first_string_arg(args, "file_path", "path")
                if not file_path:
                    continue
                if file_path not in state["modified_files"]:
                    state["modified_files"].append(file_path)

            elif tool_name in ("run_command", "execute_command"):
                cmd = _first_string_arg(args, "command", "cmd")
                if not cmd:
                    continue
                if cmd not in state["executed_commands"]:
                    state["executed_commands"].append(cmd)

    return state


def _tool_name(payload: Dict[str, Any]) -> str:
    """兼容当前 transcript 字段和早期测试字段。"""

    return str(payload.get("tool") or payload.get("tool_name") or "").strip()


def _tool_arguments(payload: Dict[str, Any]) -> Dict[str, Any]:
    """读取工具参数，兼容 `arguments` 和早期 `args`。"""

    raw = payload.get("arguments")
    if raw is None:
        raw = payload.get("args")
    return raw if isinstance(raw, dict) else {}


def _first_string_arg(args: Dict[str, Any], *keys: str) -> str | None:
    """从工具参数中取第一个非空字符串值。"""

    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None

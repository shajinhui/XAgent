"""从 transcript 生成会话摘要。"""

from __future__ import annotations

from typing import Dict, List

from session.models import TranscriptEvent


def summarize_session(events: List[TranscriptEvent]) -> str:
    """从 transcript 事件生成会话摘要。"""

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
            tool_name = event.payload.get("tool_name", "")
            if tool_name:
                tool_calls.append(tool_name)

                if tool_name in ("write_file", "edit_file"):
                    args = event.payload.get("args", {})
                    if "file_path" in args:
                        files_modified.add(args["file_path"])

                elif tool_name == "execute_command":
                    args = event.payload.get("args", {})
                    if "command" in args:
                        commands_run.append(args["command"])

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
            tool_name = event.payload.get("tool_name", "")
            args = event.payload.get("args", {})

            if tool_name in ("write_file", "edit_file") and "file_path" in args:
                file_path = args["file_path"]
                if file_path not in state["modified_files"]:
                    state["modified_files"].append(file_path)

            elif tool_name == "execute_command" and "command" in args:
                cmd = args["command"]
                if cmd not in state["executed_commands"]:
                    state["executed_commands"].append(cmd)

    return state

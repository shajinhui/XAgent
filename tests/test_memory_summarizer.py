"""memory 摘要器单元测试。"""

from __future__ import annotations

import unittest

from memory.summarizer import extract_task_state, summarize_session
from session.models import TranscriptEvent


class MemorySummarizerTest(unittest.TestCase):
    def test_summarize_session_uses_current_transcript_fields(self) -> None:
        """摘要器读取当前 transcript 中的 tool/arguments 字段。"""

        events = [
            TranscriptEvent(
                event_id="1",
                session_id="s1",
                type="user_message",
                timestamp=1.0,
                payload={"content": "优化性能"},
            ),
            TranscriptEvent(
                event_id="2",
                session_id="s1",
                type="tool_call_started",
                timestamp=2.0,
                payload={
                    "tool": "write_file",
                    "arguments": {"file_path": "/test/file.py"},
                },
            ),
            TranscriptEvent(
                event_id="3",
                session_id="s1",
                type="tool_call_started",
                timestamp=3.0,
                payload={
                    "tool": "run_command",
                    "arguments": {"command": "pytest"},
                },
            ),
        ]

        summary = summarize_session(events)

        self.assertIn("优化性能", summary)
        self.assertIn("write_file", summary)
        self.assertIn("run_command", summary)
        self.assertIn("/test/file.py", summary)
        self.assertIn("pytest", summary)

    def test_summarize_session_keeps_legacy_test_fields(self) -> None:
        """保留早期 tool_name/args 字段兼容。"""

        events = [
            TranscriptEvent(
                event_id="1",
                session_id="s1",
                type="user_message",
                timestamp=1.0,
                payload={"content": "优化性能"},
            ),
            TranscriptEvent(
                event_id="2",
                session_id="s1",
                type="tool_call_started",
                timestamp=2.0,
                payload={
                    "tool_name": "edit_file",
                    "args": {"file_path": "/test/legacy.py"},
                },
            ),
        ]

        summary = summarize_session(events)

        self.assertIn("edit_file", summary)
        self.assertIn("/test/legacy.py", summary)

    def test_extract_task_state(self) -> None:
        """提取任务状态。"""

        events = [
            TranscriptEvent(
                event_id="1",
                session_id="s1",
                type="user_message",
                timestamp=1.0,
                payload={"content": "实现用户登录功能"},
            ),
            TranscriptEvent(
                event_id="2",
                session_id="s1",
                type="tool_call_started",
                timestamp=2.0,
                payload={
                    "tool": "write_file",
                    "arguments": {"file_path": "/auth/login.py"},
                },
            ),
            TranscriptEvent(
                event_id="3",
                session_id="s1",
                type="tool_call_started",
                timestamp=3.0,
                payload={
                    "tool": "run_command",
                    "arguments": {"command": "python -m unittest"},
                },
            ),
        ]

        state = extract_task_state(events)

        self.assertIn("登录功能", state["goal"])
        self.assertIn("/auth/login.py", state["modified_files"])
        self.assertIn("python -m unittest", state["executed_commands"])

    def test_empty_session(self) -> None:
        """空会话返回明确摘要。"""

        self.assertEqual(summarize_session([]), "空会话")


if __name__ == "__main__":
    unittest.main()

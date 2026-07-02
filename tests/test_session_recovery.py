from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from session.models import TranscriptEvent
from session import SessionStore, recover_messages, recover_session_messages


class SessionRecoveryTests(unittest.TestCase):
    def test_recover_messages_rebuilds_model_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            record = store.create_session(session_id="session-1")

            store.append_event(
                record.session_id,
                "user_message",
                {"turn_id": "turn-1", "content": "读取 README"},
            )
            store.append_event(
                record.session_id,
                "assistant_message",
                {
                    "turn_id": "turn-1",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": "{\"path\":\"README.md\"}",
                            },
                        }
                    ],
                    "reasoning_content": "do not restore this",
                },
            )
            store.append_event(
                record.session_id,
                "tool_call_result",
                {
                    "turn_id": "turn-1",
                    "request_id": "call-1",
                    "tool": "read_file",
                    "ok": True,
                    "content": "# XCode",
                },
            )
            store.append_event(
                record.session_id,
                "assistant_message",
                {"turn_id": "turn-1", "content": "README 说明这是 XCode。"},
            )

            messages = recover_session_messages(store, record.session_id, "system prompt")

            self.assertEqual(messages[0], {"role": "system", "content": "system prompt"})
            self.assertEqual(messages[1], {"role": "user", "content": "读取 README"})
            self.assertEqual(messages[2]["role"], "assistant")
            self.assertEqual(messages[2]["tool_calls"][0]["id"], "call-1")
            self.assertNotIn("reasoning_content", messages[2])
            self.assertEqual(
                messages[3],
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "name": "read_file",
                    "content": "# XCode",
                },
            )
            self.assertEqual(
                messages[4],
                {"role": "assistant", "content": "README 说明这是 XCode。"},
            )

    def test_recover_messages_restores_confirmed_plan_context(self) -> None:
        messages = recover_messages(
            "system prompt",
            [
                _event(
                    "session-1",
                    "user_message",
                    {
                        "content": "实现 Plan Mode",
                        "accepted_plan": {
                            "plan_id": "plan-1",
                            "plan_markdown": "# 计划书\n\n## Execution Plan\n1. 分析需求\n2. 实现改动",
                            "items": [
                                {"step": "分析需求", "status": "in_progress"},
                                {"step": "实现改动", "status": "pending"},
                            ],
                            "model": "test-plan-model",
                        },
                    },
                )
            ],
        )

        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("## 用户原始请求", messages[1]["content"])
        self.assertIn("## 已确认计划", messages[1]["content"])
        self.assertIn("# 计划书", messages[1]["content"])
        self.assertIn("1. 分析需求", messages[1]["content"])
        self.assertIn("2. 实现改动", messages[1]["content"])

    def test_recover_messages_marks_failed_tools_with_error_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            record = store.create_session(session_id="session-1")

            store.append_event(
                record.session_id,
                "tool_call_result",
                {
                    "turn_id": "turn-1",
                    "request_id": "call-1",
                    "tool": "run_command",
                    "ok": False,
                    "content": "权限拒绝",
                },
            )

            messages = recover_session_messages(store, record.session_id, "system prompt")

            self.assertEqual(messages[1]["content"], "[ERROR] 权限拒绝")

    def test_recover_messages_ignores_non_model_events(self) -> None:
        messages = recover_messages(
            "system prompt",
            [
                _event("session-1", "session_started", {"title": "ignore"}),
                _event("session-1", "permission_decision", {"approved": True}),
                _event("session-1", "conversation_title", {"title": "ignore"}),
                _event("session-1", "runtime_error", {"message": "ignore"}),
            ],
        )

        self.assertEqual(messages, [{"role": "system", "content": "system prompt"}])


def _event(session_id: str, event_type: str, payload: dict) -> TranscriptEvent:
    return TranscriptEvent(
        event_id=f"{event_type}-1",
        session_id=session_id,
        type=event_type,
        timestamp=1.0,
        payload=payload,
    )


if __name__ == "__main__":
    unittest.main()

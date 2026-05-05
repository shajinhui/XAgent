from __future__ import annotations

import unittest

from server.app import (
    EVENT_SCHEMA_VERSION,
    SessionRuntimeState,
    build_assistant_message,
    build_event,
    denied_tool_result,
    merge_tool_call_delta,
)


class ServerEventTests(unittest.TestCase):
    def test_build_event_adds_common_fields(self) -> None:
        event = build_event(
            "tool_call_started",
            "session-1",
            "turn-1",
            request_id="request-1",
            name="read_file",
        )

        self.assertEqual(event["type"], "tool_call_started")
        self.assertEqual(event["session_id"], "session-1")
        self.assertEqual(event["turn_id"], "turn-1")
        self.assertEqual(event["request_id"], "request-1")
        self.assertEqual(event["schema_version"], EVENT_SCHEMA_VERSION)
        self.assertEqual(event["name"], "read_file")
        self.assertIn("timestamp", event)

    def test_session_state_suspend_and_resume(self) -> None:
        state = SessionRuntimeState("session-1")

        state.suspend("dangerous_shell", "blocked")

        self.assertTrue(state.as_dict()["suspended"])
        self.assertEqual(state.as_dict()["status"], "suspended")
        self.assertEqual(state.as_dict()["suspended_category"], "dangerous_shell")

        state.resume()

        self.assertFalse(state.as_dict()["suspended"])
        self.assertEqual(state.as_dict()["status"], "active")
        self.assertIsNone(state.as_dict()["suspended_category"])

    def test_merge_tool_call_stream_delta(self) -> None:
        buffers = {}

        merge_tool_call_delta(
            buffers,
            {
                "index": 0,
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{\"path\""},
            },
        )
        merge_tool_call_delta(
            buffers,
            {
                "index": 0,
                "function": {"arguments": ": \"README.md\"}"},
            },
        )
        message = build_assistant_message("", buffers)

        self.assertEqual(message["tool_calls"][0]["id"], "call-1")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(
            message["tool_calls"][0]["function"]["arguments"],
            "{\"path\": \"README.md\"}",
        )

    def test_denied_tool_result_is_structured(self) -> None:
        result = denied_tool_result(
            "run_command",
            "权限拒绝: 命令需要用户确认",
            {"category": "command_approval", "command": "ruff check ."},
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata["permission_action"], "deny")
        self.assertTrue(result.metadata["user_denied"])
        self.assertEqual(result.metadata["command"], "ruff check .")


if __name__ == "__main__":
    unittest.main()

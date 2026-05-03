from __future__ import annotations

import unittest

from server.app import build_event


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
        self.assertEqual(event["name"], "read_file")
        self.assertIn("timestamp", event)


if __name__ == "__main__":
    unittest.main()

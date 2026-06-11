from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.app import app
from server.runtime.websocket_context import WebSocketRuntimeContext


@unittest.skipIf(app is None, "FastAPI is not installed")
class WebSocketIntegrationTests(unittest.TestCase):
    def test_agent_ws_accepts_user_input_and_returns_final_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_create = WebSocketRuntimeContext.create

            def create_context(_project_root: Path, system_prompt: str) -> WebSocketRuntimeContext:
                return original_create(root, system_prompt)

            async def fake_run_turn(_ws, _session_store, turn_context):
                turn_context.history.append_assistant_message(
                    {"role": "assistant", "content": "pong"}
                )
                return turn_context.history

            with (
                patch("server.app.WebSocketRuntimeContext.create", side_effect=create_context),
                patch("server.app.run_turn", side_effect=fake_run_turn),
            ):
                client = TestClient(app)
                with client.websocket_connect("/agent/ws") as ws:
                    ready = ws.receive_json()
                    self.assertEqual(ready["type"], "ready")
                    self.assertEqual(ready["workspace"]["selected_root"], root.resolve().as_posix())

                    ws.send_json({"type": "user_input", "content": "ping"})

                    turn_started = ws.receive_json()
                    final_answer = ws.receive_json()

            self.assertEqual(turn_started["type"], "turn_started")
            self.assertTrue(turn_started["session_state"]["turn_in_progress"])
            self.assertEqual(final_answer["type"], "final_answer")
            self.assertEqual(final_answer["content"], "pong")
            self.assertFalse(final_answer["session_state"]["turn_in_progress"])


if __name__ == "__main__":
    unittest.main()

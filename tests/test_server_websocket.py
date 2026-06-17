from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.app import app
from server.runtime.transcript_events import (
    assistant_transcript_payload,
    record_transcript_event,
)
from server.runtime.websocket_context import WebSocketRuntimeContext


class ServerWebSocketTests(unittest.TestCase):
    def test_control_packets_do_not_persist_empty_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(Path(tmp), system_prompt)
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with TestClient(app) as client:
                    with client.websocket_connect("/agent/ws") as ws:
                        ready = ws.receive_json()
                        ws.send_json({"type": "new_session", "request_id": "new-1"})
                        created = ws.receive_json()
                        ws.send_text("{")
                        error = ws.receive_json()

            self.assertEqual(ready["type"], "ready")
            self.assertEqual(created["type"], "session_created")
            self.assertEqual(error["type"], "error")
            self.assertEqual(error["received_type"], "invalid_json")
            self.assertEqual(contexts[-1].session_store.list_sessions(), [])

    def test_ready_workspace_payload_contains_v2_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(Path(tmp), system_prompt)
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with TestClient(app) as client:
                    with client.websocket_connect("/agent/ws") as ws:
                        ready = ws.receive_json()

            workspace = ready["workspace"]
            self.assertEqual(workspace["selected_root"], Path(tmp).resolve().as_posix())
            self.assertEqual(workspace["project_root"], Path(tmp).resolve().as_posix())
            self.assertEqual(workspace["trust"]["level"], "session_only")
            self.assertEqual(workspace["additional_roots"], [])
            self.assertEqual(contexts[-1].workspace.project_root, Path(tmp).resolve())

    def test_open_workspace_payload_separates_selected_and_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            default_root = Path(tmp) / "default"
            default_root.mkdir()
            repo_root = Path(tmp) / "repo"
            nested = repo_root / "packages" / "app"
            nested.mkdir(parents=True)
            (repo_root / ".git").mkdir()

            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(default_root, system_prompt)
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with TestClient(app) as client:
                    with client.websocket_connect("/agent/ws") as ws:
                        ready = ws.receive_json()
                        ws.send_json(
                            {
                                "type": "open_workspace",
                                "path": nested.as_posix(),
                                "request_id": "workspace-1",
                            }
                        )
                        changed = ws.receive_json()

            self.assertEqual(ready["type"], "ready")
            self.assertEqual(changed["type"], "workspace_changed")
            self.assertEqual(changed["workspace"]["selected_root"], nested.resolve().as_posix())
            self.assertEqual(changed["workspace"]["project_root"], repo_root.resolve().as_posix())
            self.assertEqual(changed["workspace"]["git_root"], repo_root.resolve().as_posix())
            self.assertEqual(changed["previous_workspace"]["selected_root"], default_root.resolve().as_posix())
            self.assertEqual(contexts[-1].workspace.project_root, repo_root.resolve())

    def test_first_user_input_persists_session_and_returns_final_answer(self) -> None:
        async def fake_run_turn(
            ws,
            session_store,
            turn_context,
        ):
            assistant = {"role": "assistant", "content": "pong"}
            turn_context.history.append_assistant_message(assistant)
            record_transcript_event(
                session_store,
                turn_context.session_id,
                "assistant_message",
                assistant_transcript_payload(assistant, turn_context.turn_id),
            )
            return turn_context.history

        with tempfile.TemporaryDirectory() as tmp:
            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(Path(tmp), system_prompt)
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with patch("server.app.run_turn", side_effect=fake_run_turn):
                    with TestClient(app) as client:
                        with client.websocket_connect("/agent/ws") as ws:
                            ready = ws.receive_json()
                            ws.send_json(
                                {
                                    "type": "user_input",
                                    "content": "ping",
                                    "model": "openai/gpt-4o-mini",
                                    "reasoning_effort": "off",
                                }
                            )
                            turn_started = ws.receive_json()
                            task_list = ws.receive_json()
                            final_answer = ws.receive_json()

            context = contexts[-1]
            record = context.session_store.get_session(ready["session_id"])
            events = context.session_store.load_events(record.session_id)

            self.assertEqual(turn_started["type"], "turn_started")
            self.assertEqual(task_list["type"], "task_list")
            self.assertEqual(final_answer["type"], "final_answer")
            self.assertEqual(final_answer["content"], "pong")
            self.assertEqual(
                [event.type for event in events],
                [
                    "session_started",
                    "user_message",
                    "turn_started",
                    "task_list",
                    "assistant_message",
                    "final_answer",
                ],
            )


if __name__ == "__main__":
    unittest.main()

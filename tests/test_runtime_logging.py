"""结构化运行日志测试。"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from context_manager import ContextManager
from observability import (
    build_model_message_snapshot,
    runtime_log_path,
    sanitize_model_messages,
    write_runtime_log,
)
from server.runtime.model_config import ModelRequestConfig
from server.runtime.session_state import SessionRuntimeState
from server.runtime.transcript_events import record_transcript_event
from server.runtime.turn_runner import run_turn
from session import SessionStore
from session.turn_context import TurnContext
from tools.core.catalog import build_default_registry
from tools.core.types import ToolResult
from workspace import WorkspaceManager


class FakeWebSocket:
    async def send_json(self, _event: dict) -> None:
        return None


class FakeRunner:
    def execute_invocation(self, invocation, approved=None) -> ToolResult:
        del approved
        return ToolResult(
            ok=True,
            content="tool output",
            metadata={"tool": invocation.name},
        )


def _load_log_records(root: Path) -> list[dict]:
    paths = list((root / ".codex-mini" / "logs").glob("runtime-*.jsonl"))
    if not paths:
        return []
    return [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]


class RuntimeLogTests(unittest.TestCase):
    def test_write_runtime_log_appends_private_jsonl_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"CODEX_MINI_RUNTIME_LOG_ENABLED": "1"},
        ):
            root = Path(tmp)
            now = datetime(2026, 7, 3, 9, 30, tzinfo=timezone.utc)

            path = write_runtime_log(
                root,
                "user_message",
                {"turn_id": "turn-1", "content": "你好", "api_key": "secret-key"},
                session_id="session-1",
                source="transcript",
                now=now,
            )

            self.assertEqual(path, runtime_log_path(root, now=now))
            assert path is not None
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["schema_version"], 2)
            self.assertEqual(record["event"], "user_message")
            self.assertEqual(record["level"], "info")
            self.assertEqual(record["summary"], "user_message: 你好")
            self.assertGreater(record["payload_bytes"], 0)
            self.assertEqual(record["actor"], "user")
            self.assertEqual(record["session_id"], "session-1")
            self.assertEqual(record["turn_id"], "turn-1")
            self.assertEqual(record["payload"]["content"], "你好")
            self.assertEqual(record["payload"]["api_key"], "[REDACTED]")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_runtime_log_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"CODEX_MINI_RUNTIME_LOG_ENABLED": "false"},
        ):
            root = Path(tmp)

            result = write_runtime_log(root, "user_message", {"content": "disabled"})

            self.assertIsNone(result)
            self.assertFalse((root / ".codex-mini" / "logs").exists())

    def test_sanitize_model_messages_omits_private_reasoning_and_skill_bodies(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "调用工具",
                "reasoning_content": "private trace",
            },
            {
                "role": "user",
                "content": "<skill>\n<name>demo</name>\nsecret skill body\n</skill>",
            },
            {
                "role": "tool",
                "name": "read_skill",
                "content": "full skill body",
            },
            {
                "role": "tool",
                "name": "read_file",
                "content": "ordinary file content",
            },
        ]

        sanitized = sanitize_model_messages(messages)

        self.assertNotIn("reasoning_content", sanitized[0])
        self.assertEqual(sanitized[1]["content"], "[selected skill injection omitted from runtime log]")
        self.assertEqual(sanitized[2]["content"], "[skill content omitted from runtime log]")
        self.assertEqual(sanitized[3]["content"], "ordinary file content")
        self.assertEqual(messages[0]["reasoning_content"], "private trace")

    def test_model_message_snapshot_uses_delta_after_first_request(self) -> None:
        first_snapshot, first_messages = build_model_message_snapshot(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "question"},
            ]
        )
        second_snapshot, second_messages = build_model_message_snapshot(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "checking"},
                {"role": "tool", "name": "read_file", "content": "source"},
            ],
            first_messages,
        )

        self.assertEqual(first_snapshot["mode"], "full")
        self.assertEqual(first_snapshot["message_count"], 2)
        self.assertEqual(len(first_snapshot["messages"]), 2)
        self.assertEqual(second_snapshot["mode"], "delta")
        self.assertEqual(second_snapshot["message_count"], 4)
        self.assertEqual(second_snapshot["base_message_count"], 2)
        self.assertEqual(second_snapshot["messages"], second_messages[2:])

    def test_failed_tool_result_has_searchable_error_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            write_runtime_log(
                root,
                "tool_call_result",
                {
                    "turn_id": "turn-1",
                    "tool": "run_command",
                    "ok": False,
                    "content": "命令执行失败\n详细输出",
                },
                session_id="session-1",
            )

            record = _load_log_records(root)[0]
            self.assertEqual(record["level"], "error")
            self.assertEqual(
                record["summary"],
                "tool failed run_command: 命令执行失败 详细输出",
            )

    def test_transcript_event_is_mirrored_to_runtime_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"CODEX_MINI_RUNTIME_LOG_ENABLED": "1"},
        ):
            root = Path(tmp)
            store = SessionStore(root)
            store.create_session(session_id="session-1")

            record_transcript_event(
                store,
                "session-1",
                "user_message",
                {"turn_id": "turn-1", "content": "hello"},
            )

            records = _load_log_records(root)
            self.assertEqual([record["event"] for record in records], ["user_message"])
            self.assertEqual(records[0]["source"], "transcript")
            self.assertEqual(records[0]["payload"]["content"], "hello")

    def test_transcript_event_can_skip_duplicate_runtime_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(root)
            store.create_session(session_id="session-1")

            record_transcript_event(
                store,
                "session-1",
                "assistant_message",
                {"turn_id": "turn-1", "content": "answer"},
                mirror_runtime_log=False,
            )

            self.assertEqual(store.load_events("session-1")[-1].type, "assistant_message")
            self.assertEqual(_load_log_records(root), [])


class RuntimeModelBoundaryLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_turn_logs_model_request_and_visible_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"CODEX_MINI_RUNTIME_LOG_ENABLED": "1"},
        ):
            root = Path(tmp)
            workspace = WorkspaceManager(root).open()
            store = SessionStore(root)
            store.create_session(session_id="session-1")
            history = ContextManager.with_system_prompt("system prompt")
            history.append_user_message("user question")
            state = SessionRuntimeState("session-1")
            state.start_turn("turn-1")
            registry = build_default_registry()
            turn = TurnContext.from_runtime(
                session_id="session-1",
                turn_id="turn-1",
                workspace=workspace,
                session_state=state,
                registry=registry,
                runner=object(),
                history=history,
                system_prompt="system prompt",
                user_input="user question",
                model_config=ModelRequestConfig("test-model", "off"),
            )

            async def fake_stream_model_message(*_args, **_kwargs):
                return {
                    "role": "assistant",
                    "content": "visible answer",
                    "reasoning_content": "private trace",
                }

            with patch(
                "server.runtime.turn_runner.stream_model_message",
                side_effect=fake_stream_model_message,
            ):
                await run_turn(FakeWebSocket(), store, turn)

            records = _load_log_records(root)
            event_types = [record["event"] for record in records]
            self.assertEqual(event_types, ["model_request", "model_response"])
            request_record = records[0]
            response_record = records[1]
            snapshot = request_record["payload"]["message_snapshot"]
            self.assertEqual(snapshot["mode"], "full")
            self.assertEqual(snapshot["messages"][1]["content"], "user question")
            self.assertEqual(request_record["summary"], "model request iteration=1 messages=2 mode=full")
            self.assertEqual(response_record["payload"]["content"], "visible answer")
            self.assertNotIn("reasoning_content", response_record["payload"])

    async def test_run_turn_logs_only_message_delta_after_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = WorkspaceManager(root).open()
            store = SessionStore(root)
            store.create_session(session_id="session-1")
            history = ContextManager.with_system_prompt("system prompt")
            history.append_user_message("user question")
            state = SessionRuntimeState("session-1")
            state.start_turn("turn-1")
            registry = build_default_registry()
            turn = TurnContext.from_runtime(
                session_id="session-1",
                turn_id="turn-1",
                workspace=workspace,
                session_state=state,
                registry=registry,
                runner=FakeRunner(),
                history=history,
                system_prompt="system prompt",
                user_input="user question",
                model_config=ModelRequestConfig("test-model", "off"),
            )
            responses = [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"README.md"}',
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "done"},
            ]

            async def fake_stream_model_message(*_args, **_kwargs):
                return responses.pop(0)

            with patch(
                "server.runtime.turn_runner.stream_model_message",
                side_effect=fake_stream_model_message,
            ):
                await run_turn(FakeWebSocket(), store, turn)

            requests = [
                record for record in _load_log_records(root) if record["event"] == "model_request"
            ]
            self.assertEqual(len(requests), 2)
            first = requests[0]["payload"]["message_snapshot"]
            second = requests[1]["payload"]["message_snapshot"]
            self.assertEqual(first["mode"], "full")
            self.assertEqual(second["mode"], "delta")
            self.assertEqual(second["base_message_count"], 2)
            self.assertEqual(
                [message["role"] for message in second["messages"]],
                ["assistant", "tool"],
            )


if __name__ == "__main__":
    unittest.main()

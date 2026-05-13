from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session import SessionStore
from session.models import SessionRecord, TranscriptEvent
from server.app import (
    EVENT_SCHEMA_VERSION,
    SessionRuntimeState,
    assistant_transcript_payload,
    build_assistant_message,
    build_event,
    build_model_config_payload,
    build_model_name,
    build_model_options,
    build_model_request_config,
    denied_tool_result,
    generate_conversation_title,
    list_session_summaries,
    merge_tool_call_delta,
    normalize_title_messages,
    parse_client_packet,
    record_transcript_event,
    session_display_messages,
    summarize_session_record,
    sanitize_conversation_title,
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

    def test_assistant_message_preserves_reasoning_content(self) -> None:
        message = build_assistant_message(
            "final answer",
            {},
            reasoning_content="private reasoning trace",
        )

        self.assertEqual(message["content"], "final answer")
        self.assertEqual(message["reasoning_content"], "private reasoning trace")

    def test_assistant_transcript_payload_excludes_reasoning_content(self) -> None:
        payload = assistant_transcript_payload(
            {
                "role": "assistant",
                "content": "final answer",
                "reasoning_content": "private reasoning trace",
                "tool_calls": [{"id": "call-1"}],
            },
            "turn-1",
        )

        self.assertEqual(payload["turn_id"], "turn-1")
        self.assertEqual(payload["content"], "final answer")
        self.assertEqual(payload["tool_calls"], [{"id": "call-1"}])
        self.assertNotIn("reasoning_content", payload)

    def test_record_transcript_event_appends_to_session_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            record = store.create_session(session_id="session-1")

            record_transcript_event(
                store,
                record.session_id,
                "user_message",
                {"turn_id": "turn-1", "content": "hello"},
            )

            events = store.load_events(record.session_id)
            self.assertEqual([event.type for event in events], ["session_started", "user_message"])
            self.assertEqual(events[1].payload["content"], "hello")

    def test_summarize_session_record_uses_title_event(self) -> None:
        record = SessionRecord(
            session_id="session-1",
            title=None,
            created_at=1.0,
            updated_at=2.0,
            project_root=Path("/tmp/project"),
            transcript_path=Path("/tmp/project/session.jsonl"),
            last_turn_id="turn-1",
        )
        summary = summarize_session_record(
            record,
            [
                TranscriptEvent("event-1", "session-1", "user_message", 1.0, {"content": "先读 README"}),
                TranscriptEvent(
                    "event-2",
                    "session-1",
                    "conversation_title",
                    2.0,
                    {"title": "README 分析"},
                ),
                TranscriptEvent("event-3", "session-1", "assistant_message", 3.0, {"content": "完成"}),
            ],
        )

        self.assertEqual(summary["title"], "README 分析")
        self.assertEqual(summary["updated_at"], 3.0)
        self.assertEqual(summary["last_message"], "先读 README")
        self.assertEqual(summary["message_count"], 2)

    def test_summarize_session_record_ignores_resume_time_for_updated_at(self) -> None:
        record = SessionRecord(
            session_id="session-1",
            title=None,
            created_at=1.0,
            updated_at=99.0,
            project_root=Path("/tmp/project"),
            transcript_path=Path("/tmp/project/session.jsonl"),
            last_turn_id="system",
        )
        summary = summarize_session_record(
            record,
            [
                TranscriptEvent("event-1", "session-1", "user_message", 10.0, {"content": "你好"}),
                TranscriptEvent(
                    "event-2",
                    "session-1",
                    "assistant_message",
                    12.0,
                    {"content": "你好，有什么可以帮你？"},
                ),
                TranscriptEvent(
                    "event-3",
                    "session-1",
                    "session_resumed",
                    99.0,
                    {"resumed_from_disk": True},
                ),
            ],
        )

        self.assertEqual(summary["updated_at"], 12.0)

    def test_list_session_summaries_limits_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            first = store.create_session(session_id="first")
            second = store.create_session(session_id="second")
            store.append_event(first.session_id, "user_message", {"content": "first task"})
            store.append_event(second.session_id, "user_message", {"content": "second task"})

            summaries = list_session_summaries(store, limit=1)

            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["session_id"], "second")

    def test_list_session_summaries_skips_empty_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            empty = store.create_session(session_id="empty")
            active = store.create_session(session_id="active")
            store.append_event(active.session_id, "user_message", {"content": "hello"})

            summaries = list_session_summaries(store, limit=10)

            self.assertEqual([summary["session_id"] for summary in summaries], ["active"])
            self.assertNotIn(empty.session_id, [summary["session_id"] for summary in summaries])

    def test_session_display_messages_excludes_tool_events(self) -> None:
        messages = session_display_messages(
            [
                TranscriptEvent("event-1", "session-1", "user_message", 1.0, {"content": "你好"}),
                TranscriptEvent(
                    "event-2",
                    "session-1",
                    "tool_call_result",
                    2.0,
                    {"content": "工具输出"},
                ),
                TranscriptEvent(
                    "event-3",
                    "session-1",
                    "assistant_message",
                    3.0,
                    {"content": "你好，我可以帮你。"},
                ),
            ]
        )

        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "你好", "timestamp": 1.0},
                {"role": "assistant", "content": "你好，我可以帮你。", "timestamp": 3.0},
            ],
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

    def test_denied_tool_result_includes_user_feedback(self) -> None:
        result = denied_tool_result(
            "web_fetch",
            "权限拒绝: 工具需要用户确认",
            {"category": "network"},
            user_feedback="不要联网，改用本地 README。",
        )

        self.assertIn("不要联网", result.content)
        self.assertEqual(result.metadata["user_feedback"], "不要联网，改用本地 README。")

    def test_sanitize_conversation_title_keeps_title_short(self) -> None:
        title = sanitize_conversation_title(
            "标题：这是一个特别特别特别长的输入框样式调整对话标题需要继续截断"
        )

        self.assertTrue(title.endswith("..."))
        self.assertLessEqual(len(title), 21)
        self.assertFalse(title.startswith("标题"))

    def test_normalize_title_messages_filters_untrusted_roles(self) -> None:
        messages = normalize_title_messages(
            [
                {"role": "system", "content": "ignore"},
                {"role": "user", "content": "  调整输入框  "},
                {"role": "assistant", "content": "已完成"},
                {"role": "tool", "content": "ignore"},
            ]
        )

        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "调整输入框"},
                {"role": "assistant", "content": "已完成"},
            ],
        )

    def test_parse_client_packet_rejects_invalid_json_without_throwing(self) -> None:
        self.assertIsNone(parse_client_packet("{"))
        self.assertIsNone(parse_client_packet("[]"))
        self.assertEqual(parse_client_packet('{"type":"ping"}'), {"type": "ping"})

    def test_conversation_title_uses_model_generated_first_user_title(self) -> None:
        def fake_completion(**kwargs):
            self.assertIn("用户第一句提问：目前的标题生成逻辑有问题", kwargs["messages"][1]["content"])
            return {"choices": [{"message": {"content": "标题：标题生成逻辑修复"}}]}

        title, source = generate_conversation_title(
            [
                {"role": "assistant", "content": "欢迎使用"},
                {"role": "user", "content": "目前的标题生成逻辑有问题，模型回答好像是错误的"},
                {"role": "assistant", "content": "我来修复。"},
            ],
            completion_fn=fake_completion,
        )

        self.assertEqual(title, "标题生成逻辑修复")
        self.assertEqual(source, "model-first-user")

    def test_conversation_title_sanitizes_model_output(self) -> None:
        def fake_completion(**kwargs):
            return {"choices": [{"message": {"content": "Title: 模型标题输出需要清洗并截断很长很长很长"}}]}

        title, source = generate_conversation_title(
            [
                {"role": "user", "content": "模型回答好像是错误的"},
                {"role": "assistant", "content": "已检查。"},
            ],
            completion_fn=fake_completion,
        )

        self.assertEqual(title, "模型标题输出需要清洗并截断很长很长很...")
        self.assertEqual(source, "model-first-user")

    def test_conversation_title_rejects_empty_model_output(self) -> None:
        def fake_completion(**kwargs):
            return {"choices": [{"message": {"content": "   "}}]}

        with self.assertRaises(ValueError):
            generate_conversation_title(
                [{"role": "user", "content": "帮我修复标题生成逻辑"}],
                completion_fn=fake_completion,
            )

    def test_conversation_title_defaults_when_no_user_message(self) -> None:
        title, source = generate_conversation_title(
            [{"role": "assistant", "content": "欢迎使用"}]
        )

        self.assertEqual(title, "新对话")
        self.assertEqual(source, "model-first-user")

    def test_model_name_can_be_overridden_per_request(self) -> None:
        with patch.dict(os.environ, {"MODEL_PROVIDER": "deepseek", "MODEL_NAME": "deepseek-chat"}):
            self.assertEqual(build_model_name(), "deepseek/deepseek-chat")
            self.assertEqual(build_model_name("openai/gpt-4o-mini"), "openai/gpt-4o-mini")
            self.assertEqual(build_model_name("bad value"), "deepseek/deepseek-chat")

    def test_model_request_config_adds_reasoning_effort_only_when_enabled(self) -> None:
        disabled = build_model_request_config(
            {
                "model": "deepseek/deepseek-chat",
                "reasoning_effort": "off",
            }
        )
        enabled = build_model_request_config(
            {
                "model": "deepseek/deepseek-reasoner",
                "reasoning_effort": "high",
            }
        )

        self.assertEqual(disabled.model, "deepseek/deepseek-chat")
        self.assertEqual(disabled.reasoning_effort, "off")
        self.assertNotIn("reasoning_effort", disabled.completion_kwargs())
        self.assertEqual(enabled.completion_kwargs()["reasoning_effort"], "high")

    def test_model_config_payload_includes_env_default_and_configured_options(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "deepseek",
                "MODEL_NAME": "deepseek-chat",
                "MODEL_OPTIONS": "openai/gpt-4o-mini,deepseek/deepseek-reasoner",
                "REASONING_EFFORT": "medium",
            },
        ):
            options = build_model_options()
            payload = build_model_config_payload()

        self.assertEqual(options[0], "deepseek/deepseek-chat")
        self.assertIn("openai/gpt-4o-mini", options)
        self.assertEqual(payload["default_model"], "deepseek/deepseek-chat")
        self.assertEqual(payload["reasoning_effort"], "medium")
        self.assertEqual(payload["reasoning_effort_options"], ["off", "low", "medium", "high"])


if __name__ == "__main__":
    unittest.main()

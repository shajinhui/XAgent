from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session import SessionStore
from session.models import SessionRecord, TranscriptEvent
from server.processors.request_dispatcher import WebSocketRequestDispatcher
from server.processors.title_processor import (
    generate_conversation_title,
    normalize_title_messages,
    sanitize_conversation_title,
)
from server.protocol.events import EVENT_SCHEMA_VERSION, build_event, parse_client_packet
from server.runtime.model_config import (
    build_api_kwargs,
    build_low_cost_model_name,
    build_model_config_payload,
    build_model_name,
    build_model_options,
    build_model_request_config,
)
from server.runtime.model_stream import (
    build_assistant_message,
    clear_historical_reasoning_content,
    merge_tool_call_delta,
)
from server.runtime.session_state import SessionRuntimeState
from server.runtime.transcript_events import (
    answered_clarification_result,
    assistant_transcript_payload,
    denied_tool_result,
    record_transcript_event,
)
from server.runtime.turn_runner import (
    request_user_clarification,
    wait_for_clarification_response,
    wait_for_permission_decision,
)
from server.runtime.websocket_context import WebSocketRuntimeContext
from server.views.session_summary import (
    list_session_summaries,
    session_display_messages,
    summarize_session_record,
)


class FakeWebSocket:
    def __init__(self, incoming=None) -> None:
        self.sent = []
        self.incoming = list(incoming or [])

    async def send_json(self, event):
        self.sent.append(event)

    async def receive_json(self):
        return self.incoming.pop(0)


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

    def test_clear_historical_reasoning_keeps_tool_call_reasoning(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "plain reply",
                "reasoning_content": "drop this",
            },
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "keep for tool call context",
                "tool_calls": [{"id": "call-1"}],
            },
        ]

        clear_historical_reasoning_content(messages)

        self.assertNotIn("reasoning_content", messages[0])
        self.assertEqual(messages[1]["reasoning_content"], "keep for tool call context")

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
            store.append_event(
                first.session_id,
                "user_message",
                {"turn_id": "turn-first", "content": "first task"},
            )
            store.append_event(
                second.session_id,
                "user_message",
                {"turn_id": "turn-second", "content": "second task"},
            )

            summaries = list_session_summaries(store, limit=1)

            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["session_id"], "second")

    def test_list_session_summaries_skips_empty_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            empty = store.create_session(session_id="empty")
            active = store.create_session(session_id="active")
            store.append_event(
                active.session_id,
                "user_message",
                {"turn_id": "turn-active", "content": "hello"},
            )

            summaries = list_session_summaries(store, limit=10)

            self.assertEqual([summary["session_id"] for summary in summaries], ["active"])
            self.assertNotIn(empty.session_id, [summary["session_id"] for summary in summaries])

    def test_list_session_summaries_does_not_load_empty_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            for index in range(20):
                store.create_session(session_id=f"empty-{index}")

            with patch.object(store, "load_events", wraps=store.load_events) as load_events:
                summaries = list_session_summaries(store, limit=10)

            self.assertEqual(summaries, [])
            load_events.assert_not_called()

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

    def test_session_display_messages_includes_clarification_exchange(self) -> None:
        messages = session_display_messages(
            [
                TranscriptEvent(
                    "event-1",
                    "session-1",
                    "clarification_request",
                    1.0,
                    {"question": "优先覆盖什么范围？"},
                ),
                TranscriptEvent(
                    "event-2",
                    "session-1",
                    "clarification_response",
                    2.0,
                    {"content": "核心后端"},
                ),
            ]
        )

        self.assertEqual(
            messages,
            [
                {"role": "assistant", "content": "优先覆盖什么范围？", "timestamp": 1.0},
                {"role": "user", "content": "核心后端", "timestamp": 2.0},
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

    def test_answered_clarification_result_formats_selected_option(self) -> None:
        result = answered_clarification_result(
            "ask_user",
            {
                "question": "这次补充注释优先覆盖到什么范围？",
                "options": [
                    {
                        "id": "core",
                        "label": "核心后端",
                        "description": "先补 runtime/session",
                    }
                ],
            },
            {"choice_id": "core", "content": "先按推荐来"},
        )

        self.assertTrue(result.ok)
        self.assertIn("核心后端", result.content)
        self.assertIn("先按推荐来", result.content)
        self.assertEqual(result.metadata["user_interaction_action"], "answered")

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
        self.assertEqual(source, "low-cost-first-user")

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
        self.assertEqual(source, "low-cost-first-user")

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
        self.assertEqual(source, "low-cost-first-user")

    def test_conversation_title_uses_low_cost_model(self) -> None:
        def fake_completion(**kwargs):
            self.assertEqual(kwargs["model"], "deepseek/deepseek-chat")
            return {"choices": [{"message": {"content": "低成本标题"}}]}

        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "openai",
                "MODEL_NAME": "gpt-4o",
                "LOW_COST_MODEL_PROVIDER": "deepseek",
                "LOW_COST_MODEL_NAME": "deepseek-chat",
            },
        ):
            title, source = generate_conversation_title(
                [{"role": "user", "content": "帮我生成标题"}],
                completion_fn=fake_completion,
            )

        self.assertEqual(title, "低成本标题")
        self.assertEqual(source, "low-cost-first-user")

    def test_model_name_can_be_overridden_per_request(self) -> None:
        with patch.dict(os.environ, {"MODEL_PROVIDER": "deepseek", "MODEL_NAME": "deepseek-chat"}):
            self.assertEqual(build_model_name(), "deepseek/deepseek-chat")
            self.assertEqual(build_model_name("openai/gpt-4o-mini"), "openai/gpt-4o-mini")
            self.assertEqual(build_model_name("bad value"), "deepseek/deepseek-chat")

    def test_low_cost_model_defaults_to_main_model(self) -> None:
        with patch.dict(
            os.environ,
            {"MODEL_PROVIDER": "deepseek", "MODEL_NAME": "deepseek-chat"},
            clear=False,
        ):
            os.environ.pop("LOW_COST_MODEL_PROVIDER", None)
            os.environ.pop("LOW_COST_MODEL_NAME", None)
            self.assertEqual(build_low_cost_model_name(), "deepseek/deepseek-chat")

    def test_low_cost_model_can_use_dedicated_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "openai",
                "MODEL_NAME": "gpt-4o",
                "LOW_COST_MODEL_PROVIDER": "deepseek",
                "LOW_COST_MODEL_NAME": "deepseek-chat",
            },
        ):
            self.assertEqual(build_low_cost_model_name(), "deepseek/deepseek-chat")

    def test_api_kwargs_use_single_generic_api_key(self) -> None:
        with patch.dict(os.environ, {"API_KEY": "test-key", "API_BASE": "https://example.test"}):
            self.assertEqual(
                build_api_kwargs(),
                {"api_key": "test-key", "api_base": "https://example.test"},
            )

    def test_model_request_config_adds_reasoning_effort_only_when_enabled(self) -> None:
        disabled = build_model_request_config(
            {
                "model": "openai/gpt-4o-mini",
                "reasoning_effort": "off",
            }
        )
        enabled = build_model_request_config(
            {
                "model": "openai/o3-mini",
                "reasoning_effort": "high",
            }
        )

        self.assertEqual(disabled.model, "openai/gpt-4o-mini")
        self.assertEqual(disabled.reasoning_effort, "off")
        self.assertNotIn("reasoning_effort", disabled.completion_kwargs())
        self.assertEqual(enabled.completion_kwargs()["reasoning_effort"], "high")

    def test_deepseek_thinking_off_is_explicitly_disabled(self) -> None:
        config = build_model_request_config(
            {
                "model": "deepseek/deepseek-chat",
                "reasoning_effort": "off",
            }
        )

        kwargs = config.completion_kwargs()

        self.assertNotIn("reasoning_effort", kwargs)
        self.assertEqual(kwargs["temperature"], 0)
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "disabled"}})

    def test_deepseek_thinking_maps_compatible_efforts(self) -> None:
        medium = build_model_request_config(
            {
                "model": "deepseek/deepseek-v4-pro",
                "reasoning_effort": "medium",
            }
        )
        xhigh = build_model_request_config(
            {
                "model": "deepseek/deepseek-v4-pro",
                "reasoning_effort": "xhigh",
            }
        )

        medium_kwargs = medium.completion_kwargs()
        xhigh_kwargs = xhigh.completion_kwargs()

        self.assertNotIn("temperature", medium_kwargs)
        self.assertEqual(medium_kwargs["reasoning_effort"], "high")
        self.assertEqual(medium_kwargs["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertEqual(xhigh_kwargs["reasoning_effort"], "max")
        self.assertEqual(xhigh_kwargs["extra_body"], {"thinking": {"type": "enabled"}})

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
        self.assertEqual(payload["reasoning_effort_options"], ["off", "low", "medium", "high", "max"])


class WebSocketRequestDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_packet_before_first_user_input_does_not_persist_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = WebSocketRuntimeContext.create(Path(tmp), "system")
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            await dispatcher.handle_invalid_packet()

            self.assertEqual(ws.sent[0]["type"], "error")
            self.assertEqual(ws.sent[0]["received_type"], "invalid_json")
            with self.assertRaises(KeyError):
                context.session_store.get_session(context.session_id)

    async def test_new_session_replaces_memory_session_without_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = WebSocketRuntimeContext.create(Path(tmp), "system")
            original_session_id = context.session_id
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet({"type": "new_session"})

            self.assertTrue(handled)
            self.assertNotEqual(context.session_id, original_session_id)
            self.assertFalse(context.session_persisted)
            self.assertEqual(ws.sent[0]["type"], "session_created")
            with self.assertRaises(KeyError):
                context.session_store.get_session(context.session_id)

    async def test_resume_without_target_clears_suspended_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = WebSocketRuntimeContext.create(Path(tmp), "system")
            context.session_state.suspend("dangerous_shell", "blocked")
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet({"type": "resume_session"})

            self.assertTrue(handled)
            self.assertFalse(context.session_state.suspended)
            self.assertEqual(ws.sent[0]["type"], "session_resumed")
            self.assertFalse(ws.sent[0]["session_state"]["suspended"])


class TurnRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_for_permission_decision_rejects_unrelated_packets(self) -> None:
        ws = FakeWebSocket(
            [
                {"type": "user_input"},
                {"type": "permission_decision", "request_id": "other"},
                {
                    "type": "permission_decision",
                    "request_id": "request-1",
                    "approved": False,
                    "feedback": "换个方案",
                },
            ]
        )

        approved, feedback = await wait_for_permission_decision(
            ws,
            "session-1",
            "turn-1",
            "request-1",
        )

        self.assertFalse(approved)
        self.assertEqual(feedback, "换个方案")
        self.assertEqual([event["type"] for event in ws.sent], ["error", "error"])
        self.assertEqual(ws.sent[0]["received_type"], "user_input")
        self.assertEqual(ws.sent[1]["received_request_id"], "other")

    async def test_wait_for_clarification_response_rejects_unrelated_packets(self) -> None:
        ws = FakeWebSocket(
            [
                {"type": "user_input"},
                {
                    "type": "clarification_response",
                    "request_id": "other",
                    "content": "别的回答",
                },
                {
                    "type": "clarification_response",
                    "request_id": "request-1",
                    "choice_id": "core",
                    "content": "核心后端",
                },
            ]
        )

        response = await wait_for_clarification_response(
            ws,
            "session-1",
            "turn-1",
            "request-1",
        )

        self.assertEqual(response["choice_id"], "core")
        self.assertEqual(response["content"], "核心后端")
        self.assertEqual([event["type"] for event in ws.sent], ["error", "error"])
        self.assertEqual(ws.sent[0]["received_type"], "user_input")
        self.assertEqual(ws.sent[1]["received_request_id"], "other")

    async def test_request_user_clarification_records_response_and_returns_tool_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            store.create_session(session_id="session-1")
            ws = FakeWebSocket(
                [
                    {
                        "type": "clarification_response",
                        "request_id": "request-1",
                        "choice_id": "core",
                        "content": "核心后端",
                    }
                ]
            )

            result = await request_user_clarification(
                ws,
                store,
                "session-1",
                "turn-1",
                "request-1",
                "ask_user",
                {
                    "question": "优先覆盖什么范围？",
                    "options": [{"id": "core", "label": "核心后端"}],
                    "allow_freeform": True,
                },
            )

        self.assertTrue(result.ok)
        self.assertIn("核心后端", result.content)
        self.assertEqual([event["type"] for event in ws.sent], ["clarification_request", "clarification_response_ack"])


if __name__ == "__main__":
    unittest.main()

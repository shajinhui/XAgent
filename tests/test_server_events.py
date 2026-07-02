from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session import SessionStore
from session.models import SessionRecord, TranscriptEvent
from session.turn_diff import TurnDiffTracker
from patch import PatchStatus, PatchStore, build_file_change, build_patch_proposal
from server.processors.request_dispatcher import WebSocketRequestDispatcher
from server.processors.title_processor import (
    generate_conversation_title,
    normalize_title_messages,
    sanitize_conversation_title,
)
from server.processors.task_list_processor import (
    fallback_task_list,
    generate_task_list,
    normalize_task_list,
)
from server.protocol.events import EVENT_SCHEMA_VERSION, build_event, parse_client_packet
from server.runtime.model_config import (
    build_api_kwargs,
    build_low_cost_model_name,
    build_litellm_model_name,
    build_model_config_payload,
    build_model_name,
    build_model_options,
    build_model_request_config,
    configure_litellm_environment,
    fetch_provider_model_options,
)
from server.runtime.model_stream import (
    build_assistant_message,
    clear_historical_reasoning_content,
    merge_tool_call_delta,
)
from server.runtime.session_state import SessionRuntimeState, persist_websocket_session
from server.runtime.transcript_events import (
    answered_clarification_result,
    assistant_transcript_payload,
    denied_tool_result,
    record_transcript_event,
)
from server.runtime.turn_runner import (
    TurnCancelled,
    _emit_patch_approval_request_event,
    emit_tool_result,
    request_user_clarification,
    wait_for_clarification_response,
    wait_for_permission_decision,
)
from server.runtime.websocket_context import WebSocketRuntimeContext
from server.views.session_summary import (
    list_session_summaries,
    pending_patch_review_event,
    session_display_messages,
    summarize_session_record,
)
from security.permissions import ApprovalPolicy, NetworkPolicy, PermissionProfile
from tools.core.types import ToolResult
from workspace import PermissionMode, TrustLevel, WorkspaceContext, WorkspaceTrustStore


class FakeWebSocket:
    def __init__(self, incoming=None) -> None:
        self.sent = []
        self.incoming = list(incoming or [])

    async def send_json(self, event):
        self.sent.append(event)

    async def receive_json(self):
        return self.incoming.pop(0)


class FakeHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.payload


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

    def test_list_session_summaries_filters_by_workspace_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            store = SessionStore(root)
            first_workspace = WorkspaceContext(
                selected_root=first,
                project_root=root,
                current_dir=first,
                display_name="first",
            )
            second_workspace = WorkspaceContext(
                selected_root=second,
                project_root=root,
                current_dir=second,
                display_name="second",
            )
            first_session = store.create_session(
                session_id="first",
                metadata={"workspace": first_workspace.as_dict()},
            )
            second_session = store.create_session(
                session_id="second",
                metadata={"workspace": second_workspace.as_dict()},
            )
            store.append_event(
                first_session.session_id,
                "user_message",
                {"turn_id": "turn-first", "content": "first task"},
            )
            store.append_event(
                second_session.session_id,
                "user_message",
                {"turn_id": "turn-second", "content": "second task"},
            )

            summaries = list_session_summaries(store, limit=10, workspace=first_workspace)

            self.assertEqual([summary["session_id"] for summary in summaries], ["first"])
            self.assertEqual(summaries[0]["workspace"]["selected_root"], first.resolve().as_posix())

    def test_list_session_summaries_does_not_load_empty_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            for index in range(20):
                store.create_session(session_id=f"empty-{index}")

            with patch.object(store, "load_events", wraps=store.load_events) as load_events:
                summaries = list_session_summaries(store, limit=10)

            self.assertEqual(summaries, [])
            load_events.assert_not_called()

    def test_session_display_messages_restores_activity_trace(self) -> None:
        messages = session_display_messages(
            [
                TranscriptEvent("event-1", "session-1", "user_message", 1.0, {"content": "你好"}),
                TranscriptEvent(
                    "event-2",
                    "session-1",
                    "assistant_message",
                    1.5,
                    {
                        "turn_id": "turn-1",
                        "content": "",
                        "tool_calls": [{"id": "call-1"}],
                    },
                ),
                TranscriptEvent(
                    "event-3",
                    "session-1",
                    "tool_call_started",
                    2.0,
                    {
                        "turn_id": "turn-1",
                        "request_id": "call-1",
                        "tool": "read_file",
                        "arguments": '{"path": "README.md"}',
                    },
                ),
                TranscriptEvent(
                    "event-4",
                    "session-1",
                    "tool_call_result",
                    3.0,
                    {
                        "turn_id": "turn-1",
                        "request_id": "call-1",
                        "tool": "read_file",
                        "ok": True,
                        "content": "文件内容",
                        "metadata": {},
                    },
                ),
                TranscriptEvent(
                    "event-5",
                    "session-1",
                    "assistant_message",
                    4.0,
                    {"turn_id": "turn-1", "content": "**完成**"},
                ),
            ]
        )

        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "你好", "timestamp": 1.0},
                {
                    "role": "activity",
                    "content": "已处理 1s",
                    "collapsed": True,
                    "activity_key": "turn:turn-1",
                    "startedAt": 2000,
                    "finishedAt": 3000,
                    "timestamp": 2.0,
                },
                {
                    "role": "activity_event",
                    "content": "已读取 1 个文件",
                    "activity_key": "turn:turn-1",
                    "step": {
                        "label": "已读取 1 个文件",
                        "status": "success",
                        "kind": "read",
                        "requestId": "call-1",
                        "toolName": "read_file",
                        "detail": "文件内容",
                    },
                    "timestamp": 3.0,
                },
                {
                    "role": "assistant",
                    "content": "**完成**",
                    "timestamp": 4.0,
                    "activity_key": "turn:turn-1",
                },
            ],
        )

    def test_pending_patch_review_event_restores_latest_unresolved_patch(self) -> None:
        pending = pending_patch_review_event(
            [
                TranscriptEvent(
                    "event-1",
                    "session-1",
                    "patch_proposed",
                    1.0,
                    {
                        "turn_id": "turn-1",
                        "request_id": "patch-1-request",
                        "tool": "write_file",
                        "patch_id": "patch-1",
                        "patch_status": PatchStatus.PROPOSED.value,
                        "summary": "更新 README",
                        "changed_paths": ["README.md"],
                        "additions": 1,
                        "deletions": 1,
                        "changes": [
                            {
                                "path": "README.md",
                                "change_type": "update",
                                "unified_diff": "--- a/README.md\n+++ b/README.md\n",
                                "additions": 1,
                                "deletions": 1,
                                "before": "不应恢复",
                                "after": "不应恢复",
                            }
                        ],
                        "metadata": {"before": "不应恢复", "source": "dry_run"},
                    },
                ),
                TranscriptEvent(
                    "event-2",
                    "session-1",
                    "patch_proposed",
                    2.0,
                    {
                        "turn_id": "turn-2",
                        "request_id": "patch-2-request",
                        "tool": "write_file",
                        "patch_id": "patch-2",
                        "patch_status": PatchStatus.PROPOSED.value,
                        "summary": "更新 app.py",
                        "changed_paths": ["app.py"],
                        "additions": 2,
                        "deletions": 0,
                        "changes": [
                            {
                                "path": "app.py",
                                "change_type": "update",
                                "unified_diff": "--- a/app.py\n+++ b/app.py\n",
                                "additions": 2,
                                "deletions": 0,
                            }
                        ],
                        "metadata": {"source": "dry_run"},
                    },
                ),
            ]
        )

        self.assertIsNotNone(pending)
        self.assertEqual(pending["type"], "patch_proposed")
        self.assertEqual(pending["patch_id"], "patch-2")
        self.assertEqual(pending["changed_paths"], ["app.py"])
        self.assertEqual(pending["changes"][0]["path"], "app.py")
        self.assertNotIn("before", pending["changes"][0])
        self.assertNotIn("after", pending["changes"][0])
        self.assertNotIn("before", pending["metadata"])

    def test_pending_patch_review_event_ignores_resolved_patch(self) -> None:
        pending = pending_patch_review_event(
            [
                TranscriptEvent(
                    "event-1",
                    "session-1",
                    "patch_proposed",
                    1.0,
                    {
                        "turn_id": "turn-1",
                        "request_id": "patch-request",
                        "tool": "write_file",
                        "patch_id": "patch-1",
                        "patch_status": PatchStatus.PROPOSED.value,
                        "changed_paths": ["README.md"],
                    },
                ),
                TranscriptEvent(
                    "event-2",
                    "session-1",
                    "patch_applied",
                    2.0,
                    {
                        "turn_id": "turn-1",
                        "request_id": "patch-request",
                        "tool": "apply_patch",
                        "patch_id": "patch-1",
                        "patch_status": PatchStatus.APPLIED.value,
                        "changed_paths": ["README.md"],
                    },
                ),
            ]
        )

        self.assertIsNone(pending)

    def test_pending_patch_review_event_ignores_rolled_back_patch(self) -> None:
        pending = pending_patch_review_event(
            [
                TranscriptEvent(
                    "event-1",
                    "session-1",
                    "patch_proposed",
                    1.0,
                    {
                        "turn_id": "turn-1",
                        "request_id": "patch-request",
                        "tool": "write_file",
                        "patch_id": "patch-1",
                        "patch_status": PatchStatus.PROPOSED.value,
                        "changed_paths": ["README.md"],
                    },
                ),
                TranscriptEvent(
                    "event-2",
                    "session-1",
                    "patch_rolled_back",
                    2.0,
                    {
                        "turn_id": "turn-2",
                        "request_id": "rollback-request",
                        "tool": "rollback_patch",
                        "patch_id": "patch-1",
                        "patch_status": PatchStatus.ROLLED_BACK.value,
                        "changed_paths": ["README.md"],
                    },
                ),
            ]
        )

        self.assertIsNone(pending)

    def test_pending_patch_review_event_preserves_test_result_metadata(self) -> None:
        pending = pending_patch_review_event(
            [
                TranscriptEvent(
                    "event-1",
                    "session-1",
                    "patch_proposed",
                    1.0,
                    {
                        "turn_id": "turn-2",
                        "request_id": "patch-request-2",
                        "tool": "apply_patch",
                        "patch_id": "patch-test-1",
                        "patch_status": PatchStatus.PROPOSED.value,
                        "summary": "继续审查剩余变更",
                        "changed_paths": ["README.md"],
                        "changes": [
                            {
                                "path": "README.md",
                                "change_type": "update",
                                "unified_diff": "--- a/README.md\n+++ b/README.md\n",
                                "additions": 1,
                                "deletions": 0,
                            }
                        ],
                        "metadata": {
                            "partial_apply": True,
                            "test_result": {
                                "command": "python -m unittest discover -s tests",
                                "ok": False,
                                "exit_code": 1,
                                "source": "explicit",
                                "output": "FAILED sample test",
                                "before": "不应出现在测试结果里",
                            },
                            "before": "不应恢复",
                        },
                    },
                )
            ]
        )

        self.assertIsNotNone(pending)
        self.assertEqual(pending["patch_id"], "patch-test-1")
        self.assertEqual(
            pending["metadata"]["test_result"]["command"],
            "python -m unittest discover -s tests",
        )
        self.assertFalse(pending["metadata"]["test_result"]["ok"])
        self.assertEqual(pending["metadata"]["test_result"]["output"], "FAILED sample test")
        self.assertNotIn("before", pending["metadata"])

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
            self.assertEqual(build_model_name(), "deepseek-chat")
            self.assertEqual(build_model_name("gpt-4o-mini"), "gpt-4o-mini")
            self.assertEqual(build_model_name("bad value"), "deepseek-chat")

    def test_low_cost_model_defaults_to_main_model(self) -> None:
        with patch.dict(
            os.environ,
            {"MODEL_PROVIDER": "deepseek", "MODEL_NAME": "deepseek-chat"},
            clear=False,
        ):
            os.environ.pop("LOW_COST_MODEL_PROVIDER", None)
            os.environ.pop("LOW_COST_MODEL_NAME", None)
            self.assertEqual(build_low_cost_model_name(), "deepseek-chat")

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
            self.assertEqual(build_low_cost_model_name(), "deepseek-chat")

    def test_task_list_normalizes_model_output(self) -> None:
        tasks = normalize_task_list(
            {
                "tasks": [
                    {"step": " 分析项目结构 ", "status": "running"},
                    {"step": "修改前端卡片", "status": "pending"},
                    {"step": "验证构建", "status": "pending"},
                ]
            }
        )

        self.assertEqual(
            tasks,
            [
                {"step": "分析项目结构", "status": "in_progress"},
                {"step": "修改前端卡片", "status": "pending"},
                {"step": "验证构建", "status": "pending"},
            ],
        )

    def test_generate_task_list_uses_low_cost_model(self) -> None:
        def fake_completion(**kwargs):
            self.assertEqual(kwargs["model"], "deepseek/deepseek-chat")
            self.assertIn("把用户任务拆成 UI 可展示的简短任务列表", kwargs["messages"][0]["content"])
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "```json\n"
                                '{"tasks":[{"step":"定位问题","status":"in_progress"},'
                                '{"step":"修改实现","status":"pending"},'
                                '{"step":"验证结果","status":"pending"}]}\n'
                                "```"
                            )
                        }
                    }
                ]
            }

        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "openai",
                "MODEL_NAME": "gpt-4o",
                "LOW_COST_MODEL_PROVIDER": "deepseek",
                "LOW_COST_MODEL_NAME": "deepseek-chat",
            },
        ):
            tasks, source = generate_task_list("修复右上角任务卡片", completion_fn=fake_completion)

        self.assertEqual(source, "low-cost-task-list")
        self.assertEqual(
            tasks,
            [
                {"step": "定位问题", "status": "in_progress"},
                {"step": "修改实现", "status": "pending"},
                {"step": "验证结果", "status": "pending"},
            ],
        )

    def test_fallback_task_list_is_stable(self) -> None:
        tasks = fallback_task_list("修复右上角卡片展示")

        self.assertEqual(tasks[0]["status"], "in_progress")
        self.assertEqual(len(tasks), 3)
        self.assertTrue(all(task["step"] for task in tasks))

    def test_api_kwargs_use_single_generic_api_key(self) -> None:
        with patch.dict(os.environ, {"API_KEY": "test-key", "API_BASE": "https://example.test"}):
            self.assertEqual(
                build_api_kwargs(),
                {"api_key": "test-key", "api_base": "https://example.test"},
            )

    def test_litellm_model_name_adds_deepseek_provider_only_for_requests(self) -> None:
        self.assertEqual(
            build_litellm_model_name("deepseek-v4-pro"),
            "deepseek/deepseek-v4-pro",
        )
        with patch.dict(os.environ, {"MODEL_PROVIDER": "deepseek"}):
            self.assertEqual(
                build_litellm_model_name("deepseek/deepseek-v4-pro"),
                "deepseek/deepseek-v4-pro",
            )
        with patch.dict(os.environ, {"MODEL_PROVIDER": "openai"}):
            self.assertEqual(build_litellm_model_name("gpt-4o-mini"), "gpt-4o-mini")

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
                "model": "deepseek-v4-pro",
                "reasoning_effort": "medium",
            }
        )
        xhigh = build_model_request_config(
            {
                "model": "deepseek-v4-pro",
                "reasoning_effort": "xhigh",
            }
        )

        medium_kwargs = medium.completion_kwargs()
        xhigh_kwargs = xhigh.completion_kwargs()

        self.assertEqual(medium_kwargs["model"], "deepseek/deepseek-v4-pro")
        self.assertNotIn("temperature", medium_kwargs)
        self.assertEqual(medium_kwargs["reasoning_effort"], "high")
        self.assertEqual(medium_kwargs["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertEqual(xhigh_kwargs["reasoning_effort"], "max")
        self.assertEqual(xhigh_kwargs["extra_body"], {"thinking": {"type": "enabled"}})

    def test_fetch_deepseek_model_options_maps_models_endpoint(self) -> None:
        def fake_opener(request, timeout):
            self.assertEqual(request.full_url, "https://api.deepseek.com/models")
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            self.assertEqual(timeout, 3.0)
            return FakeHTTPResponse(
                b'{"object":"list","data":[{"id":"deepseek-v4-flash"},{"id":"deepseek-v4-pro"}]}'
            )

        options = fetch_provider_model_options(
            "deepseek",
            api_key="test-key",
            opener=fake_opener,
        )

        self.assertEqual(
            options,
            ["deepseek-v4-flash", "deepseek-v4-pro"],
        )

    def test_fetch_provider_model_options_fails_closed_without_api_key(self) -> None:
        self.assertEqual(fetch_provider_model_options("deepseek", api_key=""), [])

    def test_model_config_payload_prefers_remote_model_options(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "deepseek",
                "MODEL_NAME": "deepseek-chat",
                "MODEL_OPTIONS": "deepseek-v4-flash,deepseek-v4-pro",
                "REASONING_EFFORT": "medium",
            },
        ):
            with patch(
                "server.runtime.model_config.fetch_provider_model_options",
                return_value=["deepseek-v4-pro"],
            ):
                options = build_model_options()
                payload = build_model_config_payload()

        self.assertEqual(options, ["deepseek-v4-pro"])
        self.assertEqual(payload["default_model"], "deepseek-chat")
        self.assertEqual(payload["model_options"], ["deepseek-v4-pro"])
        self.assertEqual(payload["reasoning_effort"], "medium")
        self.assertEqual(payload["reasoning_effort_options"], ["off", "low", "medium", "high", "max"])

    def test_model_config_payload_falls_back_to_env_options(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "deepseek",
                "MODEL_NAME": "deepseek-v4-pro",
                "MODEL_OPTIONS": "deepseek-v4-flash,deepseek-v4-pro",
            },
        ):
            with patch(
                "server.runtime.model_config.fetch_provider_model_options",
                return_value=[],
            ):
                payload = build_model_config_payload()

        self.assertEqual(payload["default_model"], "deepseek-v4-pro")
        self.assertEqual(payload["model_options"], ["deepseek-v4-flash", "deepseek-v4-pro"])

    def test_litellm_uses_local_cost_map_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            configure_litellm_environment()

            self.assertEqual(os.environ["LITELLM_LOCAL_MODEL_COST_MAP"], "True")

    def test_litellm_cost_map_env_can_be_overridden(self) -> None:
        with patch.dict(os.environ, {"LITELLM_LOCAL_MODEL_COST_MAP": "False"}, clear=True):
            configure_litellm_environment()

            self.assertEqual(os.environ["LITELLM_LOCAL_MODEL_COST_MAP"], "False")

    def test_session_summaries_ignore_legacy_rows_with_system_last_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            record = store.create_session(session_id="legacy-hidden")
            store.append_event(
                record.session_id,
                "user_message",
                {"turn_id": "turn-1", "content": "旧会话还在吗"},
            )
            store.append_event(
                record.session_id,
                "assistant_message",
                {"turn_id": "turn-1", "content": "还在"},
            )
            with store._connect() as conn:
                conn.execute(
                    "UPDATE sessions SET last_turn_id = ? WHERE session_id = ?",
                    ("system", record.session_id),
                )

            with patch.object(store, "load_events", wraps=store.load_events) as load_events:
                summaries = list_session_summaries(store)

        self.assertEqual(summaries, [])
        load_events.assert_not_called()


class WebSocketRequestDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_patch_review_control_applies_stored_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")
            context = WebSocketRuntimeContext.create(root, "system")
            persist_websocket_session(context.session_store, context.session_id, context.workspace)
            context.session_persisted = True
            proposal = build_patch_proposal(
                session_id=context.session_id,
                turn_id="turn-preview",
                cwd=root,
                changes=[
                    build_file_change(
                        "app.py",
                        "print('old')\n",
                        "print('new')\n",
                        existed_before=True,
                        exists_after=True,
                    )
                ],
                patch_id="patch-direct-apply",
                summary="更新 app.py",
            )
            patch_store = PatchStore(root / ".codex-mini" / "patches")
            patch_store.save(proposal)
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "apply_patch_review",
                    "patch_id": "patch-direct-apply",
                    "request_id": "patch-apply-1",
                    "turn_id": "turn-review",
                }
            )

            self.assertTrue(handled)
            self.assertEqual(target.read_text(encoding="utf-8"), "print('new')\n")
            self.assertEqual(
                [event["type"] for event in ws.sent],
                ["tool_call_started", "tool_call_result", "patch_applied"],
            )
            self.assertEqual(ws.sent[0]["name"], "apply_patch")
            self.assertEqual(ws.sent[2]["patch_id"], "patch-direct-apply")
            self.assertEqual(ws.sent[2]["changed_paths"], ["app.py"])
            self.assertNotIn("before", ws.sent[2]["metadata"])
            self.assertNotIn("after", ws.sent[2]["metadata"])
            self.assertEqual(patch_store.load("patch-direct-apply").status, PatchStatus.APPLIED)
            self.assertEqual(
                [event.type for event in context.session_store.load_events(context.session_id)][-3:],
                ["tool_call_started", "tool_call_result", "patch_applied"],
            )

    async def test_apply_patch_review_control_applies_selected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.py"
            second = root / "second.py"
            first.write_text("print('old first')\n", encoding="utf-8")
            second.write_text("print('old second')\n", encoding="utf-8")
            context = WebSocketRuntimeContext.create(root, "system")
            persist_websocket_session(context.session_store, context.session_id, context.workspace)
            context.session_persisted = True
            proposal = build_patch_proposal(
                session_id=context.session_id,
                turn_id="turn-preview",
                cwd=root,
                changes=[
                    build_file_change(
                        "first.py",
                        "print('old first')\n",
                        "print('new first')\n",
                        existed_before=True,
                        exists_after=True,
                    ),
                    build_file_change(
                        "second.py",
                        "print('old second')\n",
                        "print('new second')\n",
                        existed_before=True,
                        exists_after=True,
                    ),
                ],
                patch_id="patch-direct-partial",
                summary="更新两个文件",
            )
            patch_store = PatchStore(root / ".codex-mini" / "patches")
            patch_store.save(proposal)
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "apply_patch_review",
                    "patch_id": "patch-direct-partial",
                    "selected_paths": ["first.py"],
                    "request_id": "patch-apply-partial",
                    "turn_id": "turn-review",
                }
            )

            self.assertTrue(handled)
            self.assertEqual(first.read_text(encoding="utf-8"), "print('new first')\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "print('old second')\n")
            self.assertEqual(
                [event["type"] for event in ws.sent],
                ["tool_call_started", "tool_call_result", "patch_proposed"],
            )
            self.assertTrue(ws.sent[1]["metadata"]["partial_apply"])
            self.assertTrue(ws.sent[2]["metadata"]["partial_apply"])
            self.assertEqual(ws.sent[2]["changed_paths"], ["second.py"])
            proposal = patch_store.load("patch-direct-partial")
            self.assertEqual(proposal.status, PatchStatus.PROPOSED)
            self.assertEqual(proposal.changed_paths, ["second.py"])

    async def test_reject_patch_review_control_rejects_stored_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")
            context = WebSocketRuntimeContext.create(root, "system")
            persist_websocket_session(context.session_store, context.session_id, context.workspace)
            context.session_persisted = True
            proposal = build_patch_proposal(
                session_id=context.session_id,
                turn_id="turn-preview",
                cwd=root,
                changes=[
                    build_file_change(
                        "app.py",
                        "print('old')\n",
                        "print('new')\n",
                        existed_before=True,
                        exists_after=True,
                    )
                ],
                patch_id="patch-direct-reject",
                summary="更新 app.py",
            )
            patch_store = PatchStore(root / ".codex-mini" / "patches")
            patch_store.save(proposal)
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "reject_patch_review",
                    "patch_id": "patch-direct-reject",
                    "reason": "用户选择暂不应用",
                    "request_id": "patch-reject-1",
                    "turn_id": "turn-review",
                }
            )

            self.assertTrue(handled)
            self.assertEqual(target.read_text(encoding="utf-8"), "print('old')\n")
            self.assertEqual(
                [event["type"] for event in ws.sent],
                ["tool_call_started", "tool_call_result", "patch_rejected"],
            )
            rejected = patch_store.load("patch-direct-reject")
            self.assertEqual(rejected.status, PatchStatus.REJECTED)
            self.assertEqual(rejected.metadata["reason"], "用户选择暂不应用")
            self.assertEqual(ws.sent[2]["patch_id"], "patch-direct-reject")
            self.assertEqual(ws.sent[2]["tool"], "reject_patch")

    async def test_apply_patch_review_control_keeps_policy_recheck_on_protected_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".env"
            target.write_text("TOKEN=old\n", encoding="utf-8")
            context = WebSocketRuntimeContext.create(root, "system")
            persist_websocket_session(context.session_store, context.session_id, context.workspace)
            context.session_persisted = True
            proposal = build_patch_proposal(
                session_id=context.session_id,
                turn_id="turn-preview",
                cwd=root,
                changes=[
                    build_file_change(
                        ".env",
                        "TOKEN=old\n",
                        "TOKEN=new\n",
                        existed_before=True,
                        exists_after=True,
                    )
                ],
                patch_id="patch-protected-path",
                summary="更新 .env",
            )
            patch_store = PatchStore(root / ".codex-mini" / "patches")
            patch_store.save(proposal)
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "apply_patch_review",
                    "patch_id": "patch-protected-path",
                    "request_id": "patch-apply-protected",
                    "turn_id": "turn-review",
                }
            )

            self.assertTrue(handled)
            self.assertEqual(target.read_text(encoding="utf-8"), "TOKEN=old\n")
            self.assertEqual(
                [event["type"] for event in ws.sent],
                ["tool_call_started", "tool_call_result", "patch_apply_failed"],
            )
            self.assertFalse(ws.sent[1]["ok"])
            self.assertEqual(ws.sent[2]["patch_id"], "patch-protected-path")
            self.assertEqual(patch_store.load("patch-protected-path").status, PatchStatus.FAILED)

    async def test_undo_file_restores_changed_file_and_consumes_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "notes.txt"
            target.write_text("before", encoding="utf-8")
            tracker = TurnDiffTracker()
            tracker.save_baseline(target.as_posix())
            target.write_text("after", encoding="utf-8")

            context = WebSocketRuntimeContext.create(root, "system")
            context.last_diff_tracker = tracker
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "undo_file",
                    "file_path": "notes.txt",
                    "request_id": "undo-1",
                }
            )

            self.assertTrue(handled)
            self.assertEqual(target.read_text(encoding="utf-8"), "before")
            self.assertEqual(ws.sent[0]["type"], "file_undone")
            self.assertEqual(ws.sent[0]["request_id"], "undo-1")
            self.assertNotIn(target.resolve().as_posix(), tracker.baselines)

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

    async def test_plan_request_creates_pending_plan_without_persisting_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = WebSocketRuntimeContext.create(Path(tmp), "system")
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            plan_items = [
                {"step": "分析需求", "status": "in_progress"},
                {"step": "实现改动", "status": "pending"},
            ]
            with patch(
                "server.processors.request_dispatcher.generate_task_list",
                return_value=(plan_items, "test-plan-model"),
            ):
                handled = await dispatcher.handle_control_packet(
                    {
                        "type": "plan_request",
                        "content": "实现 Plan Mode",
                        "request_id": "plan-1",
                    }
                )

            self.assertTrue(handled)
            self.assertIsNotNone(context.pending_plan)
            self.assertEqual(context.pending_plan["content"], "实现 Plan Mode")
            self.assertIn("计划书", context.pending_plan["plan_markdown"])
            self.assertIn("分析需求", context.pending_plan["plan_markdown"])
            self.assertEqual(ws.sent[0]["type"], "plan_pending")
            self.assertEqual(ws.sent[0]["items"], plan_items)
            self.assertIn("确认前不修改文件", ws.sent[0]["summary"])
            self.assertIn("## Execution Plan", ws.sent[0]["plan_markdown"])
            self.assertEqual(ws.sent[0]["model"], "test-plan-model")
            with self.assertRaises(KeyError):
                context.session_store.get_session(context.session_id)

    async def test_plan_cancel_clears_pending_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = WebSocketRuntimeContext.create(Path(tmp), "system")
            context.pending_plan = {
                "plan_id": "plan-123",
                "content": "实现 Plan Mode",
                "items": [{"step": "分析需求", "status": "in_progress"}],
                "model": "test-plan-model",
                "created_at": 1.0,
            }
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "plan_cancel",
                    "plan_id": "plan-123",
                    "request_id": "cancel-1",
                }
            )

            self.assertTrue(handled)
            self.assertIsNone(context.pending_plan)
            self.assertEqual(ws.sent[0]["type"], "plan_cancelled")
            self.assertEqual(ws.sent[0]["plan_id"], "plan-123")

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

    async def test_change_directory_updates_workspace_policy_without_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "pkg"
            nested.mkdir()
            context = WebSocketRuntimeContext.create(root, "system")
            original_session_id = context.session_id
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "change_directory",
                    "path": "pkg",
                    "request_id": "cwd-1",
                }
            )

            self.assertTrue(handled)
            self.assertEqual(context.session_id, original_session_id)
            self.assertEqual(context.workspace.current_dir, nested.resolve())
            self.assertEqual(context.runner.ctx.current_dir, nested.resolve())
            self.assertEqual(ws.sent[0]["type"], "workspace_policy_changed")
            self.assertEqual(ws.sent[0]["reason"], "change_directory")
            self.assertEqual(ws.sent[0]["workspace"]["current_dir"], nested.resolve().as_posix())
            with self.assertRaises(KeyError):
                context.session_store.get_session(context.session_id)

    async def test_add_dir_updates_additional_roots_and_runner_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            external = Path(tmp) / "external"
            root.mkdir()
            external.mkdir()
            context = WebSocketRuntimeContext.create(root, "system")
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "add_dir",
                    "path": external.as_posix(),
                    "access": "write",
                    "request_id": "add-dir-1",
                }
            )

            self.assertTrue(handled)
            self.assertEqual(ws.sent[0]["type"], "workspace_policy_changed")
            self.assertEqual(ws.sent[0]["reason"], "add_dir")
            self.assertEqual(
                ws.sent[0]["workspace"]["additional_roots"][0]["path"],
                external.resolve().as_posix(),
            )
            self.assertIn(external.resolve(), context.runner.ctx.filesystem_policy.readable_roots)
            self.assertIn(external.resolve(), context.runner.ctx.filesystem_policy.writable_roots)

    async def test_trust_workspace_enables_custom_project_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            trust_path = Path(tmp) / "trust.json"
            root.mkdir()
            config_dir = root / ".codex-mini"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                """
[permissions]
profile = "read_only"

[tests]
command = "python -m unittest discover -s tests"
timeout = 23

[[exec.rules]]
action = "deny"
prefix = ["npm", "publish"]
category = "publish_blocked"
""".strip(),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"CODEX_MINI_TRUST_STORE": trust_path.as_posix()}):
                context = WebSocketRuntimeContext.create(root, "system")
                original_session_id = context.session_id
                ws = FakeWebSocket()
                dispatcher = WebSocketRequestDispatcher(ws, context)

                handled = await dispatcher.handle_control_packet(
                    {"type": "trust_workspace", "request_id": "trust-1"}
                )

                self.assertTrue(handled)
                self.assertEqual(context.session_id, original_session_id)
                self.assertEqual(ws.sent[0]["type"], "workspace_policy_changed")
                self.assertEqual(ws.sent[0]["reason"], "trust_workspace")
                self.assertEqual(ws.sent[0]["workspace"]["trust"]["level"], "trusted")
                self.assertEqual(context.workspace.trust.level, TrustLevel.TRUSTED)
                assert context.workspace.project_policy is not None
                self.assertEqual(context.workspace.project_policy.source, "runtime_mode")
                self.assertEqual(context.runner.ctx.permission_profile, PermissionProfile.WORKSPACE_WRITE)
                self.assertEqual(
                    context.runner.ctx.default_test_command,
                    "python -m unittest discover -s tests",
                )
                self.assertEqual(context.runner.ctx.default_test_timeout, 23)
                self.assertEqual(
                    WorkspaceTrustStore(trust_path).trust_for(root).level,
                    TrustLevel.TRUSTED,
                )

                handled = await dispatcher.handle_control_packet(
                    {
                        "type": "set_permission_mode",
                        "mode": PermissionMode.CUSTOM.value,
                        "request_id": "mode-custom",
                    }
                )

                self.assertTrue(handled)
                self.assertEqual(context.workspace.project_policy.source, "project_config")
                self.assertEqual(context.runner.ctx.permission_profile, PermissionProfile.READ_ONLY)
                self.assertEqual(
                    context.runner.ctx.default_test_command,
                    "python -m unittest discover -s tests",
                )
                self.assertEqual(
                    context.runner.ctx.policy.check_command("npm publish").category,
                    "publish_blocked",
                )

    async def test_untrust_workspace_returns_to_default_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            trust_path = Path(tmp) / "trust.json"
            root.mkdir()
            config_dir = root / ".codex-mini"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                "[permissions]\nprofile = \"read_only\"\n",
                encoding="utf-8",
            )
            WorkspaceTrustStore(trust_path).mark_trusted(root)

            with patch.dict(os.environ, {"CODEX_MINI_TRUST_STORE": trust_path.as_posix()}):
                context = WebSocketRuntimeContext.create(root, "system")
                self.assertEqual(context.runner.ctx.permission_profile, PermissionProfile.WORKSPACE_WRITE)
                ws = FakeWebSocket()
                dispatcher = WebSocketRequestDispatcher(ws, context)

                handled = await dispatcher.handle_control_packet(
                    {"type": "untrust_workspace", "request_id": "untrust-1"}
                )

                self.assertTrue(handled)
                self.assertEqual(ws.sent[0]["type"], "workspace_policy_changed")
                self.assertEqual(ws.sent[0]["reason"], "untrust_workspace")
                self.assertEqual(ws.sent[0]["workspace"]["trust"]["level"], "untrusted")
                self.assertEqual(context.workspace.trust.level, TrustLevel.UNTRUSTED)
                assert context.workspace.project_policy is not None
                self.assertEqual(context.workspace.project_policy.source, "runtime_mode")
                self.assertEqual(context.runner.ctx.permission_profile, PermissionProfile.WORKSPACE_WRITE)
                self.assertEqual(
                    WorkspaceTrustStore(trust_path).trust_for(root).level,
                    TrustLevel.UNTRUSTED,
                )

    async def test_set_permission_mode_updates_runner_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = WebSocketRuntimeContext.create(root, "system")
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "set_permission_mode",
                    "mode": PermissionMode.FULL_ACCESS.value,
                    "request_id": "mode-1",
                }
            )

            self.assertTrue(handled)
            self.assertEqual(ws.sent[0]["type"], "workspace_policy_changed")
            self.assertEqual(ws.sent[0]["reason"], "set_permission_mode")
            self.assertEqual(ws.sent[0]["workspace"]["policy"]["permission_mode"], "full_access")
            self.assertEqual(context.permission_mode, PermissionMode.FULL_ACCESS)
            self.assertEqual(context.runner.ctx.permission_profile, PermissionProfile.DANGER_NO_SANDBOX)
            self.assertEqual(context.runner.ctx.approval_policy, ApprovalPolicy.AUTO)
            self.assertEqual(context.runner.ctx.network_policy, NetworkPolicy.ENABLED)

    async def test_permission_mode_persists_across_workspace_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            first.mkdir()
            second.mkdir()
            context = WebSocketRuntimeContext.create(first, "system")
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            await dispatcher.handle_control_packet(
                {
                    "type": "set_permission_mode",
                    "mode": PermissionMode.FULL_ACCESS.value,
                    "request_id": "mode-global",
                }
            )
            handled = await dispatcher.handle_control_packet(
                {
                    "type": "open_workspace",
                    "path": second.as_posix(),
                    "request_id": "workspace-second",
                }
            )

            self.assertTrue(handled)
            self.assertEqual(context.permission_mode, PermissionMode.FULL_ACCESS)
            self.assertEqual(context.workspace.selected_root, second.resolve())
            self.assertEqual(context.runner.ctx.permission_profile, PermissionProfile.DANGER_NO_SANDBOX)
            self.assertEqual(ws.sent[-1]["type"], "workspace_changed")
            self.assertEqual(ws.sent[-1]["workspace"]["policy"]["permission_mode"], "full_access")

    async def test_set_custom_permission_mode_requires_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = WebSocketRuntimeContext.create(root, "system")
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "set_permission_mode",
                    "mode": PermissionMode.CUSTOM.value,
                    "request_id": "mode-custom",
                }
            )

            self.assertTrue(handled)
            self.assertEqual(ws.sent[0]["type"], "workspace_error")
            self.assertIn("config.toml", ws.sent[0]["message"])

    async def test_trust_workspace_rejects_invalid_project_config_without_persisting_trust(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            trust_path = Path(tmp) / "trust.json"
            root.mkdir()
            config_dir = root / ".codex-mini"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                "[model]\nprovider = \"deepseek\"\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"CODEX_MINI_TRUST_STORE": trust_path.as_posix()}):
                context = WebSocketRuntimeContext.create(root, "system")
                ws = FakeWebSocket()
                dispatcher = WebSocketRequestDispatcher(ws, context)

                handled = await dispatcher.handle_control_packet(
                    {"type": "trust_workspace", "request_id": "trust-invalid"}
                )

                self.assertTrue(handled)
                self.assertEqual(ws.sent[0]["type"], "workspace_error")
                self.assertEqual(ws.sent[0]["requested_trust_level"], "trusted")
                self.assertEqual(context.workspace.trust.level, TrustLevel.SESSION_ONLY)
                self.assertEqual(
                    WorkspaceTrustStore(trust_path).trust_for(root).level,
                    TrustLevel.SESSION_ONLY,
                )

    async def test_resume_session_restores_workspace_snapshot_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            nested = root / "pkg"
            external = Path(tmp) / "external"
            root.mkdir()
            nested.mkdir()
            external.mkdir()
            (root / "AGENTS.md").write_text("root instructions", encoding="utf-8")
            (nested / "AGENTS.md").write_text("nested instructions", encoding="utf-8")

            seed_context = WebSocketRuntimeContext.create(root, "system")
            session_id = "stored-session"
            seed_context.session_store.create_session(
                session_id=session_id,
                metadata={"workspace": seed_context.workspace.as_dict()},
            )
            seed_context.session_store.append_event(
                session_id,
                "user_message",
                {"turn_id": "turn-1", "content": "hello"},
            )
            seed_context.workspace.add_additional_root(external, "read")
            seed_context.workspace.change_current_dir(nested)
            seed_context.session_store.append_event(
                session_id,
                "workspace_policy_changed",
                {
                    "turn_id": "system",
                    "workspace": seed_context.workspace.as_dict(),
                },
            )

            context = WebSocketRuntimeContext.create(root, "system")
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {"type": "resume_session", "session_id": session_id, "request_id": "resume-1"}
            )

            self.assertTrue(handled)
            self.assertEqual(ws.sent[0]["type"], "session_resumed")
            self.assertTrue(ws.sent[0]["resumed_from_disk"])
            self.assertEqual(context.workspace.current_dir, nested.resolve())
            self.assertEqual(context.runner.ctx.current_dir, nested.resolve())
            self.assertEqual(context.workspace.additional_roots[0].path, external.resolve())
            self.assertIn(external.resolve(), context.runner.ctx.filesystem_policy.readable_roots)
            self.assertEqual(ws.sent[0]["messages"][0]["role"], "user")
            self.assertEqual(ws.sent[0]["messages"][0]["content"], "hello")
            self.assertIn("root instructions", context.history.messages[0]["content"])
            self.assertIn("nested instructions", context.history.messages[0]["content"])

    async def test_resume_session_restores_matching_session_allow_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            seed_context = WebSocketRuntimeContext.create(root, "system")
            session_id = "allow-session"
            workspace_snapshot = seed_context.workspace.as_dict()
            seed_context.session_store.create_session(
                session_id=session_id,
                metadata={"workspace": workspace_snapshot},
            )
            seed_context.session_store.append_event(
                session_id,
                "permission_decision",
                {
                    "turn_id": "turn-1",
                    "request_id": "call-1",
                    "tool": "run_command",
                    "approved": True,
                    "scope": "session",
                    "prefix_rule": ["npm", "run", "test"],
                    "workspace": workspace_snapshot,
                },
            )

            context = WebSocketRuntimeContext.create(root, "system")
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {"type": "resume_session", "session_id": session_id, "request_id": "resume-allow"}
            )
            decision = context.runner.ctx.policy.check_command("npm run test -- --watch=false")

            self.assertTrue(handled)
            self.assertEqual(ws.sent[0]["type"], "session_resumed")
            self.assertTrue(decision.allowed)
            self.assertFalse(decision.approval_required)
            self.assertEqual(decision.matched_prefix_rule, ("npm", "run", "test"))

    async def test_resume_session_allow_ignores_global_permission_mode_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            seed_context = WebSocketRuntimeContext.create(root, "system")
            session_id = "allow-session-global-mode"
            workspace_snapshot = seed_context.workspace.as_dict()
            seed_context.session_store.create_session(
                session_id=session_id,
                metadata={"workspace": workspace_snapshot},
            )
            seed_context.session_store.append_event(
                session_id,
                "permission_decision",
                {
                    "turn_id": "turn-1",
                    "request_id": "call-1",
                    "tool": "run_command",
                    "approved": True,
                    "scope": "session",
                    "prefix_rule": ["npm", "run", "test"],
                    "workspace": workspace_snapshot,
                },
            )

            context = WebSocketRuntimeContext.create(root, "system")
            context.change_permission_mode(PermissionMode.FULL_ACCESS)
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "resume_session",
                    "session_id": session_id,
                    "request_id": "resume-global-mode",
                }
            )
            decision = context.runner.ctx.policy.check_command("npm run test -- --watch=false")

            self.assertTrue(handled)
            self.assertEqual(ws.sent[0]["type"], "session_resumed")
            self.assertEqual(context.permission_mode, PermissionMode.FULL_ACCESS)
            self.assertEqual(context.workspace.project_policy.permission_mode, PermissionMode.FULL_ACCESS)
            self.assertEqual(decision.matched_prefix_rule, ("npm", "run", "test"))

    async def test_resume_session_restores_persisted_suspension_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            seed_context = WebSocketRuntimeContext.create(root, "system")
            session_id = "suspended-session"
            seed_context.session_store.create_session(
                session_id=session_id,
                metadata={"workspace": seed_context.workspace.as_dict()},
            )
            seed_context.session_store.append_event(
                session_id,
                "session_suspended",
                {
                    "turn_id": "turn-1",
                    "category": "dangerous_shell",
                    "detail": "blocked",
                    "session_state": {
                        "status": "suspended",
                        "suspended": True,
                        "suspended_category": "dangerous_shell",
                        "suspended_detail": "blocked",
                        "suspended_at": 1.0,
                    },
                },
            )

            context = WebSocketRuntimeContext.create(root, "system")
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {
                    "type": "resume_session",
                    "session_id": session_id,
                    "request_id": "resume-suspended",
                }
            )

            self.assertTrue(handled)
            self.assertEqual(ws.sent[0]["type"], "session_resumed")
            self.assertTrue(ws.sent[0]["session_state"]["suspended"])
            self.assertEqual(ws.sent[0]["session_state"]["suspended_category"], "dangerous_shell")
            self.assertTrue(context.session_state.suspended)

            second_context = WebSocketRuntimeContext.create(root, "system")
            second_ws = FakeWebSocket()
            second_dispatcher = WebSocketRequestDispatcher(second_ws, second_context)

            handled = await second_dispatcher.handle_control_packet(
                {
                    "type": "resume_session",
                    "session_id": session_id,
                    "request_id": "resume-suspended-again",
                }
            )

            self.assertTrue(handled)
            self.assertTrue(second_ws.sent[0]["session_state"]["suspended"])

    async def test_resume_session_does_not_restore_allow_prefix_after_workspace_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            nested = root / "pkg"
            root.mkdir()
            nested.mkdir()
            seed_context = WebSocketRuntimeContext.create(root, "system")
            session_id = "changed-workspace-allow-session"
            initial_workspace = seed_context.workspace.as_dict()
            seed_context.session_store.create_session(
                session_id=session_id,
                metadata={"workspace": initial_workspace},
            )
            seed_context.session_store.append_event(
                session_id,
                "permission_decision",
                {
                    "turn_id": "turn-1",
                    "request_id": "call-1",
                    "tool": "run_command",
                    "approved": True,
                    "scope": "session",
                    "prefix_rule": ["npm", "run", "test"],
                    "workspace": initial_workspace,
                },
            )
            seed_context.workspace.change_current_dir(nested)
            seed_context.session_store.append_event(
                session_id,
                "workspace_policy_changed",
                {
                    "turn_id": "system",
                    "workspace": seed_context.workspace.as_dict(),
                },
            )

            context = WebSocketRuntimeContext.create(root, "system")
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {"type": "resume_session", "session_id": session_id, "request_id": "resume-mismatch"}
            )
            decision = context.runner.ctx.policy.check_command("npm run test -- --watch=false")

            self.assertTrue(handled)
            self.assertEqual(ws.sent[0]["type"], "session_resumed")
            self.assertTrue(decision.allowed)
            self.assertTrue(decision.approval_required)
            self.assertIsNone(decision.matched_prefix_rule)

    async def test_resume_session_rejects_session_allow_without_workspace_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            context = WebSocketRuntimeContext.create(root, "system")
            session_id = "missing-allow-workspace"
            context.session_store.create_session(
                session_id=session_id,
                metadata={"workspace": context.workspace.as_dict()},
            )
            context.session_store.append_event(
                session_id,
                "permission_decision",
                {
                    "turn_id": "turn-1",
                    "request_id": "call-1",
                    "tool": "run_command",
                    "approved": True,
                    "scope": "session",
                    "prefix_rule": ["npm", "run", "test"],
                },
            )
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {"type": "resume_session", "session_id": session_id, "request_id": "resume-missing"}
            )

            self.assertTrue(handled)
            self.assertEqual(ws.sent[0]["type"], "workspace_error")
            self.assertEqual(ws.sent[0]["requested_session_id"], session_id)
            self.assertIn("permission_decision 缺少 workspace snapshot", ws.sent[0]["message"])

    async def test_resume_session_rejects_legacy_workspace_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            context = WebSocketRuntimeContext.create(root, "system")
            session_id = "legacy-session"
            context.session_store.create_session(
                session_id=session_id,
                metadata={
                    "workspace": {
                        "root": root.as_posix(),
                        "current_dir": root.as_posix(),
                        "display_name": "workspace",
                        "git_root": None,
                        "allowed_roots": [],
                    }
                },
            )
            context.session_store.append_event(
                session_id,
                "user_message",
                {"turn_id": "turn-1", "content": "legacy hello"},
            )
            ws = FakeWebSocket()
            dispatcher = WebSocketRequestDispatcher(ws, context)

            handled = await dispatcher.handle_control_packet(
                {"type": "resume_session", "session_id": session_id, "request_id": "resume-old"}
            )

            self.assertTrue(handled)
            self.assertEqual(ws.sent[0]["type"], "workspace_error")
            self.assertEqual(ws.sent[0]["requested_session_id"], session_id)
            self.assertIn("selected_root", ws.sent[0]["message"])


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

        decision = await wait_for_permission_decision(
            ws,
            "session-1",
            "turn-1",
            "request-1",
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.feedback, "换个方案")
        self.assertEqual([event["type"] for event in ws.sent], ["session_busy", "error"])
        self.assertEqual(ws.sent[1]["received_request_id"], "other")

    async def test_wait_for_permission_decision_reports_busy_for_user_input(self) -> None:
        ws = FakeWebSocket(
            [
                {"type": "user_input", "content": "next"},
                {
                    "type": "permission_decision",
                    "request_id": "request-1",
                    "approved": False,
                },
            ]
        )

        decision = await wait_for_permission_decision(
            ws,
            "session-1",
            "turn-1",
            "request-1",
        )

        self.assertFalse(decision.approved)
        self.assertEqual(ws.sent[0]["type"], "session_busy")

    async def test_wait_for_permission_decision_accepts_cancel_turn(self) -> None:
        ws = FakeWebSocket(
            [
                {
                    "type": "cancel_turn",
                    "turn_id": "turn-1",
                    "request_id": "request-1",
                },
            ]
        )

        with self.assertRaises(TurnCancelled):
            await wait_for_permission_decision(
                ws,
                "session-1",
                "turn-1",
                "request-1",
            )

    async def test_wait_for_permission_decision_accepts_session_prefix_scope(self) -> None:
        ws = FakeWebSocket(
            [
                {
                    "type": "permission_decision",
                    "request_id": "request-1",
                    "approved": True,
                    "scope": "session",
                    "prefix_rule": ["npm", "run", "test"],
                },
            ]
        )

        decision = await wait_for_permission_decision(
            ws,
            "session-1",
            "turn-1",
            "request-1",
        )

        self.assertTrue(decision.approved)
        self.assertEqual(decision.scope, "session")
        self.assertEqual(decision.prefix_rule, ("npm", "run", "test"))

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
        self.assertEqual([event["type"] for event in ws.sent], ["session_busy", "error"])
        self.assertEqual(ws.sent[1]["received_request_id"], "other")

    async def test_wait_for_clarification_response_accepts_cancel_turn(self) -> None:
        ws = FakeWebSocket(
            [
                {
                    "type": "cancel_turn",
                    "turn_id": "turn-1",
                    "request_id": "request-1",
                },
            ]
        )

        with self.assertRaises(TurnCancelled):
            await wait_for_clarification_response(
                ws,
                "session-1",
                "turn-1",
                "request-1",
            )

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

    async def test_emit_tool_result_records_patch_lifecycle_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            store = SessionStore(project_root)
            store.create_session(session_id="session-1")
            change = build_file_change(
                "README.md",
                "old\n",
                "new\n",
                existed_before=True,
                exists_after=True,
            )
            proposal = build_patch_proposal(
                session_id="session-1",
                turn_id="turn-1",
                cwd=project_root,
                changes=[change],
                patch_id="patch-1",
                summary="更新 README",
            )
            PatchStore(project_root / ".codex-mini" / "patches").save(proposal)
            ws = FakeWebSocket()

            await emit_tool_result(
                ws,
                store,
                ToolResult(
                    ok=True,
                    content="preview",
                    metadata={
                        "patch_id": "patch-1",
                        "patch_status": PatchStatus.PROPOSED.value,
                        "before": "不应进入事件",
                        "after": "不应进入事件",
                    },
                ),
                SessionRuntimeState("session-1"),
                "session-1",
                "turn-1",
                "request-1",
                "write_file",
            )

            self.assertEqual([event["type"] for event in ws.sent], ["tool_call_result", "patch_proposed"])
            patch_event = ws.sent[1]
            self.assertEqual(patch_event["patch_id"], "patch-1")
            self.assertEqual(patch_event["summary"], "更新 README")
            self.assertEqual(patch_event["changed_paths"], ["README.md"])
            self.assertIn("unified_diff", patch_event["changes"][0])
            self.assertNotIn("before", patch_event["changes"][0])
            self.assertNotIn("after", patch_event["changes"][0])
            self.assertNotIn("before", patch_event["metadata"])
            self.assertNotIn("after", patch_event["metadata"])

            events = store.load_events("session-1")
            self.assertEqual(events[-2].type, "tool_call_result")
            self.assertEqual(events[-1].type, "patch_proposed")
            self.assertEqual(events[-1].payload["patch_id"], "patch-1")

    async def test_emit_tool_result_records_patch_rolled_back_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            store = SessionStore(project_root)
            store.create_session(session_id="session-1")
            proposal = build_patch_proposal(
                session_id="session-1",
                turn_id="turn-1",
                cwd=project_root,
                changes=[
                    build_file_change(
                        "README.md",
                        "old\n",
                        "new\n",
                        existed_before=True,
                        exists_after=True,
                    )
                ],
                patch_id="patch-rollback-1",
                summary="更新 README",
            ).with_state(status=PatchStatus.ROLLED_BACK, rolled_back=True)
            PatchStore(project_root / ".codex-mini" / "patches").save(proposal)
            ws = FakeWebSocket()

            await emit_tool_result(
                ws,
                store,
                ToolResult(
                    ok=True,
                    content="rolled back",
                    metadata={
                        "patch_id": "patch-rollback-1",
                        "patch_status": PatchStatus.ROLLED_BACK.value,
                    },
                ),
                SessionRuntimeState("session-1"),
                "session-1",
                "turn-2",
                "request-2",
                "rollback_patch",
            )

            self.assertEqual([event["type"] for event in ws.sent], ["tool_call_result", "patch_rolled_back"])
            self.assertEqual(ws.sent[1]["patch_id"], "patch-rollback-1")
            self.assertEqual(store.load_events("session-1")[-1].type, "patch_rolled_back")

    async def test_patch_approval_request_event_uses_stored_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            store = SessionStore(project_root)
            store.create_session(session_id="session-1")
            proposal = build_patch_proposal(
                session_id="session-1",
                turn_id="turn-1",
                cwd=project_root,
                changes=[
                    build_file_change(
                        "app.py",
                        "print('old')\n",
                        "print('new')\n",
                        existed_before=True,
                        exists_after=True,
                    )
                ],
                patch_id="patch-approval-1",
                summary="更新 app.py",
            )
            PatchStore(project_root / ".codex-mini" / "patches").save(proposal)
            ws = FakeWebSocket()

            await _emit_patch_approval_request_event(
                ws,
                store,
                session_id="session-1",
                turn_id="turn-1",
                request_id="request-apply",
                tool_name="apply_patch",
                arguments='{"patch_id": "patch-approval-1"}',
                metadata={"permission_action": "ask", "before": "不应进入事件"},
            )

            self.assertEqual([event["type"] for event in ws.sent], ["patch_approval_request"])
            self.assertEqual(ws.sent[0]["patch_id"], "patch-approval-1")
            self.assertEqual(ws.sent[0]["patch_status"], PatchStatus.PROPOSED.value)
            self.assertEqual(ws.sent[0]["tool"], "apply_patch")
            self.assertEqual(ws.sent[0]["metadata"]["action"], "apply")
            self.assertNotIn("before", ws.sent[0]["metadata"])
            self.assertEqual(store.load_events("session-1")[-1].type, "patch_approval_request")

    async def test_patch_approval_request_event_marks_rollback_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            store = SessionStore(project_root)
            store.create_session(session_id="session-1")
            proposal = build_patch_proposal(
                session_id="session-1",
                turn_id="turn-1",
                cwd=project_root,
                changes=[
                    build_file_change(
                        "app.py",
                        "print('old')\n",
                        "print('new')\n",
                        existed_before=True,
                        exists_after=True,
                    )
                ],
                patch_id="patch-rollback-approval",
                summary="更新 app.py",
            ).with_state(status=PatchStatus.APPLIED, applied_at=1.0)
            PatchStore(project_root / ".codex-mini" / "patches").save(proposal)
            ws = FakeWebSocket()

            await _emit_patch_approval_request_event(
                ws,
                store,
                session_id="session-1",
                turn_id="turn-2",
                request_id="request-rollback",
                tool_name="rollback_patch",
                arguments='{"patch_id": "patch-rollback-approval"}',
                metadata={"permission_action": "ask"},
            )

            self.assertEqual([event["type"] for event in ws.sent], ["patch_approval_request"])
            self.assertEqual(ws.sent[0]["patch_id"], "patch-rollback-approval")
            self.assertEqual(ws.sent[0]["metadata"]["action"], "rollback")


if __name__ == "__main__":
    unittest.main()

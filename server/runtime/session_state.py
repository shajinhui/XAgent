"""WebSocket session 的内存状态与延迟持久化。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List

from context_manager import ContextManager
from server.protocol.events import EVENT_SCHEMA_VERSION
from session import SessionRecord, SessionStore
from session.models import TranscriptEvent
from tools.core.catalog import build_default_registry
from tools.core.registry import ToolRegistry
from tools.core.runner import ToolRunner, create_tool_context
from workspace import WorkspaceContext
from workspace.instructions import render_system_prompt_with_project_instructions
from workspace.project_config import default_project_policy


@dataclass
class SessionRuntimeState:
    """当前连接内的会话控制状态。"""

    session_id: str
    active_turn_id: str | None = None
    turn_started_at: float | None = None
    cancellation_requested: bool = False
    suspended: bool = False
    suspended_category: str | None = None
    suspended_detail: str | None = None
    suspended_at: float | None = None

    def as_dict(self) -> Dict[str, Any]:
        """转换为前端事件可直接消费的 session_state。"""

        return {
            "status": "suspended" if self.suspended else "active",
            "turn_in_progress": self.active_turn_id is not None,
            "active_turn_id": self.active_turn_id,
            "turn_started_at": self.turn_started_at,
            "cancellation_requested": self.cancellation_requested,
            "suspended": self.suspended,
            "suspended_category": self.suspended_category,
            "suspended_detail": self.suspended_detail,
            "suspended_at": self.suspended_at,
        }

    def start_turn(self, turn_id: str, *, started_at: float | None = None) -> None:
        """标记当前 session 正在执行一轮 user_input。"""

        self.active_turn_id = turn_id
        self.turn_started_at = started_at or time.time()
        self.cancellation_requested = False

    def finish_turn(self) -> None:
        """清理当前 turn 的运行中状态。"""

        self.active_turn_id = None
        self.turn_started_at = None
        self.cancellation_requested = False

    def request_cancellation(self) -> None:
        """记录用户已请求取消当前 turn。"""

        self.cancellation_requested = True

    def suspend(self, category: str | None, detail: str, *, suspended_at: float | None = None) -> None:
        """将当前会话置为挂起，阻止后续 user_input 继续执行。"""

        self.suspended = True
        self.suspended_category = category
        self.suspended_detail = detail
        self.suspended_at = suspended_at or time.time()
        self.finish_turn()

    def resume(self) -> None:
        """解除挂起状态。"""

        self.suspended = False
        self.suspended_category = None
        self.suspended_detail = None
        self.suspended_at = None


def recover_session_runtime_state(
    session_id: str,
    events: list[TranscriptEvent],
) -> SessionRuntimeState:
    """从 transcript 恢复可跨进程保留的会话控制状态。"""

    state = SessionRuntimeState(session_id=session_id)
    for event in events:
        if event.type == "turn_started":
            turn_id = event.payload.get("turn_id")
            if isinstance(turn_id, str) and turn_id.strip():
                state.start_turn(turn_id, started_at=event.timestamp)
            continue

        if event.type in {"final_answer", "turn_cancelled", "runtime_error"}:
            state.finish_turn()
            continue

        if event.type == "session_suspended":
            state.suspend(
                _optional_text(event.payload.get("category")),
                _optional_text(event.payload.get("detail")) or "会话已挂起",
                suspended_at=event.timestamp,
            )
            continue

        if event.type == "session_resumed":
            if _payload_says_suspended(event.payload):
                state.suspend(
                    _payload_session_text(event.payload, "suspended_category"),
                    _payload_session_text(event.payload, "suspended_detail") or "会话已挂起",
                    suspended_at=_payload_session_number(event.payload, "suspended_at"),
                )
            else:
                state.resume()
            state.finish_turn()
    return state


def create_websocket_session(
    workspace: WorkspaceContext,
    system_prompt: str,
) -> tuple[str, SessionRuntimeState, ToolRegistry, ToolRunner, ContextManager]:
    """创建 WebSocket 内存会话，但不立即写入磁盘。

    这里创建的是新 session 的基础运行件：session 状态、工具目录、工具执行器、
    以及模型可见 history。真正的磁盘 session 记录等首条非空 user_input 再创建。
    """

    if workspace.session_store is None:
        raise ValueError("workspace session store is not initialized")

    session_id = str(uuid.uuid4())
    session_state = SessionRuntimeState(session_id=session_id)
    registry = build_default_registry()
    project_policy = workspace.project_policy or default_project_policy()
    runner = ToolRunner(
        registry,
        create_tool_context(
            workspace.selected_root,
            session_id,
            project_root=workspace.project_root,
            current_dir=workspace.current_dir,
            additional_roots=workspace.additional_roots,
            exec_policy=project_policy.exec_policy,
            network_policy=project_policy.network_policy,
            permission_profile=project_policy.permission_profile,
            approval_policy=project_policy.approval_policy,
        ),
    )
    rendered_system_prompt, _instructions = render_system_prompt_with_project_instructions(
        system_prompt,
        workspace,
    )
    history = ContextManager.with_system_prompt(rendered_system_prompt)

    return session_id, session_state, registry, runner, history


def persist_websocket_session(
    session_store: SessionStore,
    session_id: str,
    workspace: WorkspaceContext,
) -> SessionRecord:
    """在首条有效用户输入到达时，补建磁盘 session 记录。"""

    try:
        return session_store.get_session(session_id)
    except KeyError:
        return session_store.create_session(
            session_id=session_id,
            metadata={
                "transport": "websocket",
                "schema_version": EVENT_SCHEMA_VERSION,
                "workspace": workspace.as_dict(),
            },
        )


def _optional_text(value: Any) -> str | None:
    """把 transcript payload 中的可选文本字段收束成干净字符串。"""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _payload_says_suspended(payload: dict[str, Any]) -> bool:
    """读取 transcript 中记录的 session_state.suspended。"""

    session_state = payload.get("session_state")
    return isinstance(session_state, dict) and session_state.get("suspended") is True


def _payload_session_text(payload: dict[str, Any], key: str) -> str | None:
    """从 payload.session_state 中读取可选文本字段。"""

    session_state = payload.get("session_state")
    if not isinstance(session_state, dict):
        return None
    return _optional_text(session_state.get(key))


def _payload_session_number(payload: dict[str, Any], key: str) -> float | None:
    """从 payload.session_state 中读取可选数字字段。"""

    session_state = payload.get("session_state")
    if not isinstance(session_state, dict):
        return None
    value = session_state.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None

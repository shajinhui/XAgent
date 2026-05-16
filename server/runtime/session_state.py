"""WebSocket session 的内存状态与延迟持久化。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List

from server.protocol.events import EVENT_SCHEMA_VERSION
from session import SessionRecord, SessionStore
from tools.registry import ToolRegistry
from workspace import WorkspaceContext


@dataclass
class SessionRuntimeState:
    """当前连接内的会话控制状态。"""

    session_id: str
    suspended: bool = False
    suspended_category: str | None = None
    suspended_detail: str | None = None
    suspended_at: float | None = None

    def as_dict(self) -> Dict[str, Any]:
        """转换为前端事件可直接消费的 session_state。"""

        return {
            "status": "suspended" if self.suspended else "active",
            "suspended": self.suspended,
            "suspended_category": self.suspended_category,
            "suspended_detail": self.suspended_detail,
            "suspended_at": self.suspended_at,
        }

    def suspend(self, category: str | None, detail: str) -> None:
        """将当前会话置为挂起，阻止后续 user_input 继续执行。"""

        self.suspended = True
        self.suspended_category = category
        self.suspended_detail = detail
        self.suspended_at = time.time()

    def resume(self) -> None:
        """解除挂起状态。"""

        self.suspended = False
        self.suspended_category = None
        self.suspended_detail = None
        self.suspended_at = None


def create_websocket_session(
    workspace: WorkspaceContext,
    system_prompt: str,
) -> tuple[str, SessionRuntimeState, ToolRegistry, List[Dict[str, Any]]]:
    """创建 WebSocket 内存会话，但不立即写入磁盘。"""

    if workspace.session_store is None:
        raise ValueError("workspace session store is not initialized")

    session_id = str(uuid.uuid4())
    session_state = SessionRuntimeState(session_id=session_id)
    registry = ToolRegistry(project_root=workspace.root, session_id=session_id)
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    return session_id, session_state, registry, messages


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

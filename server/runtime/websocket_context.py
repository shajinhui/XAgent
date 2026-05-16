"""WebSocket 连接内的可变运行上下文。

一个 WebSocket 连接会在运行中切换 workspace、恢复旧 session、创建新 session。
这些状态需要集中管理，避免 `server.app` 持有一堆易错的局部变量。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from context_manager import ContextManager
from session import SessionStore, recover_session_messages
from server.runtime.session_state import SessionRuntimeState, create_websocket_session
from server.views.session_summary import session_display_messages, summarize_session_record
from tools.core.catalog import build_default_registry
from tools.core.registry import ToolRegistry
from tools.core.runner import ToolRunner, create_tool_context
from workspace import WorkspaceContext, WorkspaceManager


@dataclass
class WebSocketRuntimeContext:
    """聚合当前 WebSocket 连接绑定的可变状态。

    这是连接级上下文：它会随着 open_workspace、new_session、resume_session 改变。
    单轮 user_input 的不可散传参数会再收束到 `session.turn_context.TurnContext`。
    """

    workspace_manager: WorkspaceManager
    workspace: WorkspaceContext
    session_store: SessionStore
    system_prompt: str
    session_id: str
    session_state: SessionRuntimeState
    registry: ToolRegistry
    runner: ToolRunner
    history: ContextManager
    session_persisted: bool = False

    @property
    def messages(self) -> List[Dict[str, Any]]:
        """兼容少量旧调用方；正式的模型历史入口是 `history`。"""

        return self.history.messages

    @messages.setter
    def messages(self, messages: List[Dict[str, Any]]) -> None:
        """把恢复出的 message 列表重新包装为 ContextManager。"""

        self.history = ContextManager.from_messages(messages)

    @classmethod
    def create(cls, project_root: Path, system_prompt: str) -> "WebSocketRuntimeContext":
        """基于默认项目根目录创建连接初始上下文。"""

        workspace_manager = WorkspaceManager(project_root)
        workspace = workspace_manager.open()
        session_store = _require_session_store(workspace)
        session_id, session_state, registry, runner, history = create_websocket_session(
            workspace,
            system_prompt,
        )
        return cls(
            workspace_manager=workspace_manager,
            workspace=workspace,
            session_store=session_store,
            system_prompt=system_prompt,
            session_id=session_id,
            session_state=session_state,
            registry=registry,
            runner=runner,
            history=history,
        )

    def start_new_session(self) -> Dict[str, Any]:
        """切到新的空内存会话，并返回切换前的 session_state。"""

        previous_state = self.session_state.as_dict()
        self.session_id, self.session_state, self.registry, self.runner, self.history = (
            create_websocket_session(self.workspace, self.system_prompt)
        )
        self.session_persisted = False
        return previous_state

    def switch_workspace(self, path: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """打开新的 workspace，并重建 session store、registry 和消息上下文。"""

        previous_workspace = self.workspace.as_dict()
        previous_state = self.session_state.as_dict()
        self.workspace = self.workspace_manager.open(path)
        self.session_store = _require_session_store(self.workspace)
        self.session_id, self.session_state, self.registry, self.runner, self.history = (
            create_websocket_session(self.workspace, self.system_prompt)
        )
        self.session_persisted = False
        return previous_workspace, previous_state

    def resume_session_from_disk(self, session_id: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """从 transcript 恢复模型上下文，并返回前端可展示的消息和摘要。"""

        self.history = ContextManager.from_messages(
            recover_session_messages(
                self.session_store,
                session_id,
                self.system_prompt,
            )
        )
        target_record = self.session_store.get_session(session_id)
        target_events = self.session_store.load_events(session_id)
        self.session_id = session_id
        self.session_state = SessionRuntimeState(session_id=session_id)
        self.registry = build_default_registry()
        self.runner = ToolRunner(
            self.registry,
            create_tool_context(
                self.workspace.selected_root,
                session_id,
                project_root=self.workspace.project_root,
                current_dir=self.workspace.current_dir,
            ),
        )
        self.session_persisted = True
        return (
            session_display_messages(target_events),
            summarize_session_record(target_record, target_events),
        )


def _require_session_store(workspace: WorkspaceContext) -> SessionStore:
    """确保 WorkspaceContext 已绑定 SessionStore。"""

    if workspace.session_store is None:
        raise RuntimeError("workspace session store is not initialized")
    return workspace.session_store

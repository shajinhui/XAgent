"""WebSocket 连接内的可变运行上下文。

一个 WebSocket 连接会在运行中切换 workspace、恢复旧 session、创建新 session。
这些状态需要集中管理，避免 `server.app` 持有一堆易错的局部变量。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, cast

from context_manager import ContextManager
from session import SessionStore, recover_session_messages
from security.exec_policy import ExecPolicy, ExecPolicyRule
from server.runtime.session_allowlist import recover_session_allow_rules
from server.runtime.session_state import (
    SessionRuntimeState,
    create_websocket_session,
    recover_session_runtime_state,
)
from server.views.session_summary import session_display_messages, summarize_session_record
from tools.core.catalog import build_default_registry
from tools.core.registry import ToolRegistry
from tools.core.runner import ToolRunner, create_tool_context
from workspace import (
    PermissionMode,
    TrustLevel,
    WorkspaceContext,
    WorkspaceManager,
    WorkspaceValidationError,
)
from workspace.instructions import render_system_prompt_with_project_instructions
from workspace.permission_modes import policy_for_permission_mode
from workspace.project_config import default_project_policy, load_project_policy


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
    permission_mode: PermissionMode = PermissionMode.REQUEST_APPROVAL
    session_persisted: bool = False
    last_diff_tracker: Any = None  # 保存最后一次 turn 的 diff_tracker
    pending_plan: Dict[str, Any] | None = None

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
        _apply_permission_mode_to_workspace(workspace, PermissionMode.REQUEST_APPROVAL)
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

        # 自动生成上一个会话的摘要
        if self.session_persisted and self.session_id:
            self._auto_summarize_session(self.session_id)

        self.session_id, self.session_state, self.registry, self.runner, self.history = (
            create_websocket_session(self.workspace, self.system_prompt)
        )
        self.session_persisted = False
        self.pending_plan = None
        return previous_state

    def switch_workspace(self, path: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """打开新的 workspace，并重建 session store、registry 和消息上下文。"""

        previous_workspace = self.workspace.as_dict()
        previous_state = self.session_state.as_dict()
        next_workspace = self.workspace_manager.open(path)
        self._apply_permission_mode_to_workspace(next_workspace)
        self.workspace = next_workspace
        self.session_store = _require_session_store(next_workspace)
        self.session_id, self.session_state, self.registry, self.runner, self.history = (
            create_websocket_session(self.workspace, self.system_prompt)
        )
        self.session_persisted = False
        self.pending_plan = None
        return previous_workspace, previous_state

    def change_directory(self, path: str) -> Dict[str, Any]:
        """更新当前执行目录，并刷新工具上下文中的 filesystem policy。"""

        previous_workspace = self.workspace.as_dict()
        self.workspace.change_current_dir(path)
        self.refresh_tool_runner()
        self.refresh_history_system_prompt()
        return previous_workspace

    def add_workspace_directory(self, path: str, access: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """显式加入额外目录，并刷新工具可见的读写根。"""

        previous_workspace = self.workspace.as_dict()
        root = self.workspace.add_additional_root(path, cast(Literal["read", "write"], access))
        self.refresh_tool_runner()
        self.refresh_history_system_prompt()
        return previous_workspace, root.as_dict()

    def change_workspace_trust(self, level: TrustLevel) -> Dict[str, Any]:
        """切换当前 workspace 的信任状态，并按新 trust gate 重新加载策略。"""

        previous_workspace = self.workspace.as_dict()
        if level == TrustLevel.TRUSTED:
            trust = self.workspace_manager.trust_project(self.workspace.project_root)
        elif level == TrustLevel.UNTRUSTED:
            trust = self.workspace_manager.untrust_project(self.workspace.project_root)
        else:
            raise WorkspaceValidationError(f"不支持切换到 trust level: {level.value}")

        selected_root = self.workspace.selected_root
        current_dir = self.workspace.current_dir
        additional_roots = list(self.workspace.additional_roots)
        next_workspace = self.workspace_manager.open(selected_root, trust_override=trust)
        next_workspace.additional_roots = additional_roots
        next_workspace.change_current_dir(current_dir)
        self._apply_permission_mode_to_workspace(next_workspace)
        self.workspace = next_workspace
        self.session_store = _require_session_store(next_workspace)
        # trust 边界变化时不继承旧 exec policy，避免保留已撤销的项目策略规则。
        project_policy = next_workspace.project_policy or default_project_policy()
        self.refresh_tool_runner(exec_policy=project_policy.exec_policy)
        self.refresh_history_system_prompt()
        return previous_workspace

    def change_permission_mode(self, mode: PermissionMode) -> Dict[str, Any]:
        """应用用户显式选择的权限模式，并刷新工具运行边界。"""

        previous_workspace = self.workspace.as_dict()
        self._apply_permission_mode_to_workspace(self.workspace, mode=mode)
        self.permission_mode = mode
        self.refresh_tool_runner(exec_policy=self.workspace.project_policy.exec_policy)
        self.refresh_history_system_prompt()
        return previous_workspace

    def _apply_permission_mode_to_workspace(
        self,
        workspace: WorkspaceContext,
        *,
        mode: PermissionMode | None = None,
    ) -> None:
        """把连接级权限模式应用到指定 workspace。"""

        target_mode = mode or self.permission_mode
        _apply_permission_mode_to_workspace(workspace, target_mode)

    def current_system_prompt(self) -> str:
        """按当前 workspace 生成模型可见 system prompt。"""

        rendered, _instructions = render_system_prompt_with_project_instructions(
            self.system_prompt,
            self.workspace,
        )
        return rendered

    def refresh_history_system_prompt(self) -> str:
        """刷新历史中的 system prompt，避免 cwd/instructions 变化后上下文过期。"""

        rendered = self.current_system_prompt()
        self.history.replace_system_prompt(rendered)
        return rendered

    def refresh_tool_runner(self, *, exec_policy: ExecPolicy | None = None) -> None:
        """按最新 workspace policy 重建 ToolRunner，可选择替换命令策略边界。"""

        previous_ctx = self.runner.ctx
        project_policy = self.workspace.project_policy or default_project_policy()
        active_exec_policy = exec_policy if exec_policy is not None else previous_ctx.policy.exec_policy
        self.runner = ToolRunner(
            self.registry,
            create_tool_context(
                self.workspace.selected_root,
                self.session_id,
                project_root=self.workspace.project_root,
                current_dir=self.workspace.current_dir,
                additional_roots=self.workspace.additional_roots,
                exec_policy=active_exec_policy,
                network_policy=project_policy.network_policy,
                permission_profile=project_policy.permission_profile,
                approval_policy=project_policy.approval_policy,
                circuit_breaker=previous_ctx.circuit_breaker,
            ),
        )

    def resume_session_from_disk(
        self,
        session_id: str,
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """从 transcript 恢复模型上下文，并返回前端可展示的消息和摘要。"""

        target_record = self.session_store.get_session(session_id)
        target_events = self.session_store.load_events(session_id)
        restored_workspace = self.workspace_manager.restore_from_snapshot(
            _workspace_snapshot_from_session(target_record.metadata or {}, target_events)
        )
        self._apply_permission_mode_to_workspace(restored_workspace)
        self.workspace = restored_workspace
        self.session_store = _require_session_store(restored_workspace)
        self.history = ContextManager.from_messages(
            recover_session_messages(
                self.session_store,
                session_id,
                self.current_system_prompt(),
            )
        )
        self.session_id = session_id
        self.session_state = recover_session_runtime_state(session_id, target_events)
        self.registry = build_default_registry()
        self.pending_plan = None
        project_policy = self.workspace.project_policy or default_project_policy()
        session_allow_rules = recover_session_allow_rules(
            target_events,
            self.workspace.as_dict(),
        )
        exec_policy = _exec_policy_with_session_allow(
            project_policy.exec_policy,
            session_allow_rules,
        )
        self.runner = ToolRunner(
            self.registry,
            create_tool_context(
                self.workspace.selected_root,
                session_id,
                project_root=self.workspace.project_root,
                current_dir=self.workspace.current_dir,
                additional_roots=self.workspace.additional_roots,
                exec_policy=exec_policy,
                network_policy=project_policy.network_policy,
                permission_profile=project_policy.permission_profile,
                approval_policy=project_policy.approval_policy,
            ),
        )
        self.session_persisted = True
        return (
            session_display_messages(target_events),
            summarize_session_record(target_record, target_events),
        )

    def _auto_summarize_session(self, session_id: str) -> None:
        """自动生成会话摘要并保存到 workspace memory。"""
        try:
            from memory.indexer import update_memory_index
            from memory.store import MemoryStore
            from memory.summarizer import extract_task_state, summarize_session

            memory_dir = self.workspace.project_root / ".codex-mini" / "memory"
            memory_store = MemoryStore(memory_dir)

            writer = self.session_store.writer(session_id)
            events = writer.load()

            # 1. 保存会话摘要
            summary = summarize_session(events)
            task_state = extract_task_state(events)
            memory_store.save_session_memory(session_id, summary, {"task_state": task_state})

            # 用户偏好只通过显式 remember_preference 写入，避免误记临时或敏感信息。
            update_memory_index(memory_dir)
        except Exception:
            pass  # 静默失败，不影响正常流程


def _require_session_store(workspace: WorkspaceContext) -> SessionStore:
    """确保 WorkspaceContext 已绑定 SessionStore。"""

    if workspace.session_store is None:
        raise RuntimeError("workspace session store is not initialized")
    return workspace.session_store


def _apply_permission_mode_to_workspace(
    workspace: WorkspaceContext,
    mode: PermissionMode,
) -> None:
    """把全局权限模式投影成当前 workspace 的实际执行策略。"""

    assert workspace.trust is not None
    project_policy = load_project_policy(workspace.project_root, workspace.trust)
    workspace.project_policy = policy_for_permission_mode(mode, project_policy)


def _workspace_snapshot_from_session(
    metadata: Dict[str, Any],
    events: list[Any],
) -> Dict[str, Any]:
    """取当前 schema 的 workspace 快照，旧字段不再做兼容读取。"""

    snapshot = metadata.get("workspace")
    if not isinstance(snapshot, dict):
        raise WorkspaceValidationError("session metadata missing workspace snapshot")

    for event in events:
        if event.type != "workspace_policy_changed":
            continue
        event_workspace = event.payload.get("workspace")
        if not isinstance(event_workspace, dict):
            raise WorkspaceValidationError("workspace_policy_changed missing workspace snapshot")
        snapshot = event_workspace
    return snapshot


def _exec_policy_with_session_allow(
    base_policy: ExecPolicy,
    session_allow_rules: tuple[ExecPolicyRule, ...],
) -> ExecPolicy:
    """把 transcript 恢复出的 session allow 追加到当前项目命令策略。"""

    if not session_allow_rules:
        return base_policy
    return ExecPolicy(
        (*base_policy.rules, *session_allow_rules),
        protect_paths=base_policy.protect_paths,
    )

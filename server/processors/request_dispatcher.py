"""
WebSocket 控制请求分发器。

只处理不会直接进入模型推理的控制类 packet，例如打开工作区、新建/删除/恢复会话、
列出历史会话和标题生成。真正的 `user_input` 会返回 False，交回 WebSocket 主循环。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from server.processors.title_processor import generate_conversation_title, normalize_title_messages
from server.protocol.events import build_event
from server.runtime.transcript_events import record_transcript_event
from server.runtime.websocket_context import WebSocketRuntimeContext
from server.views.session_summary import list_session_summaries
from workspace import TrustLevel, WorkspaceValidationError


class WebSocketRequestDispatcher:
    """根据 packet type 路由控制请求，并统一发送协议事件。"""

    def __init__(self, ws: Any, context: WebSocketRuntimeContext) -> None:
        self.ws = ws
        self.context = context

    async def handle_invalid_packet(self) -> None:
        """处理无法解析成 JSON object 的客户端输入。"""

        if self.context.session_persisted:
            record_transcript_event(
                self.context.session_store,
                self.context.session_id,
                "runtime_error",
                {
                    "turn_id": "system",
                    "message": "invalid JSON packet",
                    "received_type": "invalid_json",
                },
            )
        await self.ws.send_json(
            build_event(
                "error",
                self.context.session_id,
                "system",
                message="invalid JSON packet",
                received_type="invalid_json",
            )
        )

    async def handle_control_packet(self, packet: Dict[str, Any]) -> bool:
        """尝试处理控制类 packet。

        返回 True 表示请求已被消费；返回 False 表示这是 `user_input`，
        调用方需要继续进入模型回合。
        """

        packet_type = packet.get("type")
        if packet_type == "user_input":
            return False

        if packet_type == "open_workspace":
            await self._handle_open_workspace(packet)
            return True
        if packet_type == "change_directory":
            await self._handle_change_directory(packet)
            return True
        if packet_type == "add_dir":
            await self._handle_add_dir(packet)
            return True
        if packet_type == "trust_workspace":
            await self._handle_workspace_trust_change(packet, TrustLevel.TRUSTED)
            return True
        if packet_type == "untrust_workspace":
            await self._handle_workspace_trust_change(packet, TrustLevel.UNTRUSTED)
            return True
        if packet_type == "new_session":
            await self._handle_new_session(packet)
            return True
        if packet_type == "list_sessions":
            await self._handle_list_sessions(packet)
            return True
        if packet_type == "delete_session":
            await self._handle_delete_session(packet)
            return True
        if packet_type == "resume_session":
            await self._handle_resume_session(packet)
            return True
        if packet_type == "conversation_title_request":
            await self._handle_conversation_title_request(packet)
            return True

        await self._send_error(
            packet,
            message="unsupported event type",
            received_type=packet.get("type"),
        )
        return True

    async def _handle_open_workspace(self, packet: Dict[str, Any]) -> None:
        """切换当前 workspace，并为新 workspace 创建新的内存会话。"""

        request_id = _request_id(packet)
        requested_path = str(packet.get("path") or "").strip()
        if not requested_path:
            await self._send_error(
                packet,
                request_id=request_id,
                message="workspace path is empty",
            )
            return

        try:
            previous_workspace, previous_state = self.context.switch_workspace(requested_path)
        except WorkspaceValidationError as exc:
            await self._record_runtime_error(
                packet,
                request_id=request_id,
                message=str(exc),
                requested_workspace=requested_path,
            )
            await self.ws.send_json(
                build_event(
                    "workspace_error",
                    self.context.session_id,
                    _turn_id(packet),
                    request_id=request_id,
                    message=str(exc),
                    requested_workspace=requested_path,
                    workspace=self.context.workspace.as_dict(),
                )
            )
            return

        await self.ws.send_json(
            build_event(
                "workspace_changed",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                previous_workspace=previous_workspace,
                workspace=self.context.workspace.as_dict(),
                previous_state=previous_state,
                session_state=self.context.session_state.as_dict(),
                tools=self.context.registry.metadata(),
            )
        )

    async def _handle_change_directory(self, packet: Dict[str, Any]) -> None:
        """切换当前执行目录；不会创建新 session，也不会自动扩大权限。"""

        request_id = _request_id(packet)
        requested_path = str(packet.get("path") or "").strip()
        if not requested_path:
            await self._send_error(
                packet,
                request_id=request_id,
                message="directory path is empty",
            )
            return

        try:
            previous_workspace = self.context.change_directory(requested_path)
        except WorkspaceValidationError as exc:
            await self._record_runtime_error(
                packet,
                request_id=request_id,
                message=str(exc),
                requested_path=requested_path,
            )
            await self.ws.send_json(
                build_event(
                    "workspace_error",
                    self.context.session_id,
                    _turn_id(packet),
                    request_id=request_id,
                    message=str(exc),
                    requested_path=requested_path,
                    workspace=self.context.workspace.as_dict(),
                )
            )
            return

        await self._send_workspace_policy_changed(
            packet,
            request_id=request_id,
            previous_workspace=previous_workspace,
            reason="change_directory",
            current_dir=self.context.workspace.current_dir.as_posix(),
        )

    async def _handle_add_dir(self, packet: Dict[str, Any]) -> None:
        """显式加入 additional root；读写权限由客户端 packet 明确表达。"""

        request_id = _request_id(packet)
        requested_path = str(packet.get("path") or "").strip()
        access = str(packet.get("access") or "").strip()
        if not requested_path:
            await self._send_error(
                packet,
                request_id=request_id,
                message="additional directory path is empty",
            )
            return
        if access not in ("read", "write"):
            await self._send_error(
                packet,
                request_id=request_id,
                message="additional directory access must be read or write",
                requested_path=requested_path,
            )
            return

        try:
            previous_workspace, added_root = self.context.add_workspace_directory(
                requested_path,
                access,
            )
        except WorkspaceValidationError as exc:
            await self._record_runtime_error(
                packet,
                request_id=request_id,
                message=str(exc),
                requested_path=requested_path,
            )
            await self.ws.send_json(
                build_event(
                    "workspace_error",
                    self.context.session_id,
                    _turn_id(packet),
                    request_id=request_id,
                    message=str(exc),
                    requested_path=requested_path,
                    workspace=self.context.workspace.as_dict(),
                )
            )
            return

        await self._send_workspace_policy_changed(
            packet,
            request_id=request_id,
            previous_workspace=previous_workspace,
            reason="add_dir",
            added_root=added_root,
        )

    async def _handle_workspace_trust_change(
        self,
        packet: Dict[str, Any],
        level: TrustLevel,
    ) -> None:
        """显式切换当前 workspace 信任状态，并刷新权限与工具运行边界。"""

        request_id = _request_id(packet)
        try:
            previous_workspace = self.context.change_workspace_trust(level)
        except WorkspaceValidationError as exc:
            await self._record_runtime_error(
                packet,
                request_id=request_id,
                message=str(exc),
                requested_trust_level=level.value,
            )
            await self.ws.send_json(
                build_event(
                    "workspace_error",
                    self.context.session_id,
                    _turn_id(packet),
                    request_id=request_id,
                    message=str(exc),
                    requested_trust_level=level.value,
                    workspace=self.context.workspace.as_dict(),
                )
            )
            return

        reason = "trust_workspace" if level == TrustLevel.TRUSTED else "untrust_workspace"
        await self._send_workspace_policy_changed(
            packet,
            request_id=request_id,
            previous_workspace=previous_workspace,
            reason=reason,
        )

    async def _handle_new_session(self, packet: Dict[str, Any]) -> None:
        """创建新的内存会话；不落盘，直到首条非空 user_input 到达。"""

        previous_state = self.context.start_new_session()
        await self.ws.send_json(
            build_event(
                "session_created",
                self.context.session_id,
                _turn_id(packet),
                request_id=_request_id(packet),
                previous_state=previous_state,
                session_state=self.context.session_state.as_dict(),
                workspace=self.context.workspace.as_dict(),
            )
        )

    async def _handle_list_sessions(self, packet: Dict[str, Any]) -> None:
        """返回当前 workspace 下可展示的历史会话摘要。"""

        raw_limit = packet.get("limit", 20)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 20

        sessions = list_session_summaries(self.context.session_store, limit=limit)
        await self.ws.send_json(
            build_event(
                "sessions_list",
                self.context.session_id,
                _turn_id(packet),
                request_id=_request_id(packet),
                sessions=sessions,
                workspace=self.context.workspace.as_dict(),
            )
        )

    async def _handle_delete_session(self, packet: Dict[str, Any]) -> None:
        """删除指定会话；如果删除的是当前会话，则立即切到新的空内存会话。"""

        request_id = _request_id(packet)
        target_session_id = str(packet.get("session_id") or "").strip()
        requested_workspace = str(packet.get("workspace_path") or "").strip()

        if not target_session_id:
            await self._send_error(
                packet,
                request_id=request_id,
                message="session_id is required",
            )
            return

        if requested_workspace:
            try:
                target_workspace = self.context.workspace_manager.open(requested_workspace)
            except WorkspaceValidationError as exc:
                await self.ws.send_json(
                    build_event(
                        "workspace_error",
                        self.context.session_id,
                        _turn_id(packet),
                        request_id=request_id,
                        message=str(exc),
                        requested_workspace=requested_workspace,
                        workspace=self.context.workspace.as_dict(),
                    )
                )
                return
        else:
            target_workspace = self.context.workspace

        target_store = target_workspace.session_store
        if target_store is None:
            raise RuntimeError("workspace session store is not initialized")

        try:
            target_store.delete_session(target_session_id)
        except KeyError:
            await self._send_error(
                packet,
                request_id=request_id,
                message=f"unknown session: {target_session_id}",
                requested_session_id=target_session_id,
            )
            return

        deleted_current = (
            target_workspace.project_root == self.context.workspace.project_root
            and target_session_id == self.context.session_id
        )
        if deleted_current:
            self.context.start_new_session()

        sessions = list_session_summaries(target_store, limit=30)
        await self.ws.send_json(
            build_event(
                "session_deleted",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                deleted_session_id=target_session_id,
                deleted_current=deleted_current,
                session_state=self.context.session_state.as_dict(),
                workspace=target_workspace.as_dict(),
                sessions=sessions,
            )
        )

    async def _handle_resume_session(self, packet: Dict[str, Any]) -> None:
        """恢复磁盘会话，或在未指定 session_id 时解除当前会话挂起状态。"""

        request_id = _request_id(packet)
        previous_state = self.context.session_state.as_dict()
        target_session_id = str(packet.get("session_id") or "").strip()
        if target_session_id:
            try:
                display_messages, session_summary = self.context.resume_session_from_disk(target_session_id)
            except KeyError:
                await self._send_error(
                    packet,
                    message=f"unknown session: {target_session_id}",
                    requested_session_id=target_session_id,
                )
                return
            except WorkspaceValidationError as exc:
                await self._record_runtime_error(
                    packet,
                    request_id=request_id,
                    message=str(exc),
                    requested_session_id=target_session_id,
                )
                await self.ws.send_json(
                    build_event(
                        "workspace_error",
                        self.context.session_id,
                        _turn_id(packet),
                        request_id=request_id,
                        message=str(exc),
                        requested_session_id=target_session_id,
                        workspace=self.context.workspace.as_dict(),
                    )
                )
                return

            resumed_from_disk = True
        else:
            suspended_category = self.context.session_state.suspended_category
            self.context.session_state.resume()
            self.context.runner.ctx.circuit_breaker.reset(
                self.context.session_id,
                suspended_category,
            )
            display_messages = []
            session_summary = None
            resumed_from_disk = False

        if self.context.session_persisted:
            record_transcript_event(
                self.context.session_store,
                self.context.session_id,
                "session_resumed",
                {
                    "turn_id": _turn_id(packet),
                    "request_id": request_id,
                    "previous_state": previous_state,
                    "session_state": self.context.session_state.as_dict(),
                    "resumed_from_disk": resumed_from_disk,
                    "message_count": len(self.context.messages),
                    "workspace": self.context.workspace.as_dict(),
                },
            )
        await self.ws.send_json(
            build_event(
                "session_resumed",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                previous_state=previous_state,
                session_state=self.context.session_state.as_dict(),
                resumed_from_disk=resumed_from_disk,
                message_count=len(self.context.messages),
                messages=display_messages,
                session=session_summary,
                workspace=self.context.workspace.as_dict(),
            )
        )

    async def _handle_conversation_title_request(self, packet: Dict[str, Any]) -> None:
        """根据前端传来的对话片段生成短标题。"""

        request_id = _request_id(packet)
        title_messages = normalize_title_messages(packet.get("messages"))
        if not title_messages:
            await self._send_error(
                packet,
                default_turn_id="title",
                request_id=request_id,
                message="conversation title messages are empty",
            )
            return

        try:
            title, title_model = generate_conversation_title(title_messages)
        except Exception as exc:
            await self._send_error(
                packet,
                default_turn_id="title",
                request_id=request_id,
                message=f"conversation title request failed: {exc}",
                error_type=type(exc).__name__,
            )
            return

        await self.ws.send_json(
            build_event(
                "conversation_title",
                self.context.session_id,
                _turn_id(packet, "title"),
                request_id=request_id,
                title=title,
                model=title_model,
            )
        )
        if self.context.session_persisted:
            record_transcript_event(
                self.context.session_store,
                self.context.session_id,
                "conversation_title",
                {
                    "turn_id": _turn_id(packet, "title"),
                    "request_id": request_id,
                    "title": title,
                    "model": title_model,
                },
            )

    async def _send_workspace_policy_changed(
        self,
        packet: Dict[str, Any],
        *,
        request_id: str,
        previous_workspace: Dict[str, Any],
        reason: str,
        **payload: Any,
    ) -> None:
        """发送 workspace policy 更新事件，并按需写入 transcript。"""

        event_payload = {
            "request_id": request_id,
            "previous_workspace": previous_workspace,
            "workspace": self.context.workspace.as_dict(),
            "session_state": self.context.session_state.as_dict(),
            "reason": reason,
            **payload,
        }
        if self.context.session_persisted:
            record_transcript_event(
                self.context.session_store,
                self.context.session_id,
                "workspace_policy_changed",
                {
                    "turn_id": _turn_id(packet),
                    **event_payload,
                },
            )
        await self.ws.send_json(
            build_event(
                "workspace_policy_changed",
                self.context.session_id,
                _turn_id(packet),
                **event_payload,
            )
        )

    async def _send_error(
        self,
        packet: Dict[str, Any],
        *,
        default_turn_id: str = "system",
        request_id: str | None = None,
        **payload: Any,
    ) -> None:
        """发送统一 error 事件，并在会话已落盘时记录 runtime_error。"""

        await self._record_runtime_error(
            packet,
            default_turn_id=default_turn_id,
            request_id=request_id,
            **payload,
        )
        await self.ws.send_json(
            build_event(
                "error",
                self.context.session_id,
                _turn_id(packet, default_turn_id),
                request_id=request_id or packet.get("request_id") or str(uuid.uuid4()),
                **payload,
            )
        )

    async def _record_runtime_error(
        self,
        packet: Dict[str, Any],
        *,
        default_turn_id: str = "system",
        request_id: str | None = None,
        **payload: Any,
    ) -> None:
        """只给已持久化会话记录运行时错误，避免空会话因错误被创建。"""

        if not self.context.session_persisted:
            return
        stored_payload = {
            "turn_id": _turn_id(packet, default_turn_id),
            **payload,
        }
        if request_id:
            stored_payload["request_id"] = request_id
        record_transcript_event(
            self.context.session_store,
            self.context.session_id,
            "runtime_error",
            stored_payload,
        )


def _request_id(packet: Dict[str, Any]) -> str:
    """返回数据包中的 request_id，如果缺失则生成新的 UUID。"""
    return packet.get("request_id") or str(uuid.uuid4())


def _turn_id(packet: Dict[str, Any], default: str = "system") -> str:
    """返回数据包中的 turn_id，若缺失返回默认值。"""
    return packet.get("turn_id") or default

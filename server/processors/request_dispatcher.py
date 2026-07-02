"""
WebSocket 控制请求分发器。

只处理不会直接进入模型推理的控制类 packet，例如打开工作区、新建/删除/恢复会话、
列出历史会话和标题生成。真正的 `user_input` 会返回 False，交回 WebSocket 主循环。
"""

from __future__ import annotations

import uuid
import json
import time
from typing import Any, Dict

from memory.store import MemoryStore
from memory.summarizer import extract_task_state, summarize_session
from server.processors.skill_processor import SkillRequestProcessor
from server.processors.task_list_processor import (
    build_plan_document,
    build_plan_summary,
    fallback_task_list,
    generate_task_list,
)
from server.processors.title_processor import generate_conversation_title, normalize_title_messages
from server.protocol.events import build_event
from server.runtime.transcript_events import record_transcript_event
from server.runtime.turn_runner import emit_tool_result
from server.runtime.websocket_context import WebSocketRuntimeContext
from server.views.session_summary import list_session_summaries
from workspace import PermissionMode, TrustLevel, WorkspaceValidationError


class WebSocketRequestDispatcher:
    """根据 packet type 路由控制请求，并统一发送协议事件。"""

    def __init__(self, ws: Any, context: WebSocketRuntimeContext) -> None:
        self.ws = ws
        self.context = context
        self.skill_processor = SkillRequestProcessor(ws, context)

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
        if packet_type == "set_permission_mode":
            await self._handle_permission_mode_change(packet)
            return True
        if packet_type == "plan_request":
            await self._handle_plan_request(packet)
            return True
        if packet_type == "plan_confirm":
            return await self._handle_plan_confirm(packet)
        if packet_type == "plan_cancel":
            await self._handle_plan_cancel(packet)
            return True
        if packet_type == "apply_patch_review":
            await self._handle_patch_review_control(packet, action="apply")
            return True
        if packet_type == "reject_patch_review":
            await self._handle_patch_review_control(packet, action="reject")
            return True
        if packet_type == "new_session":
            await self._handle_new_session(packet)
            return True
        if packet_type == "list_sessions":
            await self._handle_list_sessions(packet)
            return True
        if self.skill_processor.can_handle(packet_type):
            await self.skill_processor.handle(packet)
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
        if packet_type == "cancel_turn":
            await self._handle_cancel_turn(packet)
            return True
        if packet_type == "summarize_session":
            await self._handle_summarize_session(packet)
            return True
        if packet_type == "list_memory":
            await self._handle_list_memory(packet)
            return True
        if packet_type == "forget_memory":
            await self._handle_forget_memory(packet)
            return True
        if packet_type == "remember_preference":
            await self._handle_remember_preference(packet)
            return True
        if packet_type == "search_memory":
            await self._handle_search_memory(packet)
            return True
        if packet_type == "undo_file":
            await self._handle_undo_file(packet)
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

    async def _handle_permission_mode_change(self, packet: Dict[str, Any]) -> None:
        """切换当前连接的权限模式，并刷新工具执行边界。"""

        request_id = _request_id(packet)
        raw_mode = str(packet.get("mode") or "").strip()
        try:
            mode = PermissionMode(raw_mode)
            previous_workspace = self.context.change_permission_mode(mode)
        except WorkspaceValidationError as exc:
            await self._record_runtime_error(
                packet,
                request_id=request_id,
                message=str(exc),
                requested_permission_mode=raw_mode,
            )
            await self.ws.send_json(
                build_event(
                    "workspace_error",
                    self.context.session_id,
                    _turn_id(packet),
                    request_id=request_id,
                    message=str(exc),
                    requested_permission_mode=raw_mode,
                    workspace=self.context.workspace.as_dict(),
                )
            )
            return
        except ValueError:
            await self._send_error(
                packet,
                request_id=request_id,
                message=f"unsupported permission mode: {raw_mode}",
                requested_permission_mode=raw_mode,
            )
            return

        await self._send_workspace_policy_changed(
            packet,
            request_id=request_id,
            previous_workspace=previous_workspace,
            reason="set_permission_mode",
            permission_mode=mode.value,
        )

    async def _handle_plan_request(self, packet: Dict[str, Any]) -> None:
        """生成待确认计划；不进入模型回合，也不创建空 session。"""

        request_id = _request_id(packet)
        content = str(packet.get("content") or packet.get("goal") or "").strip()
        if not content:
            await self._send_error(
                packet,
                request_id=request_id,
                message="plan content is empty",
            )
            return

        try:
            items, model = generate_task_list(content)
        except Exception as exc:
            items = fallback_task_list(content)
            model = f"fallback:{type(exc).__name__}"

        plan_summary = build_plan_summary(content, items)
        plan_markdown = build_plan_document(content, items)
        plan = {
            "plan_id": str(uuid.uuid4()),
            "content": content,
            "summary": plan_summary,
            "plan_markdown": plan_markdown,
            "items": items,
            "model": model,
            "created_at": time.time(),
        }
        self.context.pending_plan = plan

        if self.context.session_persisted:
            record_transcript_event(
                self.context.session_store,
                self.context.session_id,
                "plan_pending",
                {
                    "turn_id": _turn_id(packet, "plan"),
                    "request_id": request_id,
                    **plan,
                },
            )

        await self.ws.send_json(
            build_event(
                "plan_pending",
                self.context.session_id,
                _turn_id(packet, "plan"),
                request_id=request_id,
                session_state=self.context.session_state.as_dict(),
                **plan,
            )
        )

    async def _handle_plan_confirm(self, packet: Dict[str, Any]) -> bool:
        """确认挂起计划，并把 packet 转成 user_input 交回 WebSocket 主循环。"""

        request_id = _request_id(packet)
        pending_plan = self.context.pending_plan
        if not pending_plan:
            await self._send_error(
                packet,
                request_id=request_id,
                message="no pending plan to confirm",
            )
            return True

        requested_plan_id = str(packet.get("plan_id") or "").strip()
        if requested_plan_id and requested_plan_id != pending_plan["plan_id"]:
            await self._send_error(
                packet,
                request_id=request_id,
                message="pending plan id mismatch",
                requested_plan_id=requested_plan_id,
                pending_plan_id=pending_plan["plan_id"],
            )
            return True

        # 这里故意不直接运行 turn runner；把 packet 改成 user_input 后交回 app.py，
        # 继续复用现有持久化、session_busy、取消和 final_answer 收口路径。
        packet["type"] = "user_input"
        packet["content"] = pending_plan["content"]
        packet["_accepted_plan"] = pending_plan
        packet["_plan_request_id"] = request_id
        return False

    async def _handle_plan_cancel(self, packet: Dict[str, Any]) -> None:
        """取消挂起计划；不会进入模型回合。"""

        request_id = _request_id(packet)
        pending_plan = self.context.pending_plan
        if not pending_plan:
            await self._send_error(
                packet,
                request_id=request_id,
                message="no pending plan to cancel",
            )
            return

        requested_plan_id = str(packet.get("plan_id") or "").strip()
        if requested_plan_id and requested_plan_id != pending_plan["plan_id"]:
            await self._send_error(
                packet,
                request_id=request_id,
                message="pending plan id mismatch",
                requested_plan_id=requested_plan_id,
                pending_plan_id=pending_plan["plan_id"],
            )
            return

        self.context.pending_plan = None
        if self.context.session_persisted:
            record_transcript_event(
                self.context.session_store,
                self.context.session_id,
                "plan_cancelled",
                {
                    "turn_id": _turn_id(packet, "plan"),
                    "request_id": request_id,
                    "plan_id": pending_plan["plan_id"],
                },
            )

        await self.ws.send_json(
            build_event(
                "plan_cancelled",
                self.context.session_id,
                _turn_id(packet, "plan"),
                request_id=request_id,
                plan_id=pending_plan["plan_id"],
                session_state=self.context.session_state.as_dict(),
            )
        )

    async def _handle_patch_review_control(
        self,
        packet: Dict[str, Any],
        *,
        action: str,
    ) -> None:
        """处理桌面 Diff Review 卡片的直接 apply/reject 操作。"""

        request_id = _request_id(packet)
        patch_id = str(packet.get("patch_id") or "").strip()
        if not patch_id:
            await self._send_error(
                packet,
                request_id=request_id,
                message="patch_id is required",
            )
            return
        if not self.context.session_persisted:
            await self._send_error(
                packet,
                request_id=request_id,
                message="session not persisted yet",
                patch_id=patch_id,
            )
            return

        tool_name = "reject_patch" if action == "reject" else "apply_patch"
        arguments_payload = {"patch_id": patch_id}
        if tool_name == "apply_patch":
            selected_paths = _optional_string_list(packet.get("selected_paths"))
            if selected_paths is not None:
                arguments_payload["selected_paths"] = selected_paths
            test_command = str(packet.get("test_command") or "").strip()
            if test_command:
                # Diff Review 控制包只透传用户显式提供的测试命令，后端不猜测项目测试入口。
                arguments_payload["test_command"] = test_command
            if packet.get("test_timeout") is not None:
                arguments_payload["test_timeout"] = packet.get("test_timeout")
        if tool_name == "reject_patch":
            reason = str(packet.get("reason") or "用户在 Diff Review 卡片中拒绝").strip()
            arguments_payload["reason"] = reason
        arguments = json.dumps(arguments_payload, ensure_ascii=False, sort_keys=True)
        turn_id = _turn_id(packet, "patch")

        record_transcript_event(
            self.context.session_store,
            self.context.session_id,
            "tool_call_started",
            {
                "turn_id": turn_id,
                "request_id": request_id,
                "tool": tool_name,
                "arguments": arguments,
                "source": "patch_review_control",
            },
        )
        await self.ws.send_json(
            build_event(
                "tool_call_started",
                self.context.session_id,
                turn_id,
                request_id=request_id,
                name=tool_name,
                arguments=arguments,
            )
        )

        # 用户点击 Diff Review 卡片即代表批准这次 patch 操作；工具内部仍会
        # 重新执行 FileSystemPolicy 检查，不能绕过 protected path 等安全边界。
        result = self.context.runner.execute(tool_name, arguments, approved=True)
        await emit_tool_result(
            self.ws,
            self.context.session_store,
            result,
            self.context.session_state,
            self.context.session_id,
            turn_id,
            request_id,
            tool_name,
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

        sessions = list_session_summaries(
            self.context.session_store,
            limit=limit,
            workspace=self.context.workspace,
        )
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

        sessions = list_session_summaries(target_store, limit=30, workspace=target_workspace)
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
                display_messages, session_summary, pending_patch_review = (
                    self.context.resume_session_from_disk(target_session_id)
                )
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
            pending_patch_review = None
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
                pending_patch_review=pending_patch_review,
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

    async def _handle_cancel_turn(self, packet: Dict[str, Any]) -> None:
        """处理空闲状态下的取消请求；执行中取消由 turn runner 等待点消费。"""

        if self.context.session_state.active_turn_id:
            self.context.session_state.request_cancellation()
            await self.ws.send_json(
                build_event(
                    "turn_cancelling",
                    self.context.session_id,
                    self.context.session_state.active_turn_id,
                    request_id=_request_id(packet),
                    session_state=self.context.session_state.as_dict(),
                )
            )
            return

        await self._send_error(
            packet,
            request_id=_request_id(packet),
            message="no active turn to cancel",
        )

    async def _handle_summarize_session(self, packet: Dict[str, Any]) -> None:
        """生成当前会话摘要并保存到 memory。"""

        request_id = _request_id(packet)
        if not self.context.session_persisted:
            await self._send_error(
                packet,
                request_id=request_id,
                message="session not persisted yet",
            )
            return

        memory_dir = self.context.workspace.project_root / ".codex-mini" / "memory"
        memory_store = MemoryStore(memory_dir)

        writer = self.context.session_store.writer(self.context.session_id)
        events = writer.load()

        summary = summarize_session(events)
        task_state = extract_task_state(events)

        memory_entry = memory_store.save_session_memory(
            self.context.session_id,
            summary,
            {"task_state": task_state}
        )

        await self.ws.send_json(
            build_event(
                "session_summarized",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                summary=summary,
                memory_id=memory_entry.memory_id,
            )
        )

    async def _handle_list_memory(self, packet: Dict[str, Any]) -> None:
        """列出当前 workspace 的 memory。"""

        request_id = _request_id(packet)
        memory_type = packet.get("memory_type", "session")

        memory_dir = self.context.workspace.project_root / ".codex-mini" / "memory"
        memory_store = MemoryStore(memory_dir)

        if memory_type == "session":
            memories = memory_store.list_session_memories()
        elif memory_type == "task":
            memories = memory_store.list_task_memories()
        else:
            await self._send_error(
                packet,
                request_id=request_id,
                message=f"unsupported memory_type: {memory_type}",
            )
            return

        await self.ws.send_json(
            build_event(
                "memory_list",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                memory_type=memory_type,
                memories=[
                    {
                        "memory_id": m.memory_id,
                        "memory_type": m.memory_type.value,
                        "content": m.content,
                        "created_at": m.created_at,
                        "updated_at": m.updated_at,
                        "metadata": m.metadata,
                    }
                    for m in memories
                ],
            )
        )

    async def _handle_forget_memory(self, packet: Dict[str, Any]) -> None:
        """删除指定 memory。"""

        request_id = _request_id(packet)
        memory_type = packet.get("memory_type", "session")
        memory_id = str(packet.get("memory_id", "")).strip()

        if not memory_id:
            await self._send_error(
                packet,
                request_id=request_id,
                message="memory_id is required",
            )
            return

        from memory.models import MemoryType
        memory_dir = self.context.workspace.project_root / ".codex-mini" / "memory"
        memory_store = MemoryStore(memory_dir)

        try:
            mem_type = MemoryType(memory_type)
        except ValueError:
            await self._send_error(
                packet,
                request_id=request_id,
                message=f"unsupported memory_type: {memory_type}",
            )
            return

        deleted = memory_store.delete_memory(mem_type, memory_id)
        if not deleted:
            await self._send_error(
                packet,
                request_id=request_id,
                message=f"memory not found: {memory_id}",
            )
            return

        await self.ws.send_json(
            build_event(
                "memory_deleted",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                memory_type=memory_type,
                memory_id=memory_id,
            )
        )

    async def _handle_remember_preference(self, packet: Dict[str, Any]) -> None:
        """记录用户偏好到 User Memory。"""

        request_id = _request_id(packet)
        content = str(packet.get("content", "")).strip()

        if not content:
            await self._send_error(
                packet,
                request_id=request_id,
                message="preference content is required",
            )
            return

        from pathlib import Path

        # User Memory 存储在用户主目录
        user_memory_dir = Path.home() / ".codex-mini" / "memory"
        memory_store = MemoryStore(user_memory_dir)

        # 加载现有偏好，追加新内容
        existing = memory_store.load_user_memory()
        if existing:
            new_content = f"{existing.content}\n- {content}"
        else:
            new_content = f"# 用户偏好\n\n- {content}"

        entry = memory_store.save_user_memory(new_content)

        await self.ws.send_json(
            build_event(
                "preference_remembered",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                content=content,
                memory_id=entry.memory_id,
            )
        )

    async def _handle_search_memory(self, packet: Dict[str, Any]) -> None:
        """搜索 memory 内容。"""

        request_id = _request_id(packet)
        query = str(packet.get("query", "")).strip()

        if not query:
            await self._send_error(
                packet,
                request_id=request_id,
                message="search query is required",
            )
            return

        from memory.search import search_memory, search_memory_index

        memory_dir = self.context.workspace.project_root / ".codex-mini" / "memory"

        # 搜索索引
        index_results = search_memory_index(memory_dir, query)

        # 搜索详细内容
        detail_results = search_memory(memory_dir, query, limit=5)

        await self.ws.send_json(
            build_event(
                "memory_search_results",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                query=query,
                index_results=index_results,
                detail_results=[
                    {"memory_id": mem_id, "line": line, "line_number": line_num}
                    for mem_id, line, line_num in detail_results
                ],
            )
        )

    async def _handle_undo_file(self, packet: Dict[str, Any]) -> None:
        """撤销文件修改。"""

        request_id = _request_id(packet)
        file_path = str(packet.get("file_path", "")).strip()

        if not file_path:
            await self._send_error(
                packet,
                request_id=request_id,
                message="file_path is required",
            )
            return

        # 从上次保存的 diff_tracker 撤销
        if not self.context.last_diff_tracker:
            await self._send_error(
                packet,
                request_id=request_id,
                message="no changes to undo",
            )
            return

        # 客户端可能发送相对路径，需要解析为绝对路径以匹配 diff_tracker
        from pathlib import Path as _Path

        abs_path = file_path
        if not _Path(file_path).is_absolute() and self.context.workspace:
            abs_path = (self.context.workspace.project_root / file_path).resolve().as_posix()
        elif _Path(file_path).is_absolute():
            abs_path = _Path(file_path).resolve().as_posix()
        success = self.context.last_diff_tracker.undo_file(abs_path)

        if not success:
            await self._send_error(
                packet,
                request_id=request_id,
                message=f"file not found in changes: {file_path}",
            )
            return

        await self.ws.send_json(
            build_event(
                "file_undone",
                self.context.session_id,
                _turn_id(packet),
                request_id=request_id,
                file_path=file_path,
            )
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


def _optional_string_list(value: Any) -> list[str] | None:
    """读取可选字符串列表；缺失时返回 None，非法值由后端工具继续校验。"""

    if value is None:
        return None
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value]

"""单轮模型推理、工具调用和权限确认的运行器。

WebSocket 入口只负责收发 packet；本模块负责一轮 user_input 之后的完整 Agent 回合：
流式调用模型、拼接 tool_calls、等待权限确认、执行工具、写 transcript，并在需要时挂起会话。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, List

from observability import build_model_message_snapshot, write_runtime_log
from server.protocol.events import build_event
from server.runtime.model_config import ModelRequestConfig, configure_litellm_environment
from server.runtime.model_stream import (
    build_assistant_message,
    extract_stream_delta,
    merge_tool_call_delta,
)
from server.runtime.session_state import SessionRuntimeState
from server.runtime.transcript_events import (
    answered_clarification_result,
    assistant_transcript_payload,
    denied_tool_result,
    record_transcript_event,
)
from context_manager import ContextManager
from patch import PatchFileChange, PatchProposal, PatchStatus, PatchStore
from session import SessionStore
from session.turn_context import TurnContext
from tools.core.registry import ToolRegistry
from tools.core.router import ToolRouter
from tools.core.types import ToolResult


@dataclass(frozen=True)
class PermissionDecision:
    """前端对一次 permission_request 的回答。"""

    approved: bool
    feedback: str | None = None
    scope: str = "once"
    prefix_rule: tuple[str, ...] | None = None


class TurnCancelled(Exception):
    """用户请求取消当前 turn 时抛出，由 WebSocket 入口统一收口。"""


async def stream_model_message(
    ws: Any,
    registry: ToolRegistry,
    messages: List[Dict[str, Any]],
    session_id: str,
    turn_id: str,
    model_config: ModelRequestConfig,
) -> Dict[str, Any]:
    """流式请求模型，并把 token 增量和 tool_call 增量整理成 assistant message。"""

    configure_litellm_environment()
    from litellm import completion

    stream = completion(
        **model_config.completion_kwargs(),
        messages=messages,
        tools=registry.schemas(),
        tool_choice="auto",
        stream=True,
    )

    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_call_buffers: Dict[int, Dict[str, Any]] = {}
    chunk_count = 0
    for chunk in stream:
        delta = extract_stream_delta(chunk)
        reasoning_content = delta.get("reasoning_content")
        if reasoning_content:
            reasoning_parts.append(reasoning_content)

        content = delta.get("content")
        if content:
            content_parts.append(content)
            await ws.send_json(
                build_event(
                    "assistant_token",
                    session_id,
                    turn_id,
                    token=content,
                )
            )

        for tool_call_delta in delta.get("tool_calls") or []:
            merge_tool_call_delta(tool_call_buffers, tool_call_delta)

        # 每 10 个 chunk 才让步，降低事件循环切换开销
        chunk_count += 1
        if chunk_count % 10 == 0:
            await asyncio.sleep(0)

    return build_assistant_message(
        "".join(content_parts),
        tool_call_buffers,
        reasoning_content="".join(reasoning_parts),
    )


async def wait_for_permission_decision(
    ws: Any,
    session_id: str,
    turn_id: str,
    request_id: str,
) -> PermissionDecision:
    """等待前端返回与当前工具调用匹配的权限决定。"""

    while True:
        decision = await ws.receive_json()
        if decision.get("type") != "permission_decision":
            if _is_cancel_packet(decision, turn_id, request_id):
                raise TurnCancelled("用户取消了当前回合")
            if decision.get("type") == "user_input":
                await ws.send_json(
                    build_event(
                        "session_busy",
                        session_id,
                        turn_id,
                        request_id=request_id,
                        detail="当前回合正在等待权限确认，请先处理当前请求。",
                    )
                )
                continue
            await ws.send_json(
                build_event(
                    "error",
                    session_id,
                    turn_id,
                    request_id=request_id,
                    message="waiting for permission_decision",
                    received_type=decision.get("type"),
                )
            )
            continue

        if decision.get("request_id") not in {None, request_id}:
            await ws.send_json(
                build_event(
                    "error",
                    session_id,
                    turn_id,
                    request_id=request_id,
                    message="permission_decision request_id mismatch",
                    received_request_id=decision.get("request_id"),
                )
            )
            continue

        feedback = (decision.get("feedback") or "").strip()
        scope = "session" if decision.get("scope") == "session" else "once"
        return PermissionDecision(
            approved=bool(decision.get("approved")),
            feedback=feedback or None,
            scope=scope,
            prefix_rule=_normalize_prefix_rule(decision.get("prefix_rule")),
        )


async def wait_for_clarification_response(
    ws: Any,
    session_id: str,
    turn_id: str,
    request_id: str,
) -> Dict[str, Any]:
    """等待前端返回与当前澄清问题匹配的用户回答。"""

    while True:
        response = await ws.receive_json()
        if response.get("type") != "clarification_response":
            if _is_cancel_packet(response, turn_id, request_id):
                raise TurnCancelled("用户取消了当前回合")
            if response.get("type") == "user_input":
                await ws.send_json(
                    build_event(
                        "session_busy",
                        session_id,
                        turn_id,
                        request_id=request_id,
                        detail="当前回合正在等待用户回答，请先处理当前问题。",
                    )
                )
                continue
            await ws.send_json(
                build_event(
                    "error",
                    session_id,
                    turn_id,
                    request_id=request_id,
                    message="waiting for clarification_response",
                    received_type=response.get("type"),
                )
            )
            continue

        if response.get("request_id") not in {None, request_id}:
            await ws.send_json(
                build_event(
                    "error",
                    session_id,
                    turn_id,
                    request_id=request_id,
                    message="clarification_response request_id mismatch",
                    received_request_id=response.get("request_id"),
                )
            )
            continue

        normalized = _normalize_clarification_response(response)
        if not _has_clarification_answer(normalized):
            await ws.send_json(
                build_event(
                    "error",
                    session_id,
                    turn_id,
                    request_id=request_id,
                    message="clarification_response is empty",
                )
            )
            continue

        return normalized


async def request_user_clarification(
    ws: Any,
    session_store: SessionStore,
    session_id: str,
    turn_id: str,
    request_id: str,
    tool_name: str,
    metadata: Dict[str, Any],
) -> ToolResult:
    """把 ask_user 工具调用转换为前端可渲染的问题事件，并等待用户回答。"""

    question = str(metadata.get("question") or "").strip()
    options = metadata.get("options") if isinstance(metadata.get("options"), list) else []
    allow_freeform = bool(metadata.get("allow_freeform", True))
    record_transcript_event(
        session_store,
        session_id,
        "clarification_request",
        {
            "turn_id": turn_id,
            "request_id": request_id,
            "tool": tool_name,
            "question": question,
            "options": options,
            "allow_freeform": allow_freeform,
            "metadata": metadata,
        },
    )
    await ws.send_json(
        build_event(
            "clarification_request",
            session_id,
            turn_id,
            request_id=request_id,
            tool=tool_name,
            question=question,
            options=options,
            allow_freeform=allow_freeform,
            metadata=metadata,
        )
    )

    response = await wait_for_clarification_response(ws, session_id, turn_id, request_id)
    record_transcript_event(
        session_store,
        session_id,
        "clarification_response",
        {
            "turn_id": turn_id,
            "request_id": request_id,
            "tool": tool_name,
            **response,
        },
    )
    await ws.send_json(
        build_event(
            "clarification_response_ack",
            session_id,
            turn_id,
            request_id=request_id,
            skipped=response.get("skipped", False),
        )
    )

    return answered_clarification_result(tool_name, metadata, response)


async def emit_tool_result(
    ws: Any,
    session_store: SessionStore,
    result: ToolResult,
    session_state: SessionRuntimeState,
    session_id: str,
    turn_id: str,
    request_id: str,
    tool_name: str,
) -> None:
    """写入并发送工具执行结果，必要时同步会话挂起状态。"""

    metadata = result.metadata or {}
    transcript_content = result.content
    if metadata.get("skill_used"):
        transcript_content = "[skill content omitted from transcript]"
    elif metadata.get("skill_resource"):
        transcript_content = "[skill resource content omitted from transcript]"
    record_transcript_event(
        session_store,
        session_id,
        "tool_call_result",
        {
            "turn_id": turn_id,
            "request_id": request_id,
            "tool": tool_name,
            "ok": result.ok,
            "content": transcript_content,
            "metadata": metadata,
        },
    )
    await ws.send_json(
        build_event(
            "tool_call_result",
            session_id,
            turn_id,
            request_id=request_id,
            name=tool_name,
            ok=result.ok,
            content=result.content,
            metadata=metadata,
        )
    )
    await _emit_patch_lifecycle_event(
        ws,
        session_store,
        metadata,
        session_id,
        turn_id,
        request_id,
        tool_name,
    )

    if metadata.get("session_suspended"):
        # 连续拒绝等安全策略触发后，运行时进入 suspended，后续 user_input 会被阻断。
        session_state.suspend(metadata.get("category"), result.content)
        record_transcript_event(
            session_store,
            session_id,
            "session_suspended",
            {
                "turn_id": turn_id,
                "request_id": request_id,
                "category": metadata.get("category"),
                "detail": result.content,
                "session_state": session_state.as_dict(),
            },
        )
        await ws.send_json(
            build_event(
                "session_suspended",
                session_id,
                turn_id,
                request_id=request_id,
                category=metadata.get("category"),
                detail=result.content,
                session_state=session_state.as_dict(),
            )
        )


async def _emit_patch_lifecycle_event(
    ws: Any,
    session_store: SessionStore,
    metadata: Dict[str, Any],
    session_id: str,
    turn_id: str,
    request_id: str,
    tool_name: str,
) -> None:
    """把 patch 工具结果转换为独立 lifecycle event。"""

    patch_id = str(metadata.get("patch_id") or "").strip()
    patch_status = str(metadata.get("patch_status") or "").strip()
    if not patch_id or not patch_status:
        return

    event_type = _patch_event_type(patch_status)
    if event_type is None:
        return

    try:
        proposal = PatchStore(session_store.project_root / ".codex-mini" / "patches").load(patch_id)
    except Exception:
        # patch lifecycle 只是展示/恢复事件，不能反过来中断已完成的工具结果上报。
        proposal = None
    payload = _patch_event_payload(
        metadata,
        proposal,
        turn_id=turn_id,
        request_id=request_id,
        tool_name=tool_name,
        patch_id=patch_id,
        patch_status=patch_status,
    )
    record_transcript_event(session_store, session_id, event_type, payload)
    await ws.send_json(
        build_event(
            event_type,
            session_id,
            turn_id,
            request_id=request_id,
            **{key: value for key, value in payload.items() if key not in {"turn_id", "request_id"}},
        )
    )


async def _emit_patch_approval_request_event(
    ws: Any,
    session_store: SessionStore,
    *,
    session_id: str,
    turn_id: str,
    request_id: str,
    tool_name: str,
    arguments: str,
    metadata: Dict[str, Any],
) -> None:
    """把 apply/reject patch 的普通权限请求补充为 patch 专用审批事件。"""

    patch_id = _patch_id_from_tool_arguments(tool_name, arguments)
    if not patch_id:
        return

    try:
        proposal = PatchStore(session_store.project_root / ".codex-mini" / "patches").load(patch_id)
    except Exception:
        # 非法 id 或损坏文件都只影响 review 详情展示，普通 permission_request 仍然可用。
        proposal = None

    patch_status = proposal.status.value if proposal else str(metadata.get("patch_status") or "")
    if tool_name == "reject_patch":
        action = "reject"
    elif tool_name == "rollback_patch":
        action = "rollback"
    else:
        action = "apply"
    payload = _patch_event_payload(
        {
            **metadata,
            "action": action,
        },
        proposal,
        turn_id=turn_id,
        request_id=request_id,
        tool_name=tool_name,
        patch_id=patch_id,
        patch_status=patch_status,
    )
    record_transcript_event(session_store, session_id, "patch_approval_request", payload)
    await ws.send_json(
        build_event(
            "patch_approval_request",
            session_id,
            turn_id,
            request_id=request_id,
            **{key: value for key, value in payload.items() if key not in {"turn_id", "request_id"}},
        )
    )


def _patch_id_from_tool_arguments(tool_name: str, arguments: str) -> str | None:
    """从 patch review 工具参数中提取 patch_id，其他工具不参与 patch 审批事件。"""

    if tool_name not in {"apply_patch", "reject_patch", "rollback_patch"}:
        return None
    try:
        payload = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    patch_id = str(payload.get("patch_id") or "").strip()
    return patch_id or None


def _patch_event_type(patch_status: str) -> str | None:
    return {
        PatchStatus.PROPOSED.value: "patch_proposed",
        PatchStatus.APPLIED.value: "patch_applied",
        PatchStatus.REJECTED.value: "patch_rejected",
        PatchStatus.FAILED.value: "patch_apply_failed",
        PatchStatus.ROLLED_BACK.value: "patch_rolled_back",
    }.get(patch_status)


def _patch_event_payload(
    metadata: Dict[str, Any],
    proposal: PatchProposal | None,
    *,
    turn_id: str,
    request_id: str,
    tool_name: str,
    patch_id: str,
    patch_status: str,
) -> Dict[str, Any]:
    changes = [_patch_change_payload(change) for change in proposal.changes] if proposal else []
    return {
        "turn_id": turn_id,
        "request_id": request_id,
        "tool": tool_name,
        "patch_id": patch_id,
        "patch_status": patch_status,
        "summary": proposal.summary if proposal else None,
        "changed_paths": proposal.changed_paths if proposal else list(metadata.get("changed_paths") or []),
        "additions": proposal.additions if proposal else metadata.get("additions"),
        "deletions": proposal.deletions if proposal else metadata.get("deletions"),
        "changes": changes,
        "metadata": {
            key: value
            for key, value in metadata.items()
            # lifecycle 事件只展示 review 摘要，完整 before/after 快照留在 patch store。
            if key not in {"patch_id", "patch_status", "before", "after", "content"}
        },
    }


def _patch_change_payload(change: PatchFileChange) -> Dict[str, Any]:
    return {
        "path": change.path,
        "change_type": change.change_type.value,
        "unified_diff": change.unified_diff,
        "additions": change.additions,
        "deletions": change.deletions,
        "move_path": change.move_path,
    }


async def run_turn(
    ws: Any,
    session_store: SessionStore,
    turn_context: TurnContext,
) -> ContextManager:
    """执行一次完整模型回合，直到模型给出最终回答或会话被挂起。

    本函数是主循环的核心：model -> optional tool calls -> tool results ->
    model。它只接收 TurnContext，避免 registry、runner、history、session_id
    等单轮状态在调用栈中继续散开。
    """

    session_id = turn_context.session_id
    turn_id = turn_context.turn_id
    registry = turn_context.registry
    runner = turn_context.runner
    history = turn_context.history
    session_state: SessionRuntimeState = turn_context.session_state
    model_config: ModelRequestConfig = turn_context.model.request_config
    model_iteration = 0
    previous_logged_model_messages: list[dict[str, Any]] | None = None

    while True:
        model_iteration += 1
        model_messages = turn_context.model_messages()
        message_snapshot, previous_logged_model_messages = build_model_message_snapshot(
            model_messages,
            previous_logged_model_messages,
        )
        write_runtime_log(
            session_store.project_root,
            "model_request",
            {
                "iteration": model_iteration,
                "model_config": model_config.as_dict(),
                "message_snapshot": message_snapshot,
                "tools": [item["function"]["name"] for item in registry.schemas()],
            },
            session_id=session_id,
            turn_id=turn_id,
            source="model",
            actor="agent",
        )
        message = await stream_model_message(
            ws,
            registry,
            model_messages,
            session_id,
            turn_id,
            model_config,
        )
        write_runtime_log(
            session_store.project_root,
            "model_response",
            {
                "iteration": model_iteration,
                **assistant_transcript_payload(message, turn_id),
            },
            session_id=session_id,
            turn_id=turn_id,
            source="model",
            actor="model",
        )
        history.append_assistant_message(message)
        record_transcript_event(
            session_store,
            session_id,
            "assistant_message",
            assistant_transcript_payload(message, turn_id),
            # 同一内容已作为 model_response 写入运行日志，transcript 仍正常持久化。
            mirror_runtime_log=False,
        )

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            _strip_skill_tool_results(history)
            return history

        # 模型可能一次返回多个工具调用；当前按顺序执行，便于权限和 transcript 对齐。
        for tool_call in tool_calls:
            # Router 只负责把模型返回格式转成内部 ToolInvocation，并把
            # TurnContext 中的 session/turn/workspace 信息贴到本次工具调用上。
            invocation = ToolRouter.build_tool_invocation(tool_call, turn_context)
            tool_name = invocation.name
            arguments = invocation.arguments
            request_id = invocation.call_id
            record_transcript_event(
                session_store,
                session_id,
                "tool_call_started",
                {
                    "turn_id": turn_id,
                    "request_id": request_id,
                    "tool": tool_name,
                    "arguments": arguments,
                },
            )
            await ws.send_json(
                build_event(
                    "tool_call_started",
                    session_id,
                    turn_id,
                    request_id=request_id,
                    name=tool_name,
                    arguments=arguments,
                )
            )

            # Runner 是唯一真正执行工具的入口：JSON 解析、审批拦截、异常包装都在这里。
            result = runner.execute_invocation(invocation)
            metadata = result.metadata or {}
            if metadata.get("user_interaction_action") == "ask":
                result = await request_user_clarification(
                    ws,
                    session_store,
                    session_id,
                    turn_id,
                    request_id,
                    tool_name,
                    metadata,
                )
                metadata = result.metadata or {}

            if metadata.get("permission_action") == "ask":
                # mutating 工具和命令在真正执行前必须经过前端确认。
                record_transcript_event(
                    session_store,
                    session_id,
                    "permission_request",
                    {
                        "turn_id": turn_id,
                        "request_id": request_id,
                        "tool": tool_name,
                        "arguments": arguments,
                        "detail": result.content,
                        "metadata": metadata,
                    },
                )
                await ws.send_json(
                    build_event(
                        "permission_request",
                        session_id,
                        turn_id,
                        request_id=request_id,
                        tool=tool_name,
                        arguments=arguments,
                        detail=result.content,
                        metadata=metadata,
                    )
                )
                await _emit_patch_approval_request_event(
                    ws,
                    session_store,
                    session_id=session_id,
                    turn_id=turn_id,
                    request_id=request_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    metadata=metadata,
                )
                permission_decision = await wait_for_permission_decision(
                    ws,
                    session_id,
                    turn_id,
                    request_id,
                )
                approved = permission_decision.approved
                user_feedback = permission_decision.feedback
                remembered_prefix = _approved_session_prefix(metadata, permission_decision)
                if remembered_prefix:
                    runner.ctx.policy.allow_prefix_for_session(remembered_prefix)
                record_transcript_event(
                    session_store,
                    session_id,
                    "permission_decision",
                    {
                        "turn_id": turn_id,
                        "request_id": request_id,
                        "tool": tool_name,
                        "approved": approved,
                        "feedback": user_feedback,
                        "scope": permission_decision.scope,
                        "prefix_rule": list(remembered_prefix) if remembered_prefix else None,
                        "workspace": turn_context.workspace.as_dict(),
                    },
                )
                await ws.send_json(
                    build_event(
                        "permission_decision_ack",
                        session_id,
                        turn_id,
                        request_id=request_id,
                        approved=approved,
                    )
                )
                if approved:
                    # 用户批准后带 _approved 重试同一个工具调用。
                    result = runner.execute_invocation(
                        invocation.with_approval(True, user_feedback),
                    )
                    metadata = result.metadata or {}
                else:
                    result = denied_tool_result(
                        tool_name,
                        result.content,
                        metadata,
                        user_feedback=user_feedback,
                    )
                    metadata = result.metadata or {}

            if result.ok and metadata.get("skill_used"):
                skill_payload = {
                    "turn_id": turn_id,
                    "name": str(metadata.get("skill_name") or ""),
                    "path": str(metadata.get("skill_path") or ""),
                    "scope": str(metadata.get("skill_scope") or ""),
                    "invocation_type": str(metadata.get("invocation_type") or "implicit"),
                }
                record_transcript_event(
                    session_store,
                    session_id,
                    "skill_used",
                    skill_payload,
                )
                await ws.send_json(
                    build_event(
                        "skill_used",
                        session_id,
                        turn_id,
                        name=skill_payload["name"],
                        path=skill_payload["path"],
                        scope=skill_payload["scope"],
                        invocation_type=skill_payload["invocation_type"],
                    )
                )

            await emit_tool_result(
                ws,
                session_store,
                result,
                session_state,
                session_id,
                turn_id,
                request_id,
                tool_name,
            )

            content = result.content if result.ok else f"[ERROR] {result.content}"
            # 工具结果必须作为 role=tool 回灌给模型，否则模型看不到刚才的执行结果。
            history.append_tool_result(request_id, tool_name, content)
            if session_state.suspended:
                _strip_skill_tool_results(history)
                return history


def _normalize_clarification_response(response: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {
        "content": str(response.get("content") or "").strip(),
        "skipped": bool(response.get("skipped")),
    }

    choice_id = str(response.get("choice_id") or "").strip()
    if choice_id:
        normalized["choice_id"] = choice_id

    option_index = response.get("option_index")
    if isinstance(option_index, int):
        normalized["option_index"] = option_index
    else:
        try:
            if option_index is not None and str(option_index).strip():
                normalized["option_index"] = int(str(option_index).strip())
        except (TypeError, ValueError):
            pass

    return normalized


def _strip_skill_tool_results(history: ContextManager) -> None:
    """回合结束后移除完整 skill 内容，避免污染下一轮长期上下文。"""

    for message in history.messages:
        if message.get("role") != "tool":
            continue
        if message.get("name") == "read_skill":
            message["content"] = "[skill content omitted from history after this turn]"
        elif message.get("name") == "read_skill_resource":
            message["content"] = "[skill resource content omitted from history after this turn]"


def _normalize_prefix_rule(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    parts: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if not text:
            return None
        parts.append(text)
    return tuple(parts) if parts else None


def _is_cancel_packet(packet: Dict[str, Any], turn_id: str, request_id: str) -> bool:
    """判断当前等待点是否收到匹配的取消请求。"""

    if packet.get("type") != "cancel_turn":
        return False
    packet_turn_id = packet.get("turn_id")
    packet_request_id = packet.get("request_id")
    turn_matches = packet_turn_id in {None, turn_id}
    request_matches = packet_request_id in {None, request_id}
    return turn_matches and request_matches


def _approved_session_prefix(
    metadata: Dict[str, Any],
    decision: PermissionDecision,
) -> tuple[str, ...] | None:
    if not decision.approved or decision.scope != "session":
        return None
    suggested = _normalize_prefix_rule(metadata.get("suggested_prefix_rule"))
    if suggested and decision.prefix_rule == suggested:
        return suggested
    return None


def _has_clarification_answer(response: Dict[str, Any]) -> bool:
    return bool(
        response.get("skipped")
        or response.get("content")
        or response.get("choice_id")
        or response.get("option_index") is not None
    )

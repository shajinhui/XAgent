"""单轮模型推理、工具调用和权限确认的运行器。

WebSocket 入口只负责收发 packet；本模块负责一轮 user_input 之后的完整 Agent 回合：
流式调用模型、拼接 tool_calls、等待权限确认、执行工具、写 transcript，并在需要时挂起会话。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from server.protocol.events import build_event
from server.runtime.model_config import ModelRequestConfig
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
from session import SessionStore
from tools.core.router import ToolRouter
from tools.registry import ToolRegistry
from tools.core.types import ToolResult


async def stream_model_message(
    ws: Any,
    registry: ToolRegistry,
    messages: List[Dict[str, Any]],
    session_id: str,
    turn_id: str,
    model_config: ModelRequestConfig,
) -> Dict[str, Any]:
    """流式请求模型，并把 token 增量和 tool_call 增量整理成 assistant message。"""

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

        # 让出事件循环，避免长流式响应阻塞 WebSocket 其他协程。
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
) -> tuple[bool, str | None]:
    """等待前端返回与当前工具调用匹配的权限决定。"""

    while True:
        decision = await ws.receive_json()
        if decision.get("type") != "permission_decision":
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
        return bool(decision.get("approved")), feedback or None


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
    record_transcript_event(
        session_store,
        session_id,
        "tool_call_result",
        {
            "turn_id": turn_id,
            "request_id": request_id,
            "tool": tool_name,
            "ok": result.ok,
            "content": result.content,
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


async def run_turn(
    ws: Any,
    session_store: SessionStore,
    registry: ToolRegistry,
    messages: List[Dict[str, Any]],
    session_state: SessionRuntimeState,
    session_id: str,
    turn_id: str,
    model_config: ModelRequestConfig,
) -> List[Dict[str, Any]]:
    """执行一次完整模型回合，直到模型给出最终回答或会话被挂起。"""

    while True:
        message = await stream_model_message(
            ws,
            registry,
            messages,
            session_id,
            turn_id,
            model_config,
        )
        messages.append(message)
        record_transcript_event(
            session_store,
            session_id,
            "assistant_message",
            assistant_transcript_payload(message, turn_id),
        )

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return messages

        # 模型可能一次返回多个工具调用；当前按顺序执行，便于权限和 transcript 对齐。
        for tool_call in tool_calls:
            invocation = ToolRouter.build_tool_invocation(tool_call)
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

            result = registry.execute(name=tool_name, arguments=arguments)
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
                approved, user_feedback = await wait_for_permission_decision(
                    ws,
                    session_id,
                    turn_id,
                    request_id,
                )
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
                    result = registry.execute(
                        name=tool_name,
                        arguments=arguments,
                        approved=True,
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
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": request_id,
                    "name": tool_name,
                    "content": content,
                }
            )
            if session_state.suspended:
                return messages


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


def _has_clarification_answer(response: Dict[str, Any]) -> bool:
    return bool(
        response.get("skipped")
        or response.get("content")
        or response.get("choice_id")
        or response.get("option_index") is not None
    )

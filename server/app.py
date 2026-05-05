from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from tools.registry import ToolRegistry
from tools.types import ToolResult

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
except ModuleNotFoundError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    WebSocket = None  # type: ignore[assignment]
    WebSocketDisconnect = Exception  # type: ignore[assignment]


class AgentState(TypedDict):
    messages: List[Dict[str, Any]]


EVENT_SCHEMA_VERSION = "2026-05-05"


@dataclass
class SessionRuntimeState:
    session_id: str
    suspended: bool = False
    suspended_category: str | None = None
    suspended_detail: str | None = None
    suspended_at: float | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": "suspended" if self.suspended else "active",
            "suspended": self.suspended,
            "suspended_category": self.suspended_category,
            "suspended_detail": self.suspended_detail,
            "suspended_at": self.suspended_at,
        }

    def suspend(self, category: str | None, detail: str) -> None:
        self.suspended = True
        self.suspended_category = category
        self.suspended_detail = detail
        self.suspended_at = time.time()

    def resume(self) -> None:
        self.suspended = False
        self.suspended_category = None
        self.suspended_detail = None
        self.suspended_at = None


def build_event(
    event_type: str,
    session_id: str,
    turn_id: str,
    **payload: Any,
) -> Dict[str, Any]:
    return {
        "type": event_type,
        "session_id": session_id,
        "turn_id": turn_id,
        "request_id": payload.pop("request_id", str(uuid.uuid4())),
        "schema_version": EVENT_SCHEMA_VERSION,
        "timestamp": time.time(),
        **payload,
    }


def build_model_name() -> str:
    provider = os.getenv("MODEL_PROVIDER", "openai").strip()
    model = os.getenv("MODEL_NAME", "gpt-4o-mini").strip()
    return f"{provider}/{model}"


def _object_to_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(exclude_none=True)
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith("_") and not callable(getattr(value, key))
    }


def _extract_stream_delta(chunk: Any) -> Dict[str, Any]:
    chunk_dict = _object_to_dict(chunk)
    choices = chunk_dict.get("choices")
    if choices is None:
        choices = getattr(chunk, "choices", [])
    if not choices:
        return {}

    choice = choices[0]
    choice_dict = _object_to_dict(choice)
    delta = choice_dict.get("delta")
    if delta is None:
        delta = getattr(choice, "delta", None)
    return _object_to_dict(delta)


def merge_tool_call_delta(buffers: Dict[int, Dict[str, Any]], delta: Dict[str, Any]) -> None:
    index = int(delta.get("index", len(buffers)))
    current = buffers.setdefault(
        index,
        {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        },
    )

    if delta.get("id"):
        current["id"] = delta["id"]
    if delta.get("type"):
        current["type"] = delta["type"]

    fn_delta = _object_to_dict(delta.get("function"))
    if fn_delta.get("name"):
        current["function"]["name"] += fn_delta["name"]
    if "arguments" in fn_delta:
        current["function"]["arguments"] += fn_delta.get("arguments") or ""


def build_assistant_message(content: str, tool_call_buffers: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_call_buffers:
        tool_calls: List[Dict[str, Any]] = []
        for index, tool_call in sorted(tool_call_buffers.items()):
            if not tool_call.get("id"):
                tool_call["id"] = f"tool_call_{index}"
            tool_calls.append(tool_call)
        message["tool_calls"] = tool_calls
    return message


def denied_tool_result(tool_name: str, command_or_detail: str, metadata: Dict[str, Any]) -> ToolResult:
    denied_metadata = {
        "tool": tool_name,
        "error_type": "permission_denied",
        "permission_action": "deny",
        "category": metadata.get("category", "user_denied"),
        "user_denied": True,
    }
    if metadata.get("command"):
        denied_metadata["command"] = metadata["command"]
    return ToolResult(
        ok=False,
        content=f"用户拒绝执行工具 {tool_name}: {command_or_detail}",
        metadata=denied_metadata,
    )


def build_graph(registry: ToolRegistry):
    def call_model(state: AgentState) -> AgentState:
        from litellm import completion

        response = completion(
            model=build_model_name(),
            messages=state["messages"],
            tools=registry.schemas(),
            tool_choice="auto",
            temperature=0,
        )
        message = response.choices[0].message.model_dump(exclude_none=True)
        return {"messages": state["messages"] + [message]}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if last.get("tool_calls"):
            return "tools"
        return "end"

    def call_tools(state: AgentState) -> AgentState:
        last = state["messages"][-1]
        new_messages = list(state["messages"])
        for tool_call in last.get("tool_calls", []):
            fn = tool_call["function"]
            result = registry.execute(name=fn["name"], arguments=fn.get("arguments", "{}"))
            content = result.content if result.ok else f"[ERROR] {result.content}"
            new_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": fn["name"],
                    "content": content,
                }
            )
        return {"messages": new_messages}

    graph = StateGraph(AgentState)
    graph.add_node("model", call_model)
    graph.add_node("tools", call_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", should_continue, {"tools": "tools", "end": END})
    graph.add_edge("tools", "model")
    return graph.compile()


if FastAPI is not None:
    app = FastAPI(title="Codex-mini Agent Service")
else:  # pragma: no cover
    app = None


if app is not None:

    async def stream_model_message(
        ws: WebSocket,
        registry: ToolRegistry,
        messages: List[Dict[str, Any]],
        session_id: str,
        turn_id: str,
    ) -> Dict[str, Any]:
        from litellm import completion

        stream = completion(
            model=build_model_name(),
            messages=messages,
            tools=registry.schemas(),
            tool_choice="auto",
            temperature=0,
            stream=True,
        )

        content_parts: List[str] = []
        tool_call_buffers: Dict[int, Dict[str, Any]] = {}
        for chunk in stream:
            delta = _extract_stream_delta(chunk)
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
                merge_tool_call_delta(tool_call_buffers, _object_to_dict(tool_call_delta))

            await asyncio.sleep(0)

        return build_assistant_message("".join(content_parts), tool_call_buffers)

    async def wait_for_permission_decision(
        ws: WebSocket,
        session_id: str,
        turn_id: str,
        request_id: str,
    ) -> bool:
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

            return bool(decision.get("approved"))

    async def emit_tool_result(
        ws: WebSocket,
        result: ToolResult,
        session_state: SessionRuntimeState,
        session_id: str,
        turn_id: str,
        request_id: str,
        tool_name: str,
    ) -> None:
        metadata = result.metadata or {}
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
            session_state.suspend(metadata.get("category"), result.content)
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
        ws: WebSocket,
        registry: ToolRegistry,
        messages: List[Dict[str, Any]],
        session_state: SessionRuntimeState,
        session_id: str,
        turn_id: str,
    ) -> List[Dict[str, Any]]:
        while True:
            message = await stream_model_message(ws, registry, messages, session_id, turn_id)
            messages.append(message)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return messages

            for tool_call in tool_calls:
                fn = tool_call["function"]
                tool_name = fn["name"]
                arguments = fn.get("arguments", "{}")
                await ws.send_json(
                    build_event(
                        "tool_call_started",
                        session_id,
                        turn_id,
                        request_id=tool_call["id"],
                        name=tool_name,
                        arguments=arguments,
                    )
                )

                result = registry.execute(name=tool_name, arguments=arguments)
                metadata = result.metadata or {}
                if metadata.get("permission_action") == "ask":
                    await ws.send_json(
                        build_event(
                            "permission_request",
                            session_id,
                            turn_id,
                            request_id=tool_call["id"],
                            tool=tool_name,
                            arguments=arguments,
                            detail=result.content,
                            metadata=metadata,
                        )
                    )
                    approved = await wait_for_permission_decision(
                        ws,
                        session_id,
                        turn_id,
                        tool_call["id"],
                    )
                    await ws.send_json(
                        build_event(
                            "permission_decision_ack",
                            session_id,
                            turn_id,
                            request_id=tool_call["id"],
                            approved=approved,
                        )
                    )
                    if approved:
                        result = registry.execute(
                            name=tool_name,
                            arguments=arguments,
                            approved=True,
                        )
                        metadata = result.metadata or {}
                    else:
                        result = denied_tool_result(tool_name, result.content, metadata)
                        metadata = result.metadata or {}

                await emit_tool_result(
                    ws,
                    result,
                    session_state,
                    session_id,
                    turn_id,
                    tool_call["id"],
                    tool_name,
                )

                content = result.content if result.ok else f"[ERROR] {result.content}"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": tool_name,
                        "content": content,
                    }
                )
                if session_state.suspended:
                    return messages

    @app.websocket("/agent/ws")
    async def agent_ws(ws: WebSocket) -> None:
        await ws.accept()
        load_dotenv()

        project_root = Path(__file__).resolve().parents[1]
        session_id = "ws-default"
        session_state = SessionRuntimeState(session_id=session_id)
        registry = ToolRegistry(project_root=project_root, session_id=session_id)

        system_prompt = (
            "你是一个代码助手。"
            "可以按需调用工具 read_file/write_file/edit_file/grep/run_command。"
            "如果不需要工具，直接给出最终答案。"
        )
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        await ws.send_json(
            {
                "type": "ready",
                "session_id": session_id,
                "schema_version": EVENT_SCHEMA_VERSION,
                "path": "/agent/ws",
                "tools": registry.metadata(),
                "session_state": session_state.as_dict(),
            }
        )

        try:
            while True:
                packet = await ws.receive_json()
                packet_type = packet.get("type")
                if packet_type == "resume_session":
                    previous_state = session_state.as_dict()
                    suspended_category = session_state.suspended_category
                    session_state.resume()
                    registry.ctx.circuit_breaker.reset(session_id, suspended_category)
                    await ws.send_json(
                        build_event(
                            "session_resumed",
                            session_id,
                            packet.get("turn_id") or "system",
                            previous_state=previous_state,
                            session_state=session_state.as_dict(),
                        )
                    )
                    continue

                if packet.get("type") != "user_input":
                    await ws.send_json(
                        build_event(
                            "error",
                            session_id,
                            packet.get("turn_id") or "system",
                            message="unsupported event type",
                            received_type=packet.get("type"),
                        )
                    )
                    continue

                if session_state.suspended:
                    await ws.send_json(
                        build_event(
                            "session_blocked",
                            session_id,
                            packet.get("turn_id") or "system",
                            detail="session is suspended; send resume_session before user_input",
                            session_state=session_state.as_dict(),
                        )
                    )
                    continue

                user_text = (packet.get("content") or "").strip()
                if not user_text:
                    await ws.send_json(
                        build_event(
                            "error",
                            session_id,
                            packet.get("turn_id") or "system",
                            message="content is empty",
                        )
                    )
                    continue

                messages.append({"role": "user", "content": user_text})
                turn_id = str(uuid.uuid4())
                await ws.send_json(
                    build_event(
                        "turn_started",
                        session_id,
                        turn_id,
                        session_state=session_state.as_dict(),
                    )
                )

                messages = await run_turn(
                    ws,
                    registry,
                    messages,
                    session_state,
                    session_id,
                    turn_id,
                )

                final_text = ""
                for msg in reversed(messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        final_text = msg["content"]
                        break

                if session_state.suspended:
                    final_text = session_state.suspended_detail or "会话已挂起，请恢复后继续。"

                await ws.send_json(
                    build_event(
                        "final_answer",
                        session_id,
                        turn_id,
                        content=final_text,
                        session_state=session_state.as_dict(),
                    )
                )
        except WebSocketDisconnect:
            return

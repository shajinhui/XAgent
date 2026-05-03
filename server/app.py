from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from tools.registry import ToolRegistry

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
except ModuleNotFoundError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    WebSocket = None  # type: ignore[assignment]
    WebSocketDisconnect = Exception  # type: ignore[assignment]


class AgentState(TypedDict):
    messages: List[Dict[str, Any]]


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
        "timestamp": time.time(),
        **payload,
    }


def build_model_name() -> str:
    provider = os.getenv("MODEL_PROVIDER", "openai").strip()
    model = os.getenv("MODEL_NAME", "gpt-4o-mini").strip()
    return f"{provider}/{model}"


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

    async def run_turn(
        ws: WebSocket,
        registry: ToolRegistry,
        messages: List[Dict[str, Any]],
        session_id: str,
        turn_id: str,
    ) -> List[Dict[str, Any]]:
        while True:
            from litellm import completion

            response = completion(
                model=build_model_name(),
                messages=messages,
                tools=registry.schemas(),
                tool_choice="auto",
                temperature=0,
            )
            message = response.choices[0].message.model_dump(exclude_none=True)
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
                            detail=result.content,
                            metadata=metadata,
                        )
                    )
                    decision = await ws.receive_json()
                    approved = (
                        decision.get("type") == "permission_decision"
                        and bool(decision.get("approved"))
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

                await ws.send_json(
                    build_event(
                        "tool_call_result",
                        session_id,
                        turn_id,
                        request_id=tool_call["id"],
                        name=tool_name,
                        ok=result.ok,
                        content=result.content,
                        metadata=metadata,
                    )
                )
                if metadata.get("session_suspended"):
                    await ws.send_json(
                        build_event(
                            "session_suspended",
                            session_id,
                            turn_id,
                            request_id=tool_call["id"],
                            category=metadata.get("category"),
                            detail=result.content,
                        )
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

    @app.websocket("/agent/ws")
    async def agent_ws(ws: WebSocket) -> None:
        await ws.accept()
        load_dotenv()

        project_root = Path(__file__).resolve().parents[1]
        session_id = "ws-default"
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
                "path": "/agent/ws",
                "tools": registry.metadata(),
            }
        )

        try:
            while True:
                packet = await ws.receive_json()
                if packet.get("type") != "user_input":
                    await ws.send_json({"type": "error", "message": "unsupported event type"})
                    continue

                user_text = (packet.get("content") or "").strip()
                if not user_text:
                    await ws.send_json({"type": "error", "message": "content is empty"})
                    continue

                messages.append({"role": "user", "content": user_text})
                turn_id = str(uuid.uuid4())
                await ws.send_json(build_event("turn_started", session_id, turn_id))

                messages = await run_turn(ws, registry, messages, session_id, turn_id)

                final_text = ""
                for msg in reversed(messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        final_text = msg["content"]
                        break

                # 简化版 token 推送：按词切片。
                for token in final_text.split(" "):
                    await ws.send_json(
                        build_event("assistant_token", session_id, turn_id, token=token + " ")
                    )
                    await asyncio.sleep(0.01)

                await ws.send_json(
                    build_event("final_answer", session_id, turn_id, content=final_text)
                )
        except WebSocketDisconnect:
            return

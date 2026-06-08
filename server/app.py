"""
FastAPI 服务入口与 WebSocket 路由。

这里尽量只保留传输层外壳：建立 WebSocket、发送 ready 事件、
把控制类 packet 交给 dispatcher，并把真正的模型回合交给 turn runner。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from server.processors.request_dispatcher import WebSocketRequestDispatcher
from server.protocol.events import EVENT_SCHEMA_VERSION, build_event, parse_client_packet
from server.runtime.model_config import (
    ModelRequestConfig,
    build_model_config_payload,
    build_model_name,
    build_model_request_config,
    configure_litellm_environment,
    normalize_reasoning_effort,
)
from server.runtime.session_state import persist_websocket_session
from server.runtime.transcript_events import record_transcript_event
from server.runtime.turn_runner import run_turn
from server.runtime.websocket_context import WebSocketRuntimeContext
from session.turn_context import TurnContext
from tools.core.registry import ToolRegistry
from tools.core.router import ToolRouter
from tools.core.runner import ToolRunner

try:
    from fastapi import FastAPI
    from fastapi import WebSocket as FastAPIWebSocket
    from fastapi import WebSocketDisconnect
except ModuleNotFoundError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    if not TYPE_CHECKING:
        FastAPIWebSocket = Any
    WebSocketDisconnect = Exception  # type: ignore[assignment]


class AgentState(TypedDict):
    """CLI/LangGraph 路径使用的最小状态。"""

    messages: List[Dict[str, Any]]


def build_system_prompt() -> str:
    """构建所有入口共用的系统提示。"""

    return (
        "你是一个代码助手。"
        "可以按需调用工具 read_file/ask_user/write_file/edit_file/grep/run_command。"
        "当用户意图、范围、偏好或风险接受度不清楚，并且直接假设会明显影响结果时，"
        "先调用 ask_user 提出一个简短澄清问题；如果可以做出安全、可逆的合理假设，就继续推进并说明假设。"
        "如果不需要工具，直接给出最终答案。"
    )


def build_graph(registry: ToolRegistry, runner: ToolRunner):
    """构建旧 CLI 路径使用的 LangGraph 图。

    WebSocket 主路径已经拆到 runtime/turn_runner.py；这里仅用于
    CLI 入口和早期测试，不承载桌面客户端协议逻辑。
    """

    def call_model(state: AgentState) -> AgentState:
        configure_litellm_environment()
        from litellm import completion

        response: Any = completion(
            **ModelRequestConfig(
                model=build_model_name(),
                reasoning_effort=normalize_reasoning_effort(None),
            ).completion_kwargs(),
            messages=state["messages"],
            tools=registry.schemas(),
            tool_choice="auto",
        )
        message = response.choices[0].message.model_dump(exclude_none=True)
        return {"messages": state["messages"] + [message]}

    # 说明：本函数构建的 LangGraph 仅用于 CLI/测试路径，生产环境的
    # WebSocket runtime 使用 run_turn 作为核心回合执行器，二者职责有所区分。

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if last.get("tool_calls"):
            return "tools"
        return "end"

    def call_tools(state: AgentState) -> AgentState:
        last = state["messages"][-1]
        new_messages = list(state["messages"])
        for tool_call in last.get("tool_calls", []):
            invocation = ToolRouter.build_tool_invocation(tool_call)
            result = runner.execute(
                name=invocation.name,
                arguments=invocation.arguments,
            )
            content = result.content if result.ok else f"[ERROR] {result.content}"
            new_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": invocation.call_id,
                    "name": invocation.name,
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

    @app.websocket("/agent/ws")
    async def agent_ws(ws: FastAPIWebSocket) -> None:
        """桌面客户端连接的 WebSocket runtime 入口。"""

        await ws.accept()
        load_dotenv()
        configure_litellm_environment()

        system_prompt = build_system_prompt()
        context = WebSocketRuntimeContext.create(
            Path(__file__).resolve().parents[1],
            system_prompt,
        )
        dispatcher = WebSocketRequestDispatcher(ws, context)

        # dispatcher 负责处理控制类事件（open_workspace/new_session/list_sessions 等），
        # 而模型的对话回合（model -> tool -> model 循环）由 runtime.turn_runner.run_turn 执行。

        await ws.send_json(
            {
                "type": "ready",
                "session_id": context.session_id,
                "schema_version": EVENT_SCHEMA_VERSION,
                "path": "/agent/ws",
                "tools": context.registry.metadata(),
                "session_state": context.session_state.as_dict(),
                "workspace": context.workspace.as_dict(),
                "model_config": build_model_config_payload(),
            }
        )

        # ready 事件将初始会话、工具元数据与工作区信息告知前端，前端据此渲染 UI 并
        # 决定何时向该 WebSocket 发送用户输入或控制事件。

        try:
            while True:
                raw_packet = await ws.receive_text()
                packet = parse_client_packet(raw_packet)
                if packet is None:
                    await dispatcher.handle_invalid_packet()
                    continue

                # 控制类 packet 会改变 workspace/session/title 等外围状态；
                # 只有真正的 user_input 会继续进入模型回合。
                if await dispatcher.handle_control_packet(packet):
                    continue

                if context.session_state.suspended:
                    if context.session_persisted:
                        record_transcript_event(
                            context.session_store,
                            context.session_id,
                            "session_blocked",
                            {
                                "turn_id": packet.get("turn_id") or "system",
                                "detail": "session is suspended; send resume_session before user_input",
                                "session_state": context.session_state.as_dict(),
                            },
                        )
                    await ws.send_json(
                        build_event(
                            "session_blocked",
                            context.session_id,
                            packet.get("turn_id") or "system",
                            detail="session is suspended; send resume_session before user_input",
                            session_state=context.session_state.as_dict(),
                        )
                    )
                    continue

                user_text = (packet.get("content") or "").strip()
                if not user_text:
                    if context.session_persisted:
                        record_transcript_event(
                            context.session_store,
                            context.session_id,
                            "runtime_error",
                            {
                                "turn_id": packet.get("turn_id") or "system",
                                "message": "content is empty",
                            },
                        )
                    await ws.send_json(
                        build_event(
                            "error",
                            context.session_id,
                            packet.get("turn_id") or "system",
                            message="content is empty",
                        )
                    )
                    continue

                model_config = build_model_request_config(packet)
                if not context.session_persisted:
                    # 首条非空用户输入到达时才持久化，避免打开应用或新建空会话污染历史。
                    persist_websocket_session(
                        context.session_store,
                        context.session_id,
                        context.workspace,
                    )
                    context.session_persisted = True
                # 普通 assistant 推理内容不参与下一轮上下文；带 tool_calls 的 reasoning
                # 仍保留在内存中以兼容 DeepSeek 的工具调用后的上下文拼接要求。
                turn_system_prompt = context.refresh_history_system_prompt()
                context.history.clear_historical_reasoning_content()
                context.history.append_user_message(user_text)
                turn_id = str(uuid.uuid4())
                # TurnContext 是“一轮用户输入”的运行态快照；它把连接级状态、
                # 模型配置、workspace、history 和工具运行器收束成一个参数传给 run_turn。
                turn_context = TurnContext.from_runtime(
                    session_id=context.session_id,
                    turn_id=turn_id,
                    workspace=context.workspace,
                    session_state=context.session_state,
                    registry=context.registry,
                    runner=context.runner,
                    history=context.history,
                    system_prompt=turn_system_prompt,
                    user_input=user_text,
                    model_config=model_config,
                )
                record_transcript_event(
                    context.session_store,
                    context.session_id,
                    "user_message",
                    {
                        "turn_id": turn_id,
                        "content": user_text,
                        "model_config": model_config.as_dict(),
                    },
                )
                record_transcript_event(
                    context.session_store,
                    context.session_id,
                    "turn_started",
                    {
                        "turn_id": turn_id,
                        "session_state": context.session_state.as_dict(),
                        "model_config": model_config.as_dict(),
                    },
                )
                await ws.send_json(
                    build_event(
                        "turn_started",
                        context.session_id,
                        turn_id,
                        session_state=context.session_state.as_dict(),
                        model_config=model_config.as_dict(),
                    )
                )

                try:
                    # run_turn 会持续执行 model -> tools -> model 循环，直到模型给出
                    # 没有 tool_calls 的 assistant 消息，或安全策略挂起当前 session。
                    context.history = await run_turn(
                        ws,
                        context.session_store,
                        turn_context,
                    )
                except Exception as exc:
                    record_transcript_event(
                        context.session_store,
                        context.session_id,
                        "runtime_error",
                        {
                            "turn_id": turn_id,
                            "message": f"model request failed: {exc}",
                            "error_type": type(exc).__name__,
                            "session_state": context.session_state.as_dict(),
                        },
                    )
                    await ws.send_json(
                        build_event(
                            "error",
                            context.session_id,
                            turn_id,
                            message=f"model request failed: {exc}",
                            error_type=type(exc).__name__,
                            session_state=context.session_state.as_dict(),
                        )
                    )
                    continue

                final_text = ""
                for msg in reversed(context.messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        final_text = msg["content"]
                        break

                if context.session_state.suspended:
                    final_text = context.session_state.suspended_detail or "会话已挂起，请恢复后继续。"

                record_transcript_event(
                    context.session_store,
                    context.session_id,
                    "final_answer",
                    {
                        "turn_id": turn_id,
                        "content": final_text,
                        "session_state": context.session_state.as_dict(),
                    },
                )
                await ws.send_json(
                    build_event(
                        "final_answer",
                        context.session_id,
                        turn_id,
                        content=final_text,
                        session_state=context.session_state.as_dict(),
                    )
                )
        except WebSocketDisconnect:
            return

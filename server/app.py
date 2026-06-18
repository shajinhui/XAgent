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
from server.processors.task_list_processor import fallback_task_list, generate_task_list
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
from server.runtime.turn_runner import TurnCancelled, run_turn
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

                accepted_plan = packet.get("_accepted_plan")
                if not isinstance(accepted_plan, dict):
                    accepted_plan = None
                if accepted_plan:
                    context.pending_plan = None
                elif context.pending_plan:
                    # 用户绕过挂起计划直接发普通消息时，旧计划不再适用于当前 turn。
                    context.pending_plan = None

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
                context.session_state.start_turn(turn_id)
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
                if accepted_plan:
                    record_transcript_event(
                        context.session_store,
                        context.session_id,
                        "plan_confirmed",
                        {
                            "turn_id": turn_id,
                            "request_id": packet.get("_plan_request_id") or packet.get("request_id"),
                            "plan_id": accepted_plan.get("plan_id"),
                            "content": accepted_plan.get("content"),
                            "items": accepted_plan.get("items") or [],
                            "model": accepted_plan.get("model"),
                        },
                    )
                user_message_payload = {
                    "turn_id": turn_id,
                    "content": user_text,
                    "model_config": model_config.as_dict(),
                }
                if accepted_plan:
                    user_message_payload["accepted_plan"] = {
                        "plan_id": accepted_plan.get("plan_id"),
                        "items": accepted_plan.get("items") or [],
                        "model": accepted_plan.get("model"),
                    }
                record_transcript_event(
                    context.session_store,
                    context.session_id,
                    "user_message",
                    user_message_payload,
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
                task_list_plan_id = None
                task_list_source = "auto"
                if accepted_plan:
                    task_list = accepted_plan.get("items") or []
                    task_list_model = str(accepted_plan.get("model") or "plan-confirmed")
                    task_list_plan_id = accepted_plan.get("plan_id")
                    task_list_source = "plan_confirmed"
                else:
                    try:
                        task_list, task_list_model = generate_task_list(user_text)
                    except Exception as exc:
                        task_list = fallback_task_list(user_text)
                        task_list_model = f"fallback:{type(exc).__name__}"

                record_transcript_event(
                    context.session_store,
                    context.session_id,
                    "task_list",
                    {
                        "turn_id": turn_id,
                        "items": task_list,
                        "model": task_list_model,
                        "source": task_list_source,
                        "plan_id": task_list_plan_id,
                    },
                )
                await ws.send_json(
                    build_event(
                        "task_list",
                        context.session_id,
                        turn_id,
                        items=task_list,
                        model=task_list_model,
                        source=task_list_source,
                        plan_id=task_list_plan_id,
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
                except TurnCancelled as exc:
                    context.session_state.request_cancellation()
                    requested_state = context.session_state.as_dict()
                    context.session_state.finish_turn()
                    record_transcript_event(
                        context.session_store,
                        context.session_id,
                        "turn_cancelled",
                        {
                            "turn_id": turn_id,
                            "detail": str(exc),
                            "requested_state": requested_state,
                            "session_state": context.session_state.as_dict(),
                        },
                    )
                    await ws.send_json(
                        build_event(
                            "turn_cancelled",
                            context.session_id,
                            turn_id,
                            detail=str(exc),
                            session_state=context.session_state.as_dict(),
                        )
                    )
                    continue
                except Exception as exc:
                    context.session_state.finish_turn()
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

                context.session_state.finish_turn()

                # 保存 diff_tracker 以便后续撤销
                context.last_diff_tracker = turn_context.diff_tracker

                # 获取本 turn 变更的文件
                changed_files = turn_context.diff_tracker.get_changed_files()

                # 构建变更文件列表: 相对路径 + 增删行数
                project_root = context.workspace.project_root if context.workspace else None
                changed_files_payload = []
                for abs_path, diff in changed_files.items():
                    # 转换为项目相对路径
                    rel_path = abs_path
                    if project_root:
                        try:
                            rel_path = str(Path(abs_path).relative_to(project_root))
                        except ValueError:
                            pass
                    # 计算增删行数
                    before_lines = diff["before"].splitlines() if diff["before"] else []
                    after_lines = diff["after"].splitlines() if diff["after"] else []
                    additions = max(0, len(after_lines) - len(before_lines))
                    deletions = max(0, len(before_lines) - len(after_lines))
                    # 逐行比对: 统计实际变更行（不只是净增减）
                    common = min(len(before_lines), len(after_lines))
                    changed = sum(1 for i in range(common) if before_lines[i] != after_lines[i])
                    additions += changed
                    deletions += changed
                    changed_files_payload.append({
                        "path": rel_path,
                        "abs_path": abs_path,
                        "can_undo": True,
                        "additions": additions,
                        "deletions": deletions,
                    })

                final_text = ""
                for msg in reversed(context.messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        final_text = msg["content"]
                        break

                if context.session_state.suspended:
                    final_text = context.session_state.suspended_detail or "会话已挂起，请恢复后继续。"

                # 确保所有缓冲区已刷新
                context.session_store.writer(context.session_id).flush()
                if hasattr(context.session_store, '_flush_index_update'):
                    context.session_store._flush_index_update(context.session_id)

                record_transcript_event(
                    context.session_store,
                    context.session_id,
                    "final_answer",
                    {
                        "turn_id": turn_id,
                        "content": final_text,
                        "session_state": context.session_state.as_dict(),
                        "changed_files": list(changed_files.keys()),
                    },
                )
                await ws.send_json(
                    build_event(
                        "final_answer",
                        context.session_id,
                        turn_id,
                        content=final_text,
                        session_state=context.session_state.as_dict(),
                        changed_files=[
                            {
                                "path": f["path"],
                                "can_undo": f["can_undo"],
                                "additions": f["additions"],
                                "deletions": f["deletions"],
                            }
                            for f in changed_files_payload
                        ],
                    )
                )
        except WebSocketDisconnect:
            return

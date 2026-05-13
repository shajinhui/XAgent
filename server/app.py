from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from session import SessionRecord, SessionStore, TranscriptEvent, recover_session_messages
from tools.registry import ToolRegistry
from tools.types import ToolResult
from workspace import WorkspaceContext, WorkspaceManager, WorkspaceValidationError

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
except ModuleNotFoundError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    WebSocket = None  # type: ignore[assignment]
    WebSocketDisconnect = Exception  # type: ignore[assignment]


class AgentState(TypedDict):
    messages: List[Dict[str, Any]]


EVENT_SCHEMA_VERSION = "2026-05-05"
CONVERSATION_TITLE_MODEL_SOURCE = "model-first-user"
DEFAULT_MODEL_OPTIONS = (
    "openai/gpt-4o-mini",
    "deepseek/deepseek-chat",
    "deepseek/deepseek-reasoner",
)
REASONING_EFFORT_OPTIONS = ("off", "low", "medium", "high")
REASONING_EFFORT_ALIASES = {
    "": "off",
    "none": "off",
    "disabled": "off",
    "false": "off",
    "0": "off",
    "minimal": "low",
    "normal": "medium",
}


@dataclass(frozen=True)
class ModelRequestConfig:
    model: str
    reasoning_effort: str = "off"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
        }

    def completion_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
        }
        if self.reasoning_effort != "off":
            kwargs["reasoning_effort"] = self.reasoning_effort
        return kwargs


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


def record_transcript_event(
    store: SessionStore,
    session_id: str,
    event_type: str,
    payload: Dict[str, Any] | None = None,
) -> None:
    store.append_event(session_id, event_type, payload or {})


def assistant_transcript_payload(message: Dict[str, Any], turn_id: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "turn_id": turn_id,
        "content": message.get("content") or "",
    }
    if message.get("tool_calls"):
        payload["tool_calls"] = message["tool_calls"]
    return payload


def summarize_session_record(
    record: SessionRecord,
    events: List[TranscriptEvent],
) -> Dict[str, Any]:
    title = record.title or _derive_session_title(events)
    last_message = _derive_last_user_message(events)
    last_message_at = _last_model_message_timestamp(events) or record.updated_at
    return {
        "session_id": record.session_id,
        "title": title,
        "created_at": record.created_at,
        "updated_at": last_message_at,
        "last_turn_id": record.last_turn_id,
        "message_count": _count_model_messages(events),
        "last_message": last_message,
    }


def session_display_messages(events: List[TranscriptEvent]) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    for event in events:
        if event.type not in {"user_message", "assistant_message"}:
            continue

        role = "user" if event.type == "user_message" else "assistant"
        content = str(event.payload.get("content") or "").strip()
        if not content:
            continue

        messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": event.timestamp,
            }
        )

    return messages


def list_session_summaries(store: SessionStore, limit: int = 20) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(limit, 50))
    summaries: List[Dict[str, Any]] = []
    for record in store.list_sessions():
        summary = summarize_session_record(record, store.load_events(record.session_id))
        if summary["message_count"] > 0:
            summaries.append(summary)
    summaries.sort(key=lambda summary: (summary["updated_at"], summary["session_id"]), reverse=True)
    return summaries[:safe_limit]


def _derive_session_title(events: List[TranscriptEvent]) -> str:
    for event in reversed(events):
        if event.type != "conversation_title":
            continue
        title = str(event.payload.get("title") or "").strip()
        if title:
            return title

    for event in events:
        if event.type != "user_message":
            continue
        content = str(event.payload.get("content") or "").strip()
        if content:
            return sanitize_conversation_title(content)

    return "新对话"


def _derive_last_user_message(events: List[TranscriptEvent]) -> str:
    for event in reversed(events):
        if event.type != "user_message":
            continue
        content = str(event.payload.get("content") or "").strip()
        if content:
            return content[:160]
    return ""


def _count_model_messages(events: List[TranscriptEvent]) -> int:
    return sum(1 for event in events if event.type in {"user_message", "assistant_message"})


def _last_model_message_timestamp(events: List[TranscriptEvent]) -> float | None:
    for event in reversed(events):
        if event.type in {"user_message", "assistant_message"}:
            return event.timestamp
    return None


def normalize_model_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    model = value.strip()
    if not model or len(model) > 160:
        return None
    if any(char.isspace() for char in model):
        return None
    if "/" not in model:
        return None
    return model


def build_default_model_name() -> str:
    provider = os.getenv("MODEL_PROVIDER", "openai").strip()
    model = os.getenv("MODEL_NAME", "gpt-4o-mini").strip()
    return f"{provider}/{model}"


def build_model_name(model_override: Any | None = None) -> str:
    return normalize_model_name(model_override) or build_default_model_name()


def normalize_reasoning_effort(value: Any) -> str:
    if value is None:
        value = os.getenv("REASONING_EFFORT", "off")
    if not isinstance(value, str):
        return "off"

    effort = value.strip().lower()
    effort = REASONING_EFFORT_ALIASES.get(effort, effort)
    if effort in REASONING_EFFORT_OPTIONS:
        return effort
    return "off"


def _parse_model_option_list(raw_value: str) -> List[str]:
    options: List[str] = []
    for item in raw_value.split(","):
        model = normalize_model_name(item)
        if model and model not in options:
            options.append(model)
    return options


def build_model_options() -> List[str]:
    options = [
        build_default_model_name(),
        *_parse_model_option_list(os.getenv("MODEL_OPTIONS", "")),
        *DEFAULT_MODEL_OPTIONS,
    ]
    return list(dict.fromkeys(option for option in options if normalize_model_name(option)))


def build_model_request_config(packet: Dict[str, Any] | None = None) -> ModelRequestConfig:
    packet = packet or {}
    return ModelRequestConfig(
        model=build_model_name(packet.get("model")),
        reasoning_effort=normalize_reasoning_effort(packet.get("reasoning_effort")),
    )


def build_model_config_payload() -> Dict[str, Any]:
    return {
        "default_model": build_default_model_name(),
        "model_options": build_model_options(),
        "reasoning_effort": normalize_reasoning_effort(None),
        "reasoning_effort_options": list(REASONING_EFFORT_OPTIONS),
    }


def build_system_prompt() -> str:
    return (
        "你是一个代码助手。"
        "可以按需调用工具 read_file/write_file/edit_file/grep/run_command。"
        "如果不需要工具，直接给出最终答案。"
    )


def create_websocket_session(
    workspace: WorkspaceContext,
    system_prompt: str,
) -> tuple[str, SessionRuntimeState, ToolRegistry, List[Dict[str, Any]]]:
    if workspace.session_store is None:
        raise ValueError("workspace session store is not initialized")

    session_store = workspace.session_store
    session_record = session_store.create_session(
        metadata={
            "transport": "websocket",
            "schema_version": EVENT_SCHEMA_VERSION,
            "workspace": workspace.as_dict(),
        },
    )
    session_id = session_record.session_id
    session_state = SessionRuntimeState(session_id=session_id)
    registry = ToolRegistry(project_root=workspace.root, session_id=session_id)
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    return session_id, session_state, registry, messages


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


def sanitize_conversation_title(raw_title: str) -> str:
    title = " ".join(raw_title.strip().split())
    title = title.strip("`'\"“”‘’# ")
    for prefix in ("标题：", "标题:", "Title:", "Title："):
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()
            break

    if not title:
        return "新对话"
    if len(title) <= 18:
        return title
    return f"{title[:18]}..."


def first_user_message_content(messages: List[Dict[str, str]]) -> str:
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if content:
            return content
    return ""


def normalize_title_messages(messages: Any) -> List[Dict[str, str]]:
    if not isinstance(messages, list):
        return []

    normalized: List[Dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        normalized.append({"role": role, "content": content[:1200]})

    return normalized[-8:]


def extract_completion_text(response: Any) -> str:
    response_dict = _object_to_dict(response)
    choices = response_dict.get("choices")
    if choices is None:
        choices = getattr(response, "choices", [])
    if not choices:
        return ""

    choice = choices[0]
    choice_dict = _object_to_dict(choice)
    message = choice_dict.get("message")
    if message is None:
        message = getattr(choice, "message", None)

    message_dict = _object_to_dict(message)
    content = message_dict.get("content")
    if content is None:
        content = getattr(message, "content", "")
    return str(content or "")


def parse_client_packet(raw_packet: str) -> Dict[str, Any] | None:
    try:
        packet = json.loads(raw_packet)
    except json.JSONDecodeError:
        return None
    if not isinstance(packet, dict):
        return None
    return packet


def generate_conversation_title(
    messages: List[Dict[str, str]],
    completion_fn: Any | None = None,
) -> tuple[str, str]:
    first_user_content = first_user_message_content(messages)
    if not first_user_content:
        return "新对话", CONVERSATION_TITLE_MODEL_SOURCE

    if completion_fn is None:
        from litellm import completion as completion_fn

    response = completion_fn(
        model=build_model_name(),
        messages=[
            {
                "role": "system",
                "content": (
                    "你负责给对话生成简短标题。"
                    "只根据用户第一句提问概括主题，输出一个中文短标题。"
                    "不要解释，不要加引号，不要加“标题：”前缀，长度控制在 18 个字以内。"
                ),
            },
            {
                "role": "user",
                "content": f"用户第一句提问：{first_user_content}",
            },
        ],
        temperature=0,
        max_tokens=32,
        extra_body={"thinking": {"type": "disabled"}},
    )
    title = sanitize_conversation_title(extract_completion_text(response))
    if title == "新对话":
        raise ValueError("conversation title model returned an empty title")
    return title, CONVERSATION_TITLE_MODEL_SOURCE


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


def build_assistant_message(
    content: str,
    tool_call_buffers: Dict[int, Dict[str, Any]],
    reasoning_content: str = "",
) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    if tool_call_buffers:
        tool_calls: List[Dict[str, Any]] = []
        for index, tool_call in sorted(tool_call_buffers.items()):
            if not tool_call.get("id"):
                tool_call["id"] = f"tool_call_{index}"
            tool_calls.append(tool_call)
        message["tool_calls"] = tool_calls
    return message


def denied_tool_result(
    tool_name: str,
    command_or_detail: str,
    metadata: Dict[str, Any],
    user_feedback: str | None = None,
) -> ToolResult:
    denied_metadata = {
        "tool": tool_name,
        "error_type": "permission_denied",
        "permission_action": "deny",
        "category": metadata.get("category", "user_denied"),
        "user_denied": True,
    }
    if metadata.get("command"):
        denied_metadata["command"] = metadata["command"]
    if user_feedback:
        denied_metadata["user_feedback"] = user_feedback

    content = f"用户拒绝执行工具 {tool_name}: {command_or_detail}"
    if user_feedback:
        content += f"\n用户希望你这样调整方案: {user_feedback}"

    return ToolResult(
        ok=False,
        content=content,
        metadata=denied_metadata,
    )


def clear_historical_reasoning_content(messages: List[Dict[str, Any]]) -> None:
    for message in messages:
        if message.get("role") == "assistant":
            message.pop("reasoning_content", None)


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
        model_config: ModelRequestConfig,
    ) -> Dict[str, Any]:
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
            delta = _extract_stream_delta(chunk)
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
                merge_tool_call_delta(tool_call_buffers, _object_to_dict(tool_call_delta))

            await asyncio.sleep(0)

        return build_assistant_message(
            "".join(content_parts),
            tool_call_buffers,
            reasoning_content="".join(reasoning_parts),
        )

    async def wait_for_permission_decision(
        ws: WebSocket,
        session_id: str,
        turn_id: str,
        request_id: str,
    ) -> tuple[bool, str | None]:
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

    async def emit_tool_result(
        ws: WebSocket,
        session_store: SessionStore,
        result: ToolResult,
        session_state: SessionRuntimeState,
        session_id: str,
        turn_id: str,
        request_id: str,
        tool_name: str,
    ) -> None:
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
        ws: WebSocket,
        session_store: SessionStore,
        registry: ToolRegistry,
        messages: List[Dict[str, Any]],
        session_state: SessionRuntimeState,
        session_id: str,
        turn_id: str,
        model_config: ModelRequestConfig,
    ) -> List[Dict[str, Any]]:
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

            for tool_call in tool_calls:
                fn = tool_call["function"]
                tool_name = fn["name"]
                arguments = fn.get("arguments", "{}")
                record_transcript_event(
                    session_store,
                    session_id,
                    "tool_call_started",
                    {
                        "turn_id": turn_id,
                        "request_id": tool_call["id"],
                        "tool": tool_name,
                        "arguments": arguments,
                    },
                )
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
                    record_transcript_event(
                        session_store,
                        session_id,
                        "permission_request",
                        {
                            "turn_id": turn_id,
                            "request_id": tool_call["id"],
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
                            request_id=tool_call["id"],
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
                        tool_call["id"],
                    )
                    record_transcript_event(
                        session_store,
                        session_id,
                        "permission_decision",
                        {
                            "turn_id": turn_id,
                            "request_id": tool_call["id"],
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

        workspace_manager = WorkspaceManager(Path(__file__).resolve().parents[1])
        workspace = workspace_manager.open()
        session_store = workspace.session_store
        if session_store is None:
            raise RuntimeError("workspace session store is not initialized")
        system_prompt = build_system_prompt()
        session_id, session_state, registry, messages = create_websocket_session(
            workspace,
            system_prompt,
        )

        await ws.send_json(
            {
                "type": "ready",
                "session_id": session_id,
                "schema_version": EVENT_SCHEMA_VERSION,
                "path": "/agent/ws",
                "tools": registry.metadata(),
                "session_state": session_state.as_dict(),
                "workspace": workspace.as_dict(),
                "model_config": build_model_config_payload(),
            }
        )

        try:
            while True:
                raw_packet = await ws.receive_text()
                packet = parse_client_packet(raw_packet)
                if packet is None:
                    record_transcript_event(
                        session_store,
                        session_id,
                        "runtime_error",
                        {
                            "turn_id": "system",
                            "message": "invalid JSON packet",
                            "received_type": "invalid_json",
                        },
                    )
                    await ws.send_json(
                        build_event(
                            "error",
                            session_id,
                            "system",
                            message="invalid JSON packet",
                            received_type="invalid_json",
                        )
                    )
                    continue

                packet_type = packet.get("type")
                if packet_type == "open_workspace":
                    request_id = packet.get("request_id") or str(uuid.uuid4())
                    requested_path = str(packet.get("path") or "").strip()
                    if not requested_path:
                        record_transcript_event(
                            session_store,
                            session_id,
                            "runtime_error",
                            {
                                "turn_id": packet.get("turn_id") or "system",
                                "request_id": request_id,
                                "message": "workspace path is empty",
                            },
                        )
                        await ws.send_json(
                            build_event(
                                "error",
                                session_id,
                                packet.get("turn_id") or "system",
                                request_id=request_id,
                                message="workspace path is empty",
                            )
                        )
                        continue

                    try:
                        next_workspace = workspace_manager.open(requested_path)
                    except WorkspaceValidationError as exc:
                        record_transcript_event(
                            session_store,
                            session_id,
                            "runtime_error",
                            {
                                "turn_id": packet.get("turn_id") or "system",
                                "request_id": request_id,
                                "message": str(exc),
                                "requested_workspace": requested_path,
                            },
                        )
                        await ws.send_json(
                            build_event(
                                "workspace_error",
                                session_id,
                                packet.get("turn_id") or "system",
                                request_id=request_id,
                                message=str(exc),
                                requested_workspace=requested_path,
                                workspace=workspace.as_dict(),
                            )
                        )
                        continue

                    previous_workspace = workspace.as_dict()
                    previous_state = session_state.as_dict()
                    workspace = next_workspace
                    session_store = workspace.session_store
                    if session_store is None:
                        raise RuntimeError("workspace session store is not initialized")
                    session_id, session_state, registry, messages = create_websocket_session(
                        workspace,
                        system_prompt,
                    )
                    record_transcript_event(
                        session_store,
                        session_id,
                        "workspace_opened",
                        {
                            "turn_id": packet.get("turn_id") or "system",
                            "request_id": request_id,
                            "previous_workspace": previous_workspace,
                            "workspace": workspace.as_dict(),
                            "previous_state": previous_state,
                            "session_state": session_state.as_dict(),
                        },
                    )
                    await ws.send_json(
                        build_event(
                            "workspace_changed",
                            session_id,
                            packet.get("turn_id") or "system",
                            request_id=request_id,
                            previous_workspace=previous_workspace,
                            workspace=workspace.as_dict(),
                            previous_state=previous_state,
                            session_state=session_state.as_dict(),
                            tools=registry.metadata(),
                        )
                    )
                    continue

                if packet_type == "new_session":
                    previous_state = session_state.as_dict()
                    session_id, session_state, registry, messages = create_websocket_session(
                        workspace,
                        system_prompt,
                    )
                    await ws.send_json(
                        build_event(
                            "session_created",
                            session_id,
                            packet.get("turn_id") or "system",
                            request_id=packet.get("request_id") or str(uuid.uuid4()),
                            previous_state=previous_state,
                            session_state=session_state.as_dict(),
                            workspace=workspace.as_dict(),
                        )
                    )
                    continue

                if packet_type == "list_sessions":
                    raw_limit = packet.get("limit", 20)
                    try:
                        limit = int(raw_limit)
                    except (TypeError, ValueError):
                        limit = 20

                    sessions = list_session_summaries(session_store, limit=limit)
                    await ws.send_json(
                        build_event(
                            "sessions_list",
                            session_id,
                            packet.get("turn_id") or "system",
                            request_id=packet.get("request_id") or str(uuid.uuid4()),
                            sessions=sessions,
                            workspace=workspace.as_dict(),
                        )
                    )
                    continue

                if packet_type == "delete_session":
                    request_id = packet.get("request_id") or str(uuid.uuid4())
                    target_session_id = str(packet.get("session_id") or "").strip()
                    requested_workspace = str(packet.get("workspace_path") or "").strip()

                    if not target_session_id:
                        await ws.send_json(
                            build_event(
                                "error",
                                session_id,
                                packet.get("turn_id") or "system",
                                request_id=request_id,
                                message="session_id is required",
                            )
                        )
                        continue

                    if requested_workspace:
                        try:
                            target_workspace = workspace_manager.open(requested_workspace)
                        except WorkspaceValidationError as exc:
                            await ws.send_json(
                                build_event(
                                    "workspace_error",
                                    session_id,
                                    packet.get("turn_id") or "system",
                                    request_id=request_id,
                                    message=str(exc),
                                    requested_workspace=requested_workspace,
                                    workspace=workspace.as_dict(),
                                )
                            )
                            continue
                    else:
                        target_workspace = workspace

                    target_store = target_workspace.session_store
                    if target_store is None:
                        raise RuntimeError("workspace session store is not initialized")

                    try:
                        target_store.delete_session(target_session_id)
                    except KeyError:
                        await ws.send_json(
                            build_event(
                                "error",
                                session_id,
                                packet.get("turn_id") or "system",
                                request_id=request_id,
                                message=f"unknown session: {target_session_id}",
                                requested_session_id=target_session_id,
                            )
                        )
                        continue

                    deleted_current = (
                        target_workspace.root == workspace.root and target_session_id == session_id
                    )
                    if deleted_current:
                        session_id, session_state, registry, messages = create_websocket_session(
                            workspace,
                            system_prompt,
                        )

                    sessions = list_session_summaries(target_store, limit=30)
                    await ws.send_json(
                        build_event(
                            "session_deleted",
                            session_id,
                            packet.get("turn_id") or "system",
                            request_id=request_id,
                            deleted_session_id=target_session_id,
                            deleted_current=deleted_current,
                            session_state=session_state.as_dict(),
                            workspace=target_workspace.as_dict(),
                            sessions=sessions,
                        )
                    )
                    continue

                if packet_type == "resume_session":
                    previous_state = session_state.as_dict()
                    target_session_id = str(packet.get("session_id") or "").strip()
                    if target_session_id:
                        try:
                            messages = recover_session_messages(
                                session_store,
                                target_session_id,
                                system_prompt,
                            )
                            target_record = session_store.get_session(target_session_id)
                            target_events = session_store.load_events(target_session_id)
                        except KeyError:
                            record_transcript_event(
                                session_store,
                                session_id,
                                "runtime_error",
                                {
                                    "turn_id": packet.get("turn_id") or "system",
                                    "message": f"unknown session: {target_session_id}",
                                    "requested_session_id": target_session_id,
                                },
                            )
                            await ws.send_json(
                                build_event(
                                    "error",
                                    session_id,
                                    packet.get("turn_id") or "system",
                                    message=f"unknown session: {target_session_id}",
                                    requested_session_id=target_session_id,
                                )
                            )
                            continue

                        session_id = target_session_id
                        session_state = SessionRuntimeState(session_id=session_id)
                        registry = ToolRegistry(project_root=workspace.root, session_id=session_id)
                        display_messages = session_display_messages(target_events)
                        session_summary = summarize_session_record(target_record, target_events)
                        resumed_from_disk = True
                    else:
                        suspended_category = session_state.suspended_category
                        session_state.resume()
                        registry.ctx.circuit_breaker.reset(session_id, suspended_category)
                        display_messages = []
                        session_summary = None
                        resumed_from_disk = False

                    record_transcript_event(
                        session_store,
                        session_id,
                        "session_resumed",
                        {
                            "turn_id": packet.get("turn_id") or "system",
                            "previous_state": previous_state,
                            "session_state": session_state.as_dict(),
                            "resumed_from_disk": resumed_from_disk,
                            "message_count": len(messages),
                        },
                    )
                    await ws.send_json(
                        build_event(
                            "session_resumed",
                            session_id,
                            packet.get("turn_id") or "system",
                            previous_state=previous_state,
                            session_state=session_state.as_dict(),
                            resumed_from_disk=resumed_from_disk,
                            message_count=len(messages),
                            messages=display_messages,
                            session=session_summary,
                            workspace=workspace.as_dict(),
                        )
                    )
                    continue

                if packet_type == "conversation_title_request":
                    request_id = packet.get("request_id") or str(uuid.uuid4())
                    title_messages = normalize_title_messages(packet.get("messages"))
                    if not title_messages:
                        record_transcript_event(
                            session_store,
                            session_id,
                            "runtime_error",
                            {
                                "turn_id": packet.get("turn_id") or "title",
                                "request_id": request_id,
                                "message": "conversation title messages are empty",
                            },
                        )
                        await ws.send_json(
                            build_event(
                                "error",
                                session_id,
                                packet.get("turn_id") or "title",
                                request_id=request_id,
                                message="conversation title messages are empty",
                            )
                        )
                        continue

                    try:
                        title, title_model = generate_conversation_title(title_messages)
                    except Exception as exc:
                        record_transcript_event(
                            session_store,
                            session_id,
                            "runtime_error",
                            {
                                "turn_id": packet.get("turn_id") or "title",
                                "request_id": request_id,
                                "message": f"conversation title request failed: {exc}",
                                "error_type": type(exc).__name__,
                            },
                        )
                        await ws.send_json(
                            build_event(
                                "error",
                                session_id,
                                packet.get("turn_id") or "title",
                                request_id=request_id,
                                message=f"conversation title request failed: {exc}",
                                error_type=type(exc).__name__,
                            )
                        )
                        continue

                    await ws.send_json(
                        build_event(
                            "conversation_title",
                            session_id,
                            packet.get("turn_id") or "title",
                            request_id=request_id,
                            title=title,
                            model=title_model,
                        )
                    )
                    record_transcript_event(
                        session_store,
                        session_id,
                        "conversation_title",
                        {
                            "turn_id": packet.get("turn_id") or "title",
                            "request_id": request_id,
                            "title": title,
                            "model": title_model,
                        },
                    )
                    continue

                if packet_type != "user_input":
                    record_transcript_event(
                        session_store,
                        session_id,
                        "runtime_error",
                        {
                            "turn_id": packet.get("turn_id") or "system",
                            "message": "unsupported event type",
                            "received_type": packet.get("type"),
                        },
                    )
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
                    record_transcript_event(
                        session_store,
                        session_id,
                        "session_blocked",
                        {
                            "turn_id": packet.get("turn_id") or "system",
                            "detail": "session is suspended; send resume_session before user_input",
                            "session_state": session_state.as_dict(),
                        },
                    )
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
                    record_transcript_event(
                        session_store,
                        session_id,
                        "runtime_error",
                        {
                            "turn_id": packet.get("turn_id") or "system",
                            "message": "content is empty",
                        },
                    )
                    await ws.send_json(
                        build_event(
                            "error",
                            session_id,
                            packet.get("turn_id") or "system",
                            message="content is empty",
                        )
                    )
                    continue

                model_config = build_model_request_config(packet)
                clear_historical_reasoning_content(messages)
                messages.append({"role": "user", "content": user_text})
                turn_id = str(uuid.uuid4())
                record_transcript_event(
                    session_store,
                    session_id,
                    "user_message",
                    {
                        "turn_id": turn_id,
                        "content": user_text,
                        "model_config": model_config.as_dict(),
                    },
                )
                record_transcript_event(
                    session_store,
                    session_id,
                    "turn_started",
                    {
                        "turn_id": turn_id,
                        "session_state": session_state.as_dict(),
                        "model_config": model_config.as_dict(),
                    },
                )
                await ws.send_json(
                    build_event(
                        "turn_started",
                        session_id,
                        turn_id,
                        session_state=session_state.as_dict(),
                        model_config=model_config.as_dict(),
                    )
                )

                try:
                    messages = await run_turn(
                        ws,
                        session_store,
                        registry,
                        messages,
                        session_state,
                        session_id,
                        turn_id,
                        model_config,
                    )
                except Exception as exc:
                    record_transcript_event(
                        session_store,
                        session_id,
                        "runtime_error",
                        {
                            "turn_id": turn_id,
                            "message": f"model request failed: {exc}",
                            "error_type": type(exc).__name__,
                            "session_state": session_state.as_dict(),
                        },
                    )
                    await ws.send_json(
                        build_event(
                            "error",
                            session_id,
                            turn_id,
                            message=f"model request failed: {exc}",
                            error_type=type(exc).__name__,
                            session_state=session_state.as_dict(),
                        )
                    )
                    continue

                final_text = ""
                for msg in reversed(messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        final_text = msg["content"]
                        break

                if session_state.suspended:
                    final_text = session_state.suspended_detail or "会话已挂起，请恢复后继续。"

                record_transcript_event(
                    session_store,
                    session_id,
                    "final_answer",
                    {
                        "turn_id": turn_id,
                        "content": final_text,
                        "session_state": session_state.as_dict(),
                    },
                )
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

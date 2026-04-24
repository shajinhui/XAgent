from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from litellm import completion

from tools.toolkit import execute_tool_call, get_tool_schemas


class AgentState(TypedDict):
    """Agent 状态：只维护消息列表。"""

    messages: List[Dict[str, Any]]


def _build_model_name() -> str:
    provider = os.getenv("MODEL_PROVIDER", "openai").strip()
    model = os.getenv("MODEL_NAME", "gpt-4o-mini").strip()
    return f"{provider}/{model}"


def call_model(state: AgentState) -> AgentState:
    """调用模型节点。"""

    model_name = _build_model_name()
    response = completion(
        model=model_name,
        messages=state["messages"],
        tools=get_tool_schemas(),
        tool_choice="auto",
        temperature=0,
    )
    message = response.choices[0].message.model_dump(exclude_none=True)
    return {"messages": state["messages"] + [message]}


def should_continue(state: AgentState) -> str:
    """如果模型发起了工具调用，则进入 tools 节点，否则结束。"""

    last = state["messages"][-1]
    if last.get("tool_calls"):
        return "tools"
    return "end"


def call_tools(state: AgentState) -> AgentState:
    """执行模型请求的全部工具调用。"""

    project_root = Path(__file__).parent.resolve()
    last = state["messages"][-1]
    new_messages = list(state["messages"])

    for tool_call in last.get("tool_calls", []):
        fn = tool_call["function"]
        ok, content = execute_tool_call(
            project_root=project_root,
            name=fn["name"],
            arguments=fn.get("arguments", "{}"),
        )
        if not ok:
            content = f"[ERROR] {content}"

        new_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "name": fn["name"],
                "content": content,
            }
        )

    return {"messages": new_messages}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("model", call_model)
    graph.add_node("tools", call_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges(
        "model",
        should_continue,
        {
            "tools": "tools",
            "end": END,
        },
    )
    graph.add_edge("tools", "model")
    return graph.compile()


def main() -> None:
    load_dotenv()

    # 若你接 DeepSeek，需要在 .env 中提供 DEEPSEEK_API_KEY，并把 MODEL_PROVIDER 设为 deepseek。
    if os.getenv("MODEL_PROVIDER", "openai") == "openai" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("缺少 OPENAI_API_KEY，请先在 .env 中配置")

    app = build_graph()
    system_prompt = (
        "你是一个代码助手。"
        "可以按需调用工具 read_file 和 run_command。"
        "如果不需要工具，直接给出最终答案。"
    )

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    print("Codex-mini 终端版已启动，输入 quit 退出。")
    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() in {"quit", "exit", "q"}:
            print("已退出。")
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        result = app.invoke({"messages": messages})
        messages = result["messages"]

        final_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                final_text = msg["content"]
                break

        print(f"\nAgent: {final_text or '(模型未返回文本，可查看工具调用结果)'}")


if __name__ == "__main__":
    main()

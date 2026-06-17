from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from prompts import PromptBuilder, SystemPromptConfig
from server.runtime.model_config import configure_litellm_environment
from tools.core.catalog import build_default_registry
from tools.core.registry import ToolRegistry
from tools.core.router import ToolRouter
from tools.core.runner import ToolRunner, create_tool_context


class AgentState(TypedDict):
    """Agent 的内部状态类型定义。

    目前代理只维护一份消息列表（`messages`），按顺序记录系统/用户/模型/工具的交互。
    结构示例：
    [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "...", "tool_calls": [...]},
        {"role": "tool", "name": "read_file", "content": "..."},
    ]
    """

    messages: List[Dict[str, Any]]


def _build_model_name() -> str:
    """根据环境变量构建模型标识字符串。

    约定：`MODEL_NAME` 保存服务商原始模型 id；`MODEL_PROVIDER` 只用于服务商特定行为判断。
    """

    return os.getenv("MODEL_NAME", "gpt-4o-mini").strip() or "gpt-4o-mini"


def _build_api_kwargs() -> Dict[str, str]:
    kwargs: Dict[str, str] = {}
    api_key = os.getenv("API_KEY", "").strip()
    if api_key:
        kwargs["api_key"] = api_key

    api_base = os.getenv("API_BASE", "").strip()
    if api_base:
        kwargs["api_base"] = api_base
    return kwargs


def _normalize_reasoning_effort() -> str:
    effort = os.getenv("REASONING_EFFORT", "off").strip().lower()
    aliases = {
        "": "off",
        "none": "off",
        "disabled": "off",
        "false": "off",
        "0": "off",
        "minimal": "low",
        "normal": "medium",
        "xhigh": "max",
    }
    effort = aliases.get(effort, effort)
    if effort in {"off", "low", "medium", "high", "max"}:
        return effort
    return "off"


def _build_completion_kwargs(model_name: str) -> Dict[str, Any]:
    reasoning_effort = _normalize_reasoning_effort()
    provider = os.getenv("MODEL_PROVIDER", "").strip().lower()
    model = model_name.lower()
    litellm_model = (
        f"deepseek/{model_name}"
        if "/" not in model_name and (provider == "deepseek" or model.startswith("deepseek-"))
        else model_name
    )
    kwargs: Dict[str, Any] = {"model": litellm_model, **_build_api_kwargs()}
    if model.startswith("deepseek/") or model.startswith("deepseek-") or (
        provider == "deepseek" and "/" not in model
    ):
        kwargs["extra_body"] = {
            "thinking": {"type": "disabled" if reasoning_effort == "off" else "enabled"}
        }
        if reasoning_effort != "off":
            kwargs["reasoning_effort"] = "max" if reasoning_effort == "max" else "high"
            return kwargs

    kwargs["temperature"] = 0
    if reasoning_effort != "off":
        kwargs["reasoning_effort"] = reasoning_effort
    return kwargs


def call_model(state: AgentState, registry: ToolRegistry) -> AgentState:
    """调用 LLM 模型节点并将模型响应附加到消息列表中。

    输入：当前 AgentState（包含已有消息）。
    行为：使用 `litellm.completion` 调用模型；将返回的模型消息追加到消息列表并返回新的状态。

    注意：completion 中会传入 `tools=registry.schemas()`，让模型能够以结构化方式选择工具调用（若需要）。
    """

    configure_litellm_environment()
    from litellm import completion

    model_name = _build_model_name()
    messages = state["messages"]

    # 检查是否需要压缩
    from context_manager.compaction import should_compact, compact_messages
    if should_compact(messages, threshold_tokens=256000):
        import asyncio
        from context_manager.summarizer import generate_summary

        print("⚙️  上下文接近 256k 限制，正在压缩前 80% 的对话...")

        # 压缩消息
        compacted_messages, summary_text = compact_messages(messages, keep_ratio=0.2)

        # 生成摘要
        summary = asyncio.run(generate_summary(summary_text))

        # 替换占位符
        for msg in compacted_messages:
            if msg.get("content") == "[SUMMARY_PLACEHOLDER]":
                msg["content"] = f"## 早期对话摘要\n\n{summary}"
                break

        messages = compacted_messages
        print(f"✓ 已压缩，估算节省 {len(summary_text) // 4 - len(summary) // 4} tokens")

    # 调用 litellm completion；传入当前的对话消息和工具定义
    response = completion(
        **_build_completion_kwargs(model_name),
        messages=messages,
        tools=registry.schemas(),
        tool_choice="auto",
    )

    # 提取模型返回的消息结构（忽略 None 字段）并追加到消息列表中
    message = response.choices[0].message.model_dump(exclude_none=True) # type: ignore
    return {"messages": messages + [message]}


def should_continue(state: AgentState) -> str:
    """根据模型的最新消息决定下一步分支：

    - 如果模型在最新消息中发起了 `tool_calls`，返回 "tools"（进入工具执行节点）
    - 否则返回 "end"（状态机结束）
    """

    last = state["messages"][-1]
    if last.get("tool_calls"):
        return "tools"
    return "end"


def call_tools(state: AgentState, runner: ToolRunner) -> AgentState:
    """执行模型请求的所有工具调用，并把工具输出作为工具消息追加到对话中。

    实现细节：
    - 解析最新模型消息中的 `tool_calls` 列表
    - 对每个调用使用 `ToolRunner.execute` 执行
    - 将每次工具执行的返回结果作为一条 role 为 `tool` 的消息追加，
      并保留 `tool_call_id` 以便模型能将工具输出与先前请求关联
    """

    last = state["messages"][-1]
    # 复制原消息列表，后面在此列表上追加工具消息
    new_messages = list(state["messages"])

    # 遍历模型发起的所有工具调用（如果没有则不会进入循环）
    for tool_call in last.get("tool_calls", []):
        invocation = ToolRouter.build_tool_invocation(tool_call)
        # runner.execute 的返回值为 ToolResult(ok, content, metadata)
        result = runner.execute(name=invocation.name, arguments=invocation.arguments)
        ok = result.ok
        content = result.content
        if not ok:
            # 如果工具执行失败，使用 [ERROR] 前缀标记，模型会看到这个文本并可据此调整
            content = f"[ERROR] {content}"

        # 将工具的输出作为一条工具消息追加到对话流中
        new_messages.append(
            {
                "role": "tool",
                "tool_call_id": invocation.call_id,
                "name": invocation.name,
                "content": content,
            }
        )

    return {"messages": new_messages}


def build_graph(registry: ToolRegistry, runner: ToolRunner):
    """构建并编译状态机图（StateGraph）。

    - model 节点：调用模型（`call_model`）
    - tools 节点：执行工具（`call_tools`）
    - 控制流：START -> model -> (如果需要工具) tools -> model -> ... -> END
    """

    graph = StateGraph(AgentState)
    graph.add_node("model", lambda state: call_model(state, registry))
    graph.add_node("tools", lambda state: call_tools(state, runner))
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
    """主入口：加载环境、校验 API Key、构建状态机并在终端中启动交互循环。

    交互逻辑：
    - 每轮读取用户输入并追加到 `messages`
    - 调用编译后的状态机 `app.invoke({'messages': messages})`
    - 状态机会在必要时调用模型与工具，返回更新后的消息列表
    - 从消息列表中找到最新可显示的模型文本并打印
    """

    # 从 .env 文件加载环境变量（如果存在）
    load_dotenv()

    if not os.getenv("API_KEY"):
        raise RuntimeError("缺少 API_KEY，请先在 .env 中配置")

    # 构建状态机应用
    project_root = Path(__file__).parent.resolve()
    registry = build_default_registry()
    runner = ToolRunner(registry, create_tool_context(project_root))
    app = build_graph(registry, runner)

    # 使用提示词模块构建系统提示
    config = SystemPromptConfig(personality="default", include_tools=True)
    builder = PromptBuilder()
    system_prompt = builder.build(config)

    # 初始化消息队列，首条为 system
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    print("Codex-mini 终端版已启动，输入 quit 退出。")
    while True:
        # 读取用户输入（在终端中）并去除首尾空白
        user_input = input("\n你: ").strip()
        # 支持常见退出命令
        if user_input.lower() in {"quit", "exit", "q"}:
            print("已退出。")
            break
        if not user_input:
            # 空输入则跳过本轮
            continue

        # 将用户消息追加到对话中并触发状态机
        messages.append({"role": "user", "content": user_input})
        result = app.invoke({"messages": messages})
        # 状态机返回的结果包含更新后的消息列表
        messages = result["messages"]

        # 从后向前寻找最近的 assistant 消息并显示其内容（如果有）
        final_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                final_text = msg["content"]
                break

        # 若没有可显示的 assistant 文本，则提示用户查看工具调用结果
        print(f"\nAgent: {final_text or '(模型未返回文本，可查看工具调用结果)'}")


if __name__ == "__main__":
    main()

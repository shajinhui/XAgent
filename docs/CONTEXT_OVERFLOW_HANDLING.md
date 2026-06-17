# 上下文溢出处理机制对比

本文档对比 **OpenAI Codex** 和 **Codex-mini** 在单轮对话上下文超过限制时的处理策略。

---

## 问题场景

当单轮对话的上下文（system prompt + 历史消息 + 当前输入 + 工具输出）超过模型的上下文窗口限制时，需要采取措施避免 API 请求失败。

**常见触发场景**:
- 长期对话累积大量历史
- 单次工具输出过大（如读取大文件）
- 多个工具连续调用产生大量输出
- 项目文档 (AGENTS.md) 过长

---

## OpenAI Codex (Rust) 策略

### 核心机制：**自动压缩 (Auto-Compaction)**

**位置**: `codex-rs/core/src/compact.rs`, `compact_remote.rs`

### 1️⃣ **检测溢出**

```rust
Err(CodexErr::ContextWindowExceeded) => {
    // API 返回上下文窗口超限错误
}
```

**触发时机**:
- 模型 API 返回 `ContextWindowExceeded` 错误
- 本地 token 估算超过 `model_context_window`

### 2️⃣ **渐进式删除最旧历史**

**策略**: 逐个删除最旧的消息，直到上下文可以容纳

```rust
if turn_input_len > 1 {
    error!(
        "Context window exceeded while compacting; removing oldest history item. Error: {e}"
    );
    history.remove_first_item();  // 删除最旧的一条消息
    retries = 0;
    continue;  // 重试 API 调用
}
```

**remove_first_item 实现**:

```rust
pub(crate) fn remove_first_item(&mut self) {
    if !self.items.is_empty() {
        // 删除最旧的消息 (index 0)
        let removed = self.items.remove(0);

        // 维护不变式：如果删除的是 FunctionCall，也删除对应的 FunctionCallOutput
        normalize::remove_corresponding_for(&mut self.items, &removed);
    }
}
```

**关键特性**:
- ✅ 保持最近消息完整（利用提示缓存）
- ✅ 维护 Call/Output 配对不变式
- ✅ 增量删除，逐步适应上下文窗口
- ✅ 失败时自动重试

### 3️⃣ **智能工具输出截断**

**在压缩之前先尝试截断工具输出**

```rust
pub(crate) fn trim_function_call_history_to_fit_context_window(
    history: &mut ContextManager,
    turn_context: &TurnContext,
    base_instructions: &BaseInstructions,
) -> (usize, i64) {
    let Some(context_window) = turn_context.model_context_window() else {
        return (0, 0);
    };

    // 倒序遍历历史（从最新到最旧）
    for index in (0..item_count).rev() {
        let estimated_tokens = history.estimate_token_count_with_base_instructions(base_instructions);

        if estimated_tokens <= context_window {
            break;  // 已经可以容纳
        }

        // 尝试重写/截断这个工具输出
        if let Some(rewritten) = rewritten_output_for_context_window(&item) {
            // 用截断版本替换原输出
            history.replace_item(index, rewritten);
            rewritten_outputs += 1;
        }
    }
}
```

**rewritten_output_for_context_window**:

```rust
fn rewritten_output_for_context_window(item: &ResponseItem) -> Option<ResponseItem> {
    match item {
        ResponseItem::FunctionCallOutput { output, .. } => {
            Some(ResponseItem::FunctionCallOutput {
                output: truncate_to_message(output),  // 截断为固定消息
                // ...
            })
        }
        _ => None,
    }
}

const CONTEXT_WINDOW_TRUNCATED_OUTPUT_MESSAGE: &str =
    "Output exceeded the available model context and was truncated";
```

### 4️⃣ **远程压缩 (Remote Compaction)**

**当支持时，使用服务端压缩 API**

```rust
pub(crate) fn should_use_remote_compact_task(provider: &ModelProviderInfo) -> bool {
    provider.supports_remote_compaction()  // 部分模型提供商支持
}
```

**工作流程**:
1. 调用服务端 `/compact` API
2. 服务端使用小模型生成历史摘要
3. 用摘要替换完整历史
4. 返回压缩后的上下文

### 5️⃣ **手动压缩命令**

**用户主动触发**:
```bash
/compact  # 手动压缩当前会话历史
```

**压缩提示词** (`SUMMARIZATION_PROMPT`):

```
Summarize the conversation history above in a way that preserves the essential
context needed to continue the conversation. Focus on:
- Key decisions and their rationale
- Current task state and blockers
- Important code changes and their purpose
- User preferences expressed during the conversation
```

---

## Codex-mini (Python) 策略

### 核心机制：**CLI 阈值压缩第一版** ⚠️

**当前状态**: Codex-mini 已在 CLI `agent_loop.py` 中加入基于粗略 token 估算的自动压缩第一版；WebSocket 主路径、API 报错后的重试恢复、工具输出智能截断还没有产品化。

### 现有行为

**CLI 路径接近阈值时**:

```python
# agent_loop.py: call_model()
messages = state["messages"]
if should_compact(messages, threshold_tokens=256000):
    compacted_messages, summary_text = compact_messages(messages, keep_ratio=0.2)
    summary = asyncio.run(generate_summary(summary_text))
    messages = compacted_messages
```

**结果**:
- ✅ CLI 长对话会先压缩早期 80% 消息，再保留最近上下文
- ⚠️ 压缩触发依赖粗略估算，不是模型真实 token 计数
- ⚠️ 还没有在 WebSocket runtime 中形成完整自动恢复链路
- ⚠️ 如果 API 已经返回 context window exceeded，当前还缺少捕获后自动重试

### 潜在改进方案

#### 方案 1: **简单截断策略**

```python
def truncate_if_needed(messages: List[Dict], max_tokens: int) -> List[Dict]:
    """如果超限，删除最旧的消息"""
    while estimate_tokens(messages) > max_tokens:
        if len(messages) <= 2:  # 保留 system + 最新一条
            break
        # 删除第二条（system 之后的最旧消息）
        removed = messages.pop(1)
        # 如果删除了 tool call，也删除对应 tool result
        if removed.get("tool_calls"):
            remove_corresponding_tool_results(messages, removed)
    return messages
```

#### 方案 2: **工具输出截断**

```python
def truncate_tool_results(messages: List[Dict], max_output_tokens: int = 5000):
    """截断过长的工具输出"""
    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if estimate_tokens(content) > max_output_tokens:
                msg["content"] = truncate_text(
                    content,
                    max_output_tokens * 4,  # 字符数 ≈ token * 4
                    notice="\n...[output truncated due to context limit]"
                )
```

#### 方案 3: **Memory 摘要注入** (已有基础)

```python
def handle_context_overflow(context_manager: ContextManager, session_id: str):
    """上下文溢出时生成摘要并注入"""

    # 1. 生成当前会话摘要
    events = load_transcript_events(session_id)
    summary = summarize_session(events, use_model=True)

    # 2. 保存摘要
    memory_store.save_session_memory(session_id, summary)

    # 3. 清空历史，只保留摘要
    context_manager.replace([
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": f"## 会话历史摘要\n\n{summary}"},
        # 保留最近 N 轮完整对话
    ])
```

---

## 详细对比

| 维度 | OpenAI Codex (Rust) | Codex-mini (Python) |
|------|---------------------|---------------------|
| **自动检测** | ✅ API 错误捕获 + 本地估算 | ❌ 无自动检测 |
| **处理策略** | 渐进式删除最旧消息 | ❌ 无处理（抛出异常） |
| **工具输出** | 智能截断 + 替换为提示消息 | ❌ 无处理 |
| **重试机制** | ✅ 自动重试，逐步删除 | ❌ 无重试 |
| **不变式维护** | ✅ Call/Output 配对保持 | ❌ N/A |
| **远程压缩** | ✅ 支持（部分提供商） | ❌ 不支持 |
| **手动压缩** | ✅ `/compact` 命令 | ❌ 不支持 |
| **用户通知** | ✅ EventMsg::Warning 通知 | ❌ 无通知 |
| **缓存优化** | ✅ 删除最旧保留前缀（缓存友好） | ❌ N/A |

---

## 实际场景示例

### 场景 1: 读取大文件

**Codex 处理流程**:

```
1. 用户: "读取 large_file.json"
2. read_file 工具返回 50,000 tokens 的内容
3. 构建上下文: system (5k) + history (30k) + tool_output (50k) = 85k tokens
4. 模型上下文窗口: 128k tokens ✅ 成功

--- 对话继续，历史累积到 100k ---

5. 用户: "再读取 another_large.json"
6. read_file 返回 40,000 tokens
7. 构建上下文: system (5k) + history (100k) + tool_output (40k) = 145k tokens
8. 超限! 触发 ContextWindowExceeded

9. Codex 自动处理:
   - 尝试截断 tool_output: 40k → 2k (替换为截断消息)
   - 估算: 5k + 100k + 2k = 107k tokens ✅
   - 重试 API 调用成功

10. 模型收到: "Output exceeded the available model context and was truncated"
11. 模型响应: "文件太大，我已看到截断消息。能否分段读取？"
```

**Codex-mini 当前行为**:

```
1-7. 同上
8. 超限! LiteLLM 抛出异常

9. ❌ 会话中断，错误消息显示给用户
10. 用户需要手动清理历史或重启会话
```

### 场景 2: 长期对话累积

**Codex 处理流程**:

```
经过 50 轮对话...

1. 上下文: system (5k) + history (120k) + user_input (1k) = 126k tokens
2. 接近 128k 限制 ⚠️

3. 下一轮对话触发溢出
4. Codex 自动删除最早的 3 轮对话
5. 上下文缩减到: system (5k) + history (100k) + user_input (1k) = 106k ✅
6. 继续对话

7. 再经过 20 轮，又接近限制
8. 用户执行 `/compact` 手动压缩
9. 历史被压缩为 10k token 的摘要
10. 重置为: system (5k) + summary (10k) = 15k tokens
11. 可以继续长时间对话
```

**Codex-mini 当前行为**:

```
经过 50 轮对话...

1-2. 同上
3. 下一轮对话触发溢出
4. ❌ API 调用失败，会话中断

用户需要:
- 选项 A: 手动触发 summarize_session，然后新建会话
- 选项 B: 清理 .codex-mini/sessions/ 重新开始
- 选项 C: 减少输入长度后重试
```

---

## 推荐改进方案

### 为 Codex-mini 添加自动压缩

**实现位置**: `agent_loop.py:call_model()`

```python
def call_model(state: AgentState, registry: ToolRegistry, max_retries: int = 3) -> AgentState:
    """调用 LLM 并自动处理上下文溢出"""

    model_name = _build_model_name()
    messages = state["messages"]

    for retry in range(max_retries):
        try:
            response = completion(
                **_build_completion_kwargs(model_name),
                messages=messages,
                tools=registry.schemas(),
                tool_choice="auto",
            )
            # 成功，返回结果
            message = response.choices[0].message.model_dump(exclude_none=True)
            return {"messages": state["messages"] + [message]}

        except Exception as e:
            error_str = str(e).lower()

            # 检测上下文溢出错误
            if "context" in error_str and ("limit" in error_str or "exceeded" in error_str or "window" in error_str):
                print(f"⚠️  上下文窗口超限，尝试自动压缩... (重试 {retry + 1}/{max_retries})")

                # 策略 1: 截断工具输出
                if _truncate_tool_outputs(messages, max_tokens=5000):
                    print("   → 已截断过长的工具输出")
                    continue

                # 策略 2: 删除最旧的消息
                if len(messages) > 3:  # 保留 system + 至少 2 条
                    removed = messages.pop(1)  # 删除 system 之后最旧的
                    print(f"   → 已删除最旧的消息: {removed.get('role')}")

                    # 维护配对
                    if removed.get("tool_calls"):
                        _remove_orphaned_tool_results(messages, removed)

                    continue

                # 策略 3: 已无法进一步压缩
                print("   ✗ 无法进一步压缩，上下文仍然超限")
                raise

            # 其他错误，直接抛出
            raise

    raise Exception(f"重试 {max_retries} 次后仍然失败")


def _truncate_tool_outputs(messages: List[Dict], max_tokens: int) -> bool:
    """截断过长的工具输出，返回是否有修改"""
    modified = False
    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            estimated = len(content) // 4
            if estimated > max_tokens:
                msg["content"] = truncate_text(
                    content,
                    max_tokens * 4,
                    notice="\n\n...[output truncated due to context limit]"
                )
                modified = True
    return modified


def _remove_orphaned_tool_results(messages: List[Dict], removed_msg: Dict):
    """删除与被移除的 assistant 消息对应的 tool 结果"""
    if not removed_msg.get("tool_calls"):
        return

    tool_call_ids = {tc["id"] for tc in removed_msg["tool_calls"]}

    # 反向遍历删除对应的 tool 消息
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "tool":
            if messages[i].get("tool_call_id") in tool_call_ids:
                messages.pop(i)
```

### 添加手动压缩命令

**实现位置**: `server/processors/request_dispatcher.py`

```python
async def handle_compact_session(ws: WebSocket, session_state: SessionState):
    """处理 /compact 命令"""

    # 1. 生成当前会话摘要
    events = session_state.session_store.load_events(session_state.session_id)
    summary = summarize_session(events, use_model=True)  # 使用小模型

    # 2. 保存摘要到 Memory
    memory_store = MemoryStore(session_state.workspace.project_root / ".codex-mini" / "memory")
    memory_store.save_session_memory(session_state.session_id, summary)

    # 3. 重置上下文历史
    session_state.context_manager.replace([
        {"role": "system", "content": session_state.system_prompt},
        {"role": "system", "content": f"## 会话历史摘要\n\n{summary}"},
    ])

    # 4. 通知前端
    await ws.send_json({
        "type": "session_compacted",
        "session_id": session_state.session_id,
        "summary_tokens": len(summary) // 4,
        "message": "会话历史已压缩为摘要"
    })
```

---

## 总结

### OpenAI Codex

✅ **完善的自动处理机制**:
- 自动检测溢出
- 渐进式删除 + 智能截断
- 自动重试，用户无感知
- 维护消息对完整性
- 支持手动压缩命令

### Codex-mini

⚠️ **已有基础压缩，但仍是第一版**:
- CLI 可在请求前压缩早期对话
- WebSocket runtime 还缺少同等能力
- 还没有 API 超限后的自动重试恢复
- 还没有内置 `/compact` 压缩命令

### 建议

为 Codex-mini 添加：
1. **基础自动处理** (优先级高)
   - 捕获上下文溢出异常
   - 自动截断工具输出
   - 渐进式删除最旧消息
   - 将 CLI 压缩策略迁移到 WebSocket turn runner

2. **手动压缩命令** (优先级中)
   - `/compact` 命令
   - 使用现有的 `summarize_session`
   - 重置上下文为摘要

3. **用户提示** (优先级中)
   - 显示上下文使用率
   - 接近限制时警告
   - 压缩成功后通知

这样可以让 Codex-mini 具备基本的长对话能力，避免频繁中断用户工作流。

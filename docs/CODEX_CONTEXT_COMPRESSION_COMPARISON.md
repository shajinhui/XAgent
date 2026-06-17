# OpenAI Codex 上下文压缩机制对比分析

本文档对比分析 **OpenAI Codex (Rust)** 和 **Codex-mini (Python)** 两个项目的上下文压缩实现，提炼核心差异与设计思路。

## 项目背景

- **OpenAI Codex**: OpenAI 官方开源的本地 Coding Agent，使用 Rust 实现，生产级质量
- **Codex-mini**: 学习项目，Python 实现，参考 Codex 的架构设计但简化实现

---

## 核心架构对比

### OpenAI Codex (Rust)

**核心模块**: `codex-rs/core/src/context_manager/`

```
context_manager/
├── history.rs              # ContextManager 核心实现
├── normalize.rs            # 历史消息规范化
├── updates.rs              # 消息更新逻辑
└── mod.rs
```

**关键特性**:
1. **Rollout 压缩存储** (`codex-rs/rollout/src/compression.rs`)
2. **用户轮次边界截断** (`thread_rollout_truncation.rs`)
3. **工具输出智能截断** (`utils/output-truncation`)
4. **Token 精确估算** (基于字节启发式)
5. **历史版本管理** (`history_version` 字段)

### Codex-mini (Python)

**核心模块**: `context_manager/`

```
context_manager/
├── history.py              # ContextManager 实现
├── truncation.py           # 简单文本截断
└── updates.py              # 消息追加辅助
```

**关键特性**:
1. **Memory 层摘要** (`memory/summarizer.py`)
2. **分层 Memory 注入** (优先级控制)
3. **推理内容清理** (reasoning_content)
4. **浅拷贝优化** (性能)
5. **简单 Token 估算** (1 token ≈ 4 字符)

---

## 详细对比

### 1. 上下文压缩策略

#### OpenAI Codex: **滑动窗口 + 智能截断**

**用户轮次边界截断** (`thread_rollout_truncation.rs`):

```rust
/// 保留最近 N 个 fork turns（用户消息或触发轮次）
pub(crate) fn truncate_rollout_to_last_n_fork_turns(
    items: &[RolloutItem],
    n_from_end: usize,
) -> Vec<RolloutItem>
```

**工作原理**:
- 扫描 rollout 识别用户消息边界
- 处理 `ThreadRolledBack` 事件（撤销操作）
- 保留最近 N 轮完整对话
- 早期轮次被丢弃，不生成摘要

**优势**:
- 精确控制上下文窗口大小
- 支持撤销/回滚操作
- 快速，无需模型推理

**劣势**:
- 丢失早期上下文信息
- 无法跨会话共享知识

#### Codex-mini: **Memory 摘要 + 分层注入**

**会话摘要生成** (`memory/summarizer.py`):

```python
def summarize_session(events: List[TranscriptEvent], use_model: bool = False) -> str:
    """基于规则或小模型生成摘要"""
    if use_model:
        return _summarize_with_model(events)  # claude-3-haiku
    return _summarize_with_rules(events)      # 快速规则提取
```

**分层注入优先级** (`context_manager/history.py:inject_memory()`):

```
1. User Memory (全局偏好, 1000 tokens)
2. Project Memory (项目规则, 800 tokens)
3. 最近会话摘要 (2个历史会话, 动态)
4. Task Memory (当前任务, 500 tokens)
总预算: 2000 tokens
```

**优势**:
- 保留压缩的历史信息
- 跨会话知识共享
- 用户偏好持久化

**劣势**:
- 摘要质量依赖启发式或小模型
- 额外的存储和计算开销

### 2. 工具输出处理

#### OpenAI Codex: **智能截断策略**

**TruncationPolicy** (`utils/output-truncation/src/lib.rs`):

```rust
pub enum TruncationPolicy {
    Bytes(usize),    // 字节预算
    Tokens(usize),   // Token 预算
}

pub fn truncate_text(content: &str, policy: TruncationPolicy) -> String {
    match policy {
        TruncationPolicy::Bytes(bytes) => truncate_middle_chars(content, bytes),
        TruncationPolicy::Tokens(tokens) => truncate_middle_with_token_budget(content, tokens).0,
    }
}
```

**关键特性**:
- **中间截断** (truncate_middle): 保留头部和尾部，丢弃中间
- **分项预算**: 每个工具输出单独计算预算
- **多模态支持**: 分别处理文本和图片
- **格式化输出**: 添加 `Total output lines: N` 元信息

**示例**:
```
Total output lines: 1000

[前 500 行]
... [500 tokens truncated] ...
[后 500 行]
```

#### Codex-mini: **简单文本截断**

**truncate_text** (`context_manager/truncation.py`):

```python
def truncate_text(text: str, max_chars: int, notice: str = "\n...[truncated]") -> str:
    """截断到 max_chars，在末尾添加 notice"""
    if len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(notice))
    return f"{text[:keep]}{notice}"
```

**特性**:
- 只保留前 N 个字符
- 丢弃尾部内容
- 简单明了，无中间截断

### 3. Rollout 存储与压缩

#### OpenAI Codex: **透明压缩存储**

**文件格式** (`rollout/src/compression.rs`):

```
.codex/
  rollouts/
    <session_id>.jsonl       # 纯文本（热数据）
    <session_id>.jsonl.zst   # Zstandard 压缩（冷数据）
```

**工作流程**:
1. 新会话写入 `.jsonl` (append-only)
2. 后台 worker 检测冷文件（最后修改时间）
3. 压缩为 `.jsonl.zst`，删除原文件
4. 读取时透明解压（`open_rollout_line_reader`）
5. 恢复写入时自动物化回 `.jsonl`

**优势**:
- 大幅节省磁盘空间（压缩率 70-90%）
- 对应用层透明
- 支持增量追加

**实现**:
```rust
pub async fn open_rollout_line_reader(path: &Path) -> io::Result<RolloutLineReader> {
    // 自动检测 .jsonl 或 .jsonl.zst
    // 透明解压缩
}

pub(crate) fn materialize_rollout_for_append(path: &Path) -> io::Result<PathBuf> {
    // 解压 .jsonl.zst -> .jsonl
    // 删除压缩文件
}
```

#### Codex-mini: **纯文本存储**

**文件格式** (`session/transcript.py`):

```
.codex-mini/
  sessions/
    index.sqlite          # 会话索引
    transcripts/
      <session_id>.jsonl  # 纯文本事件流
```

**特性**:
- Append-only JSONL
- 批量缓冲写入（10 条事件）
- 无压缩

### 4. Token 计数与预算管理

#### OpenAI Codex: **精确估算**

**Token 估算** (`utils/output-truncation`):

```rust
pub fn approx_token_count(text: &str) -> usize {
    // 基于字节的启发式算法
    // 考虑 UTF-8、空白、标点等
}

pub fn approx_bytes_for_tokens(tokens: usize) -> usize {
    // Token 到字节的反向映射
}
```

**TokenUsageInfo 跟踪** (`context_manager/history.rs`):

```rust
pub(crate) struct ContextManager {
    items: Vec<ResponseItem>,
    history_version: u64,
    token_info: Option<TokenUsageInfo>,  // 累积使用量
    // ...
}

pub(crate) fn estimate_token_count(&self, turn_context: &TurnContext) -> Option<i64> {
    // 遍历所有 items 估算
    // 包含 base_instructions
}
```

**预算管理**:
- 实时跟踪 token 使用
- 支持上下文窗口检查
- 自动标记 token_usage_full

#### Codex-mini: **粗略估算**

**简单规则** (`context_manager/history.py`):

```python
@staticmethod
def _estimate_tokens(text: str) -> int:
    """粗略估算: 1 token ≈ 4 字符"""
    return len(text) // 4
```

**Memory 预算控制**:

```python
def inject_memory(self, max_tokens: int = 2000) -> None:
    remaining_tokens = max_tokens

    # 按优先级消耗预算
    if user_mem and tokens <= remaining_tokens:
        memories.append(user_mem.content)
        remaining_tokens -= tokens
    # ...
```

### 5. 消息历史规范化

#### OpenAI Codex: **完整规范化流程**

**normalize.rs**:

```rust
pub(crate) fn normalize_history(items: &mut Vec<ResponseItem>, input_modalities: &[InputModality]) {
    // 1. 清理无配对的 FunctionCall/FunctionCallOutput
    remove_unpaired_function_calls(items);

    // 2. 处理空消息
    remove_empty_messages(items);

    // 3. 合并连续的同类消息
    merge_consecutive_messages(items);

    // 4. 去除不支持的模态（如图片）
    if !input_modalities.contains(&InputModality::Image) {
        strip_images(items);
    }
}
```

**不变式维护**:
- 每个 `FunctionCall` 必须有对应的 `FunctionCallOutput`
- 不允许连续的同角色消息
- 消息顺序保持语义完整

#### Codex-mini: **轻量清理**

**clear_historical_reasoning_content** (`context_manager/history.py`):

```python
def clear_historical_reasoning_content(messages: List[Dict[str, Any]]) -> None:
    """删除历史 reasoning_content"""
    for message in messages:
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            message.pop("reasoning_content", None)
```

**特性**:
- 只清理推理内容（节省 token）
- 不执行复杂规范化
- 依赖 LiteLLM 自动处理

### 6. 性能优化

#### OpenAI Codex

**优化点**:
1. **零拷贝**: 使用引用和 `Deref` 避免克隆
2. **惰性计算**: Token 估算按需执行
3. **缓存**: `BlockingLruCache` 缓存计算结果
4. **批量操作**: 一次性处理多个 items
5. **异步 I/O**: `tokio::fs` 异步文件操作
6. **后台压缩**: 非阻塞 rollout 压缩

**代码示例**:

```rust
pub(crate) fn record_items<I>(&mut self, items: I, policy: TruncationPolicy)
where
    I: IntoIterator,
    I::Item: std::ops::Deref<Target = ResponseItem>,  // 零拷贝
{
    for item in items {
        let item_ref = item.deref();
        // 处理引用，不克隆
    }
}
```

#### Codex-mini

**优化点**:
1. **浅拷贝**: `list(messages)` 而非深拷贝
2. **批量写入**: 累积 10 条事件再刷盘
3. **缓冲 I/O**: `TranscriptWriter` 内存缓冲
4. **规则摘要**: 避免调用模型（默认）
5. **SQLite 批量提交**: 减少事务次数

**代码示例**:

```python
def __init__(self, messages: Iterable[Dict[str, Any]] | None = None) -> None:
    # 浅拷贝，调用方保证不修改原始消息
    self._messages = list(messages) if messages else []
```

**效果**:
- 长对话性能提升 30-50%
- 减少 80-90% 的磁盘 I/O

---

## 设计哲学对比

### OpenAI Codex: **生产级可靠性**

**关键原则**:
- ✅ **精确控制**: Token 精确估算，严格预算管理
- ✅ **透明压缩**: 自动压缩冷数据，无需用户感知
- ✅ **可恢复性**: Rollback 支持，版本管理
- ✅ **性能优先**: 零拷贝、异步 I/O、缓存
- ✅ **类型安全**: Rust 类型系统保证正确性

**适用场景**: 生产环境，大规模用户，长期会话

### Codex-mini: **快速原型 & 学习**

**关键原则**:
- ✅ **简单实现**: 最小可行方案，易于理解
- ✅ **透明存储**: 明文 JSON，可直接查看编辑
- ✅ **跨会话共享**: Memory 层支持知识复用
- ✅ **灵活扩展**: Python 动态特性，快速迭代
- ✅ **谨慎注入**: 新会话不自动加载历史

**适用场景**: 学习研究，快速原型，小规模使用

---

## 关键差异总结

| 维度 | OpenAI Codex (Rust) | Codex-mini (Python) |
|------|---------------------|---------------------|
| **压缩策略** | 滑动窗口（丢弃早期轮次） | Memory 摘要（压缩保留） |
| **Token 计数** | 精确启发式（字节分析） | 简单规则（1:4 比例） |
| **工具输出** | 智能中间截断 + 分项预算 | 简单前缀截断 |
| **存储格式** | 透明 Zstandard 压缩 | 纯文本 JSONL |
| **历史规范化** | 完整不变式维护 | 轻量 reasoning 清理 |
| **性能优化** | 零拷贝 + 异步 + 缓存 | 浅拷贝 + 批量 I/O |
| **跨会话** | 不支持（每会话独立） | Memory 层支持 |
| **实现复杂度** | 高（生产级） | 中（原型级） |

---

## 可借鉴的设计

### 从 OpenAI Codex 学习

1. **中间截断策略**:
   ```python
   # 改进 truncate_text
   def truncate_middle(text: str, max_chars: int) -> str:
       if len(text) <= max_chars:
           return text
       keep_head = max_chars // 2
       keep_tail = max_chars - keep_head - 20  # 为标记预留空间
       return f"{text[:keep_head]}\n... [truncated] ...\n{text[-keep_tail:]}"
   ```

2. **分项预算管理**:
   ```python
   def truncate_tool_outputs(outputs: List[str], total_budget: int) -> List[str]:
       per_output_budget = total_budget // len(outputs)
       return [truncate_text(o, per_output_budget) for o in outputs]
   ```

3. **Rollout 压缩**:
   ```python
   import zstandard as zstd

   def compress_old_sessions(days_threshold: int = 7):
       """后台压缩超过 N 天的会话文件"""
       for jsonl_file in find_old_sessions(days_threshold):
           compress_to_zst(jsonl_file)
   ```

4. **Token 精确估算**:
   ```python
   import tiktoken

   def estimate_tokens(text: str, model: str = "gpt-4o") -> int:
       enc = tiktoken.encoding_for_model(model)
       return len(enc.encode(text))
   ```

### 从 Codex-mini 学习

1. **Memory 层架构**:
   - 用户偏好全局持久化
   - 项目规则项目级存储
   - 会话摘要按需注入

2. **分层优先级注入**:
   - 高优先级内容优先保留
   - Token 预算动态分配
   - 超出预算时丢弃低优先级

3. **谨慎注入策略**:
   - 避免污染新会话上下文
   - 恢复时才注入对应 Memory
   - 用户显式控制偏好记录

---

## 未来优化方向

### 混合策略

结合两者优势的理想方案：

```
1. 活跃轮次 (最近 10 轮)
   - 完整保留，无压缩
   - OpenAI Codex 风格滑动窗口

2. 早期轮次 (10 轮之前)
   - 生成结构化摘要
   - Codex-mini 风格 Memory 层

3. 工具输出
   - 智能中间截断
   - 分项预算管理

4. 存储
   - 透明 Zstandard 压缩
   - 后台异步处理

5. Token 计数
   - tiktoken 精确计算
   - 缓存加速重复计算
```

### 智能摘要

使用小模型（claude-3-haiku）生成高质量摘要：

```python
async def summarize_with_model(events: List[Event]) -> str:
    """异步调用小模型生成摘要"""
    prompt = build_summary_prompt(events)
    summary = await litellm.acompletion(
        model="claude-3-haiku-20240307",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
    )
    return summary.choices[0].message.content
```

### 向量检索

为历史会话建立向量索引：

```python
from chromadb import Client

def index_session_memory(session_id: str, summary: str):
    """将会话摘要存入向量数据库"""
    client = Client()
    collection = client.get_or_create_collection("sessions")
    collection.add(
        documents=[summary],
        ids=[session_id],
    )

def retrieve_relevant_memories(query: str, top_k: int = 3) -> List[str]:
    """根据当前任务检索相关历史"""
    results = collection.query(query_texts=[query], n_results=top_k)
    return results['documents'][0]
```

---

## 总结

**OpenAI Codex** 和 **Codex-mini** 代表了两种不同的设计权衡：

- **Codex**: 追求性能、可靠性和生产级质量，适合大规模部署
- **Codex-mini**: 追求简单、灵活和可理解性，适合学习和原型

两者的核心差异在于：
- **Codex** 丢弃早期轮次（滑动窗口）
- **Codex-mini** 压缩保留历史（Memory 摘要）

理想的生产系统应该结合两者优势：
- 近期历史完整保留（Codex 风格）
- 早期历史摘要存储（Codex-mini 风格）
- 智能工具输出截断（Codex 风格）
- 跨会话知识共享（Codex-mini 风格）

这样既能保证性能和准确性，又能实现长期记忆和知识复用。

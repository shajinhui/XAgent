# 上下文压缩机制详解

本文档详细说明 Codex-mini 项目中的上下文压缩（Context Compression）策略与实现。

## 概述

上下文压缩是 Agent 系统在长对话场景下管理有限 token 预算的核心机制。当对话历史增长、工具调用堆积时，需要通过压缩策略避免超出模型的上下文窗口限制，同时尽可能保留对任务推理有价值的信息。

## 核心策略

Codex-mini 采用**多层次上下文管理**策略，而非单一的滑动窗口或简单截断：

### 1. Memory 层（会话摘要）

**位置**: `memory/summarizer.py`, `memory/store.py`

**机制**:
- 将历史 transcript 事件压缩为结构化摘要（Session Memory）
- 支持基于规则的快速摘要（默认）和基于小模型的智能摘要（可选）
- 摘要内容包括：用户意图、工具使用统计、修改文件列表、执行命令列表

**实现**:

```python
def summarize_session(events: List[TranscriptEvent], use_model: bool = False) -> str:
    if use_model:
        return _summarize_with_model(events)  # 使用 claude-3-haiku 生成摘要
    return _summarize_with_rules(events)      # 基于规则快速提取
```

**规则摘要提取**:
- 提取首条用户消息作为意图（截断到 200 字符）
- 统计各工具调用次数
- 收集所有修改的文件路径（最多 20 个）
- 收集所有执行的命令（最多 10 条）

**模型摘要生成**:
- 使用 claude-3-haiku-20240307 低成本模型
- 只取前 50 个事件，每个事件内容截断到 200 字符
- 生成 3-5 个要点的结构化摘要
- 超时 10 秒，失败时回退到规则方法

### 2. 历史消息清理

**位置**: `context_manager/history.py`

**机制**:
- 清理历史消息中的 `reasoning_content` 字段（推理过程）
- 仅保留带有 `tool_calls` 的 assistant 消息的推理内容
- 避免将大量内部推理过程回传给模型

**实现**:

```python
def clear_historical_reasoning_content(messages: List[Dict[str, Any]]) -> None:
    """删除不应回传的 reasoning_content 字段"""
    for message in messages:
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            message.pop("reasoning_content", None)
```

**效果**: 减少 30-50% 的历史消息 token 开销（尤其是使用推理模型时）

### 3. Memory 注入与分层加载

**位置**: `context_manager/history.py:inject_memory()`

**机制**:
- 在新会话或恢复会话时，按优先级分层注入 Memory
- 设置 token 预算（默认 2000 tokens），超出则丢弃低优先级 Memory
- 优先级排序：用户偏好 > 项目规则 > 最近会话上下文 > 当前任务

**实现**:

```python
def inject_memory(
    self,
    *,
    session_id: str | None = None,
    project_root: Path | None = None,
    max_tokens: int = 2000,
) -> None:
    """按优先级注入 Memory 到上下文（在 system prompt 后）"""
    memories = self._load_memories(session_id, project_root, max_tokens)
    # 在 system prompt 后依次注入
    insert_pos = 1 if self._messages and self._messages[0].get("role") == "system" else 0
    for mem_content in memories:
        self._messages.insert(insert_pos, {"role": "system", "content": mem_content})
        insert_pos += 1
```

**加载优先级**:

1. **User Memory** (全局，最高优先级，最多 1000 tokens)
   - 路径: `~/.codex-mini/memory/users/preferences.json`
   - 内容: 用户显式记录的偏好（通过 `remember_preference` 写入）

2. **Project Memory** (项目规则，最多 800 tokens)
   - 路径: `<project_root>/.codex-mini/memory/projects/<project_name>.json`
   - 内容: 项目级规则与约束

3. **最近会话上下文** (跨会话上下文，最多 2 个历史会话)
   - 路径: `<project_root>/.codex-mini/memory/sessions/<session_id>.json`
   - 内容: 最近 3 个会话的摘要（跳过当前会话）
   - 限制: 每个摘要 token 数不超过剩余预算

4. **Task Memory** (当前任务状态，最多 500 tokens)
   - 路径: `<project_root>/.codex-mini/memory/tasks/<task_id>.json`
   - 内容: 当前任务的目标、尝试方法、阻塞点、下一步计划

**Token 估算**: 简单规则 `len(text) // 4` （1 token ≈ 4 字符）

### 4. 文本截断

**位置**: `context_manager/truncation.py`

**机制**:
- 对单条过长内容（如文件读取结果、命令输出）进行截断
- 在截断位置添加明确的提示文本

**实现**:

```python
def truncate_text(text: str, max_chars: int, notice: str = "\n...[truncated]") -> str:
    """截断文本到 max_chars 长度（含 notice），并在末尾追加 notice"""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(notice))
    return f"{text[:keep]}{notice}"
```

**使用场景**: 工具输出、文件内容、长错误消息等

### 5. 会话恢复时的 Memory 注入

**位置**: `session/recovery.py`

**机制**:
- 从 transcript 重建消息历史时，自动注入该会话的 Session Memory
- 摘要作为独立的 system 消息插入（在主 system prompt 之后）

**实现**:

```python
def recover_messages(
    system_prompt: str,
    events: Iterable[TranscriptEvent],
    *,
    session_id: str | None = None,
    project_root: Path | None = None,
) -> List[Dict[str, Any]]:
    messages = [{"role": "system", "content": system_prompt}]

    # 注入 Session Memory
    if session_id and project_root:
        memory_summary = _load_session_memory(session_id, project_root)
        if memory_summary:
            messages.append({
                "role": "system",
                "content": f"## 会话恢复摘要\n\n{memory_summary}"
            })

    # 重建后续消息...
    return messages
```

### 6. 浅拷贝优化

**位置**: `context_manager/history.py`

**机制**:
- 消息历史管理使用浅拷贝而非深拷贝
- 减少长对话场景下的内存拷贝开销

**实现**:

```python
def __init__(self, messages: Iterable[Dict[str, Any]] | None = None) -> None:
    # 使用浅拷贝减少开销，调用方需确保不修改原始 message
    self._messages: List[Dict[str, Any]] = list(messages) if messages else []
```

**效果**: 长对话场景下性能提升 30-50%

## 完整流程示例

### 场景 1：新会话启动

```
1. 创建 ContextManager，加载 system prompt
2. 调用 inject_memory()
   - 加载 User Memory (用户偏好)
   - 加载 Project Memory (项目规则)
   - 加载最近 2 个历史会话摘要
   - 总 token 预算：2000
3. 用户输入追加为 user 消息
4. 模型生成 assistant 消息（可能包含 tool_calls）
5. 工具执行，结果追加为 tool 消息
6. 调用 clear_historical_reasoning_content() 清理推理内容
7. 循环直到模型输出最终答案
```

### 场景 2：恢复历史会话

```
1. 从 SessionStore 加载 transcript 事件
2. 调用 recover_messages()
   - 插入 system prompt
   - 加载并注入该会话的 Session Memory 摘要
   - 遍历 transcript 重建 user/assistant/tool 消息
   - 过滤掉历史 reasoning_content
3. 创建 ContextManager，加载重建的消息
4. 用户继续对话...
```

### 场景 3：会话结束时保存摘要

```
1. 前端发送 summarize_session 控制事件
2. 后端调用 summarize_session(events)
   - 基于规则提取或使用小模型生成
3. 保存到 MemoryStore
   - 路径: .codex-mini/memory/sessions/<session_id>.json
4. 后续新会话可以引用该摘要作为上下文
```

## 未来优化方向

### 1. 智能消息压缩
- 对历史消息按重要性打分
- 保留关键的决策点和失败尝试
- 压缩重复的工具调用结果

### 2. 滑动窗口策略
- 当消息数超过阈值时，保留最近 N 轮完整对话
- 早期消息压缩为摘要注入

### 3. 增量摘要
- 每 K 轮对话自动生成增量摘要
- 避免一次性处理大量历史

### 4. 向量检索增强
- 对历史会话建立向量索引
- 根据当前任务语义检索相关历史片段
- 选择性注入而非按时间顺序

### 5. Token 计数优化
- 使用 tiktoken 精确计算 token 数
- 替代当前的简单字符比例估算

## 性能影响

根据 `PERFORMANCE_OPTIMIZATION.md`，上下文管理相关优化效果：

- **消息历史浅拷贝**: 长对话性能提升 30-50%
- **清理 reasoning_content**: 减少 30-50% 历史消息 token 开销
- **Memory 摘要**: 将完整会话历史压缩到 < 500 tokens

## 相关文件

- `context_manager/history.py` - 核心上下文管理器
- `context_manager/truncation.py` - 文本截断工具
- `memory/summarizer.py` - 会话摘要生成
- `memory/store.py` - Memory 持久化存储
- `session/recovery.py` - 会话恢复逻辑
- `docs/MEMORY_LAYER.md` - Memory 层详细文档
- `docs/PERFORMANCE_OPTIMIZATION.md` - 性能优化记录

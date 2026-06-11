# Memory 层实现文档

## 概述

Memory 层提供 Session Memory 和 Task Memory 的存储与摘要能力，支持会话恢复和任务状态追踪。

## 架构

```
memory/
├── __init__.py          # 包导出
├── models.py            # 数据模型（MemoryEntry, MemoryType）
├── store.py             # 文件存储（JSON）
└── summarizer.py        # 从 transcript 生成摘要
```

## 数据模型

### MemoryType

```python
class MemoryType(str, Enum):
    SESSION = "session"  # 会话摘要
    TASK = "task"        # 任务状态
```

### MemoryEntry

```python
@dataclass(frozen=True)
class MemoryEntry:
    memory_id: str           # session_id 或 task_id
    memory_type: MemoryType  # 类型
    content: str             # 摘要内容（markdown）
    created_at: float        # 创建时间戳
    updated_at: float        # 更新时间戳
    metadata: Dict[str, Any] # 扩展元数据
```

## 存储结构

```
.codex-mini/
  memory/
    sessions/
      <session_id>.json
    tasks/
      <task_id>.json
```

每个 JSON 文件包含完整的 `MemoryEntry` 序列化数据。

## API

### MemoryStore

```python
store = MemoryStore(data_dir)

# 保存会话摘要
entry = store.save_session_memory(session_id, content, metadata)

# 加载会话摘要
entry = store.load_session_memory(session_id)

# 列出所有会话摘要（按更新时间倒序）
entries = store.list_session_memories()

# 删除 memory
deleted = store.delete_memory(MemoryType.SESSION, session_id)
```

### Summarizer

```python
from memory.summarizer import summarize_session, extract_task_state

# 从 transcript 生成会话摘要
summary = summarize_session(events)

# 提取任务状态
state = extract_task_state(events)
# state = {
#     "goal": "...",
#     "attempted_approaches": [],
#     "modified_files": [],
#     "executed_commands": [],
#     "blockers": [],
#     "next_steps": [],
# }
```

## WebSocket 控制事件

### summarize_session

生成当前会话摘要并保存到 memory。

**请求**:
```json
{
  "type": "summarize_session",
  "request_id": "..."
}
```

**响应**:
```json
{
  "type": "session_summarized",
  "session_id": "...",
  "turn_id": "...",
  "request_id": "...",
  "summary": "markdown 格式的摘要",
  "memory_id": "session_id"
}
```

### list_memory

列出当前 workspace 的 memory。

**请求**:
```json
{
  "type": "list_memory",
  "memory_type": "session",  // 或 "task"
  "request_id": "..."
}
```

**响应**:
```json
{
  "type": "memory_list",
  "session_id": "...",
  "memory_type": "session",
  "memories": [
    {
      "memory_id": "...",
      "memory_type": "session",
      "content": "...",
      "created_at": 1234567890.0,
      "updated_at": 1234567890.0,
      "metadata": {}
    }
  ]
}
```

### forget_memory

删除指定 memory。

**请求**:
```json
{
  "type": "forget_memory",
  "memory_type": "session",
  "memory_id": "...",
  "request_id": "..."
}
```

**响应**:
```json
{
  "type": "memory_deleted",
  "session_id": "...",
  "memory_type": "session",
  "memory_id": "...",
  "request_id": "..."
}
```

## 使用场景

### 1. Resume 时加载会话摘要

```python
memory_store = MemoryStore(workspace.project_root / ".codex-mini" / "memory")
entry = memory_store.load_session_memory(session_id)
if entry:
    # 注入到 context
    context_summary = entry.content
```

### 2. 手动触发会话摘要

前端发送 `summarize_session` 控制事件，后端生成摘要并保存。

### 3. 列出历史会话摘要

前端发送 `list_memory` 控制事件，展示历史会话列表。

## 设计决策

1. **不自动注入**: 当前实现不会自动注入到模型上下文，等 ContextManager 成熟后再统一注入。
2. **JSON 存储**: 使用简单的 JSON 文件，不依赖 Chroma 或向量数据库。
3. **手动触发**: 摘要生成由用户或前端主动触发，不自动运行。
4. **可审查**: 所有 memory 都是明文 JSON，用户可直接查看和编辑。

## 后续扩展

- **ContextManager 集成**: 在 `build_messages()` 时自动注入 memory。
- **Task Memory 生成**: 添加 `save_task_memory` 的 WebSocket 事件。
- **User/Project Memory**: 在第六阶段扩展用户偏好和项目规则。
- **Vector Memory**: 可选的语义检索层。

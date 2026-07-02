# Memory 层实现文档

## 概述

Memory 层提供 Session Memory 和 Task Memory 的存储与摘要能力，支持会话恢复和任务状态追踪。
当前实现使用透明 JSON/Markdown 文件，不依赖向量数据库。

## 架构

```
memory/
├── __init__.py          # 包导出
├── models.py            # 数据模型（MemoryEntry, MemoryType）
├── store.py             # 文件存储（JSON）
├── summarizer.py        # 从 transcript 生成摘要
├── indexer.py           # 生成 MEMORY.md / memory_summary.md
├── search.py            # 关键词搜索
└── learner.py           # 自动学习实验模块（当前不接入写入链路）
```

## 数据模型

### MemoryType

```python
class MemoryType(str, Enum):
    SESSION = "session"  # 会话摘要
    TASK = "task"        # 任务状态
    USER = "user"        # 显式用户偏好
    PROJECT = "project"  # 项目规则
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
    MEMORY.md
    memory_summary.md
    sessions/
      <session_id>.json
    tasks/
      <task_id>.json
    users/
      preferences.json
    projects/
      <project_id>.json
```

每个 JSON 文件包含完整的 `MemoryEntry` 序列化数据。
`memory_id` 会经过路径安全校验，禁止 `/`、`\`、空值、控制字符和超长 id；写入采用临时文件 + 原子替换。

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

# 显式记录用户偏好
entry = store.save_user_memory("# 用户偏好\n\n- 喜欢中文解释")
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

摘要器读取当前 transcript 的 `tool` / `arguments` 字段，同时兼容早期测试里的 `tool_name` / `args` 字段。

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

### remember_preference

显式记录用户偏好到用户级 memory。当前不会从普通对话中自动抽取偏好。

**请求**:

```json
{
  "type": "remember_preference",
  "content": "喜欢用中文解释实现细节",
  "request_id": "..."
}
```

**响应**:

```json
{
  "type": "preference_remembered",
  "session_id": "...",
  "request_id": "...",
  "content": "喜欢用中文解释实现细节",
  "memory_id": "preferences"
}
```

### search_memory

按关键词搜索当前 workspace 的 memory 索引和 session/task 明细。

**请求**:

```json
{
  "type": "search_memory",
  "query": "性能",
  "request_id": "..."
}
```

**响应**:

```json
{
  "type": "memory_search_results",
  "session_id": "...",
  "request_id": "...",
  "query": "性能",
  "index_results": "...",
  "detail_results": [
    {
      "memory_id": "...",
      "line": "...",
      "line_number": 1
    }
  ]
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

### 3. 自动保存会话摘要

切换到新会话时，后端会尝试把上一个已持久化会话摘要保存到当前 workspace 的 memory。
该流程只保存 Session Memory，不会自动写入全局 User Memory。

### 4. 列出历史会话摘要

前端发送 `list_memory` 控制事件，展示历史会话列表。

## 设计决策

1. **谨慎注入**: 新会话创建时不自动注入历史 memory；恢复会话时只注入当前 session 的恢复摘要。`ContextManager.inject_memory()` 保留为后续显式开关能力。
2. **JSON 存储**: 使用简单的 JSON 文件，不依赖 Chroma 或向量数据库。
3. **显式偏好**: 用户偏好只通过 `remember_preference` 写入，普通对话不会自动沉淀为长期偏好。
4. **可审查**: 所有 memory 都是明文 JSON，用户可直接查看和编辑。

## 后续扩展

- **ContextManager 集成**: 在 `build_messages()` 时按开关注入 memory。
- **Task Memory 生成**: 添加 `save_task_memory` 的 WebSocket 事件。
- **User/Project Memory**: 补充查看、删除和项目规则编辑入口。
- **Vector Memory**: 可选的语义检索层。

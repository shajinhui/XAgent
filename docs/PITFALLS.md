# 踩坑记录

本文档记录 Codex-mini 开发过程中遇到的典型问题、误判路径、根因和修复方式。目标不是写复盘作文，而是给后续排查留下可执行的线索。

## 2026-05-14：后端启动后 CPU 占用偏高

### 现象

- Electron 启动 Python 后端后，即使用户没有发起模型请求，后端 CPU 也会短时间偏高。
- 直觉上容易怀疑是 `uvicorn`、WebSocket 常驻循环、模型 streaming 或前端重连导致空转。
- 本地 `.codex-mini/sessions/` 已积累大量 transcript 文件，例如 100+ 个 `.jsonl`。

### 关键误判

不要只看 `server/app.py` 中的 `while True` 就判断是空循环。

WebSocket 主循环停在：

```python
raw_packet = await ws.receive_text()
```

这里是 await 阻塞等待消息，空闲时不应该吃 CPU。真正的问题发生在连接建立后前端主动请求历史会话列表。

### 触发链路

启动后链路大致是：

```text
desktop onMounted
  -> runtime.connect()
  -> 后端发送 ready
  -> 前端 handleEvent("ready")
  -> requestSessions()
  -> WebSocket list_sessions
  -> server.list_session_summaries()
  -> SessionStore.list_sessions()
  -> 对每个 session 调 store.load_events(session_id)
  -> 逐个读取 transcripts/*.jsonl
```

旧实现里 `list_session_summaries(store, limit=30)` 表面带了 limit，但实际是：

```text
先取全部 sessions
  -> 逐个读取 transcript
  -> 从 JSONL 里统计 message_count / title / last_message
  -> 过滤空会话
  -> 排序
  -> 最后才截断 limit
```

这意味着即使只有 0 个真实对话，只要有 100 个空 session transcript，也会读 100 个 JSONL 文件。

### 为什么会积累大量空 session

当前 WebSocket 连接建立时会立即创建持久 session：

```text
agent_ws accept
  -> create_websocket_session()
  -> SessionStore.create_session()
  -> 写入 session_started transcript
```

如果用户只是打开客户端、刷新、切 workspace、重连，而没有真正输入消息，就会留下只有 `session_started`、`workspace_opened`、`session_resumed`、`conversation_title`、`runtime_error` 等运行事件的空 session。

这些空 session 对产品侧历史列表没有意义，但旧查询仍然会读取它们的 transcript。

### 根因

根因不是单点 CPU bug，而是两个设计叠加：

- `list_session_summaries()` 用 transcript 内容过滤“是否是真实会话”，导致每次列表查询都要读 JSONL。
- SQLite index 中的 `last_turn_id` 曾被 `system` / `title` 这类运行事件覆盖，导致无法可靠用索引区分真实用户 turn 和运行事件。

### 修复方式

修复方向：列表页先用 SQLite index 缩小候选集，再读取少量 transcript 生成摘要。

已落地的关键点：

- `SessionStore.list_sessions(..., with_turns=True)` 支持只查询有真实用户 turn 的 session。
- SQL 过滤掉 `last_turn_id IS NULL`、空字符串、`system`、`title`。
- `_extract_turn_id()` 不再把 `system` / `title` 当成真实 turn id。
- `list_session_summaries()` 改为 `store.list_sessions(limit=safe_limit, with_turns=True)`，避免扫描全部空 transcript。
- 增加测试保证空 session 不会触发 `load_events()`。

相关文件：

- `session/store.py`
- `server/app.py`
- `tests/test_session_store.py`
- `tests/test_server_events.py`

### 排查命令

查看 session 数量和 transcript 数量：

```bash
find .codex-mini/sessions -type f 2>/dev/null | wc -l
find .codex-mini/sessions -type f -name '*.jsonl' 2>/dev/null | wc -l
du -sh .codex-mini 2>/dev/null || true
```

查看 SQLite 里哪些 `last_turn_id` 占多数：

```bash
.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path

db_path = Path(".codex-mini/sessions/index.sqlite")
if not db_path.exists():
    print("no db")
else:
    con = sqlite3.connect(db_path)
    rows = con.execute(
        """
        SELECT coalesce(last_turn_id, '<null>'), count(*)
        FROM sessions
        GROUP BY coalesce(last_turn_id, '<null>')
        ORDER BY count(*) DESC
        """
    ).fetchall()
    print(rows)
PY
```

查看修复后实际会进入摘要生成的候选数量：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from session import SessionStore
from server.app import list_session_summaries

store = SessionStore(Path("."))
print("indexed_sessions", len(store.list_sessions()))
print("turn_sessions", len(store.list_sessions(with_turns=True)))
print("summaries", len(list_session_summaries(store, limit=30)))
PY
```

### 回归测试

```bash
.venv/bin/python -m unittest tests.test_session_store tests.test_server_events
.venv/bin/python -m unittest discover -s tests
```

### 后续防线

这次修复降低了启动时历史列表扫描成本；随后又补上了更根本的防线：WebSocket 新会话先只存在于内存，直到第一条非空 `user_input` 到达时才创建 SQLite 记录和 `session_started` transcript。

还可以继续优化：

- 前端 `requestSessions()` 做节流，避免 `ready`、`final_answer`、`conversation_title`、`session_deleted` 等事件连续触发列表刷新。
- SQLite index 中可以进一步保存 `message_count`、`last_message`、`preview/title`，让历史列表完全不依赖读取 transcript。
- 对 `.codex-mini/sessions/` 加清理策略：空 session 可定期删除或启动时轻量清理。

### 经验规则

- 列表页不要靠读 append-only 日志做过滤；append-only transcript 是恢复源，不应该是高频索引源。
- `limit` 必须尽早作用在索引查询上，不能“全量读完再 limit”。
- 运行事件的 `turn_id` 不等于用户 turn；`system`、`title` 这类值不能污染 session 的最近真实 turn。
- 后端 CPU 高时，除了看常驻循环，也要看前端启动事件触发了哪些“看似轻量”的查询。

## 2026-05-14：DeepSeek 思考模式 off 不等于真的关闭

### 现象

- 配置里设置 `REASONING_EFFORT=off`，但 DeepSeek 模型仍可能输出或消耗思考模式相关内容。
- 直觉上容易以为“不传 `reasoning_effort` 就是关闭思考”，但 DeepSeek 的默认 thinking 开关是 enabled。

### 根因

DeepSeek 的思考模式有独立开关：

```json
{"thinking": {"type": "enabled"}}
```

关闭时也需要显式传：

```json
{"thinking": {"type": "disabled"}}
```

所以只是不传 `reasoning_effort`，并不能表达“关闭 thinking”。

### 修复方式

- DeepSeek 模型下 `REASONING_EFFORT=off` 会发送 `extra_body={"thinking": {"type": "disabled"}}`。
- DeepSeek 模型下开启思考会发送 `extra_body={"thinking": {"type": "enabled"}}`。
- DeepSeek 兼容映射：`low/medium` -> `high`，`xhigh/max` -> `max`。
- DeepSeek thinking 开启时不再传 `temperature`，避免配置看起来生效但实际被忽略。
- 活跃内存里只保留带 `tool_calls` 的 assistant `reasoning_content`，普通历史 assistant 消息仍会清理。

### 经验规则

- `reasoning_effort` 是强度，不是开关；开关要看服务商是否有独立参数。
- 对支持工具调用的 thinking 模型，带 `tool_calls` 的 assistant `reasoning_content` 可能是后续上下文协议的一部分，不能和普通回复 reasoning 一起无脑清掉。
- 持久 transcript 仍不要保存 `reasoning_content`；如果未来要支持跨进程恢复 DeepSeek 工具调用上下文，需要单独设计安全的模型上下文缓存，而不是把思维链直接写入用户可见历史。

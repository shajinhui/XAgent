# 当前项目架构状况

本文档记录 Codex-mini 当前阶段的项目架构、模块职责、运行链路、已完成能力和仍待收口事项。若本文件与根目录 `AGENTS.md` 的项目状态描述不一致，应同步更新两者。

## 项目定位

Codex-mini 是一个 Python 版 Codex 类本地 Agent 学习项目，目前正处于阶段 2：从单体工具函数升级为可扩展的 Agent 工具运行时，并逐步走向本地桌面客户端 + Python runtime 的形态。

当前状态可以概括为：

- CLI Agent 主流程已经接入新的工具注册与分发机制。
- 工具层已经从 `tools/toolkit.py` 拆分为多个独立工具模块。
- 安全策略、命令白名单、危险命令拦截和熔断器已经有第一版实现。
- `run_command` 已改为通过 macOS 原生沙箱在真实项目 workspace 中执行。
- FastAPI WebSocket 服务已经作为本地事件传输原型接入，但它的最终角色应是桌面客户端/IDE 扩展的 runtime bridge。
- WebSocket runtime contract 已有第一版：schema version、session state、真实 streaming、权限确认重试、session 挂起/阻断/恢复、session 创建/列表/磁盘恢复、基于首条用户提问的模型标题。
- 会话持久化已有第一版：`.codex-mini/sessions/index.sqlite` 作为索引，`transcripts/*.jsonl` 作为 append-only 事件流。
- workspace 后端骨架已有第一版：验证用户选择的工作区目录，并在 `open_workspace` 时重建 session store 与 tool registry。
- `run_command.cwd` 已有第一版：命令可以在 workspace 内部子目录执行，但 cwd 不会扩大 sandbox 写入边界。
- `server/` 已从单体 WebSocket 文件开始拆分：`protocol/` 负责事件协议，`runtime/` 负责模型请求、streaming 和 session 状态，`processors/` 负责业务处理器，`views/` 负责给客户端展示的投影数据。
- 桌面客户端已经进入 Electron/Vue 本地 runtime client 方向，负责聊天、审批、Markdown 渲染、历史会话、工作区打开和会话操作。
- 已建立基础 `unittest` 测试，覆盖安全策略、工具注册表、WebSocket 事件、会话存储和恢复。

## 顶层目录结构

```text
.
├── agent_loop.py
├── main.py
├── tools/
│   ├── __init__.py
│   ├── toolkit.py
│   ├── types.py
│   ├── registry.py
│   ├── core/
│   │   ├── catalog.py
│   │   ├── protocol.py
│   │   ├── registry.py
│   │   ├── router.py
│   │   ├── runner.py
│   │   └── types.py
│   ├── filesystem/
│   │   ├── read_file.py
│   │   ├── write_file.py
│   │   └── edit_file.py
│   ├── search/
│   │   └── grep.py
│   ├── shell/
│   │   └── run_command.py
│   ├── network/
│   │   └── web_fetch.py
│   └── interaction/
│       ├── ask_user.py
│       └── ask_user_spec.py
├── security/
│   ├── __init__.py
│   ├── policy.py
│   └── circuit_breaker.py
├── sandbox/
│   ├── __init__.py
│   └── macos_executor.py
├── server/
│   ├── __init__.py
│   ├── app.py
│   ├── protocol/
│   │   ├── events.py
│   │   └── serialization.py
│   ├── runtime/
│   │   ├── model_config.py
│   │   ├── model_stream.py
│   │   ├── session_state.py
│   │   ├── transcript_events.py
│   │   ├── turn_runner.py
│   │   └── websocket_context.py
│   ├── processors/
│   │   ├── request_dispatcher.py
│   │   └── title_processor.py
│   └── views/
│       └── session_summary.py
├── workspace/
│   ├── __init__.py
│   ├── models.py
│   ├── validator.py
│   └── manager.py
├── session/
│   ├── __init__.py
│   ├── models.py
│   ├── store.py
│   ├── transcript.py
│   └── recovery.py
├── desktop/
│   └── ...
├── tests/
│   ├── test_macos_executor.py
│   ├── test_security_policy.py
│   ├── test_server_events.py
│   ├── test_session_recovery.py
│   ├── test_session_store.py
│   └── test_tool_registry.py
├── docs/
│   └── PROJECT_ARCHITECTURE_STATUS.md
├── README.md
├── Makefile
├── requirements.txt
└── pyproject.toml
```

## 分层架构

```text
用户输入
  |
  v
CLI: agent_loop.py              WebSocket: server/app.py
  |                                      |
  |                         server/protocol + runtime
  |                         processors + views
  |                                      |
  +-------------------+------------------+
                      |
                      v
             LangGraph Agent Loop
                      |
                      v
              LiteLLM completion
                      |
                      v
              ToolRegistry schemas
                      |
                      v
              ToolRegistry execute
                      |
       +--------------+--------------+----------------+
       |              |              |                |
       v              v              v                v
  tools/*.py   security/policy.py   sandbox/macos_executor.py   session/*.py
       |              |              |                |
       +--------------+--------------+----------------+
                      |
                      v
              ToolResult / tool message
                      |
                      v
              模型继续推理或输出最终答案
```

## 核心模块职责

### `agent_loop.py`

CLI 版 Agent 主循环。

主要职责：

- 加载 `.env`。
- 根据 `MODEL_PROVIDER` 和 `MODEL_NAME` 组装 LiteLLM 模型名。
- 构建 LangGraph 状态机。
- 把 `ToolRegistry.schemas()` 暴露给模型。
- 当模型发起 tool call 时，通过 `ToolRegistry.execute()` 执行工具。
- 将工具结果作为 `role=tool` 消息回填给模型。

当前状态：

- 已接入新的 `ToolRegistry`。
- 仍是同步调用模型。
- 还没有真实 token streaming。
- 工具已具备 read-only/mutating/parallel 元信息，但调度目前仍是顺序执行。

### `tools/core/` 与 `tools/registry.py`

工具系统已按 Codex 风格拆成 core 层、兼容门面和领域工具包。

主要职责：

- `tools/core/protocol.py` 定义工具最小协议和函数式工具适配器。
- `tools/core/registry.py` 只负责注册、查找、schema 和 metadata 暴露。
- `tools/core/catalog.py` 负责装配默认内置工具，替代 registry 内部硬编码。
- `tools/core/runner.py` 统一 JSON 参数解析、审批适配、异常包装和 `ToolResult` 归一化。
- `tools/core/router.py` 把模型返回的 tool call 解析为内部 `ToolInvocation`。
- `tools/registry.py` 保留 `ToolRegistry(project_root, session_id)`、`schemas()`、`metadata()`、`execute()` 兼容入口。

当前注册工具：

- `read_file`
- `ask_user`
- `write_file`
- `edit_file`
- `grep`
- `run_command`
- `web_fetch`

当前状态：

- registry、router、runner、catalog 已从旧单文件 registry 拆出。
- 已加入工具元信息：`is_read_only`、`is_mutating`、`supports_parallel`、`requires_approval`。
- 权限错误已带 `metadata`，可表达 `ask/deny`、风险类别、命令和 session suspension 状态。
- WebSocket 层已经消费 `ask/deny/session_suspended/clarification` 等 metadata，并据此推送权限、澄清问题、结果、挂起和恢复相关事件。

### `session/models.py`

会话持久化共享类型。

当前状态：

- `SessionRecord` 描述 session id、创建/更新时间、项目根目录、transcript 路径、标题、最近 turn 和 metadata。
- `TranscriptEvent` 描述 append-only transcript 中的事件 id、session id、类型、时间戳和 payload。
- 这些类型是 `SessionStore`、`TranscriptWriter`、恢复逻辑和 WebSocket 展示摘要之间的稳定数据边界。

### `session/store.py`

SQLite 会话索引和 transcript 访问层。

当前状态：

- 默认数据目录为 `.codex-mini/sessions/`。
- `index.sqlite` 记录 session 元信息，并按 `updated_at` 建索引。
- `transcripts/*.jsonl` 保存每个 session 的 append-only 事件流。
- 支持创建 session、追加事件、读取单个 session、列出 session、加载 transcript。
- 追加事件时会更新 session 的 `updated_at` 和 `last_turn_id`。

注意事项：

- `.codex-mini/sessions/` 是本地运行态数据，不应当作为产品源码提交。
- 当前 session suspension 状态仍主要在 WebSocket 连接内，尚未持久化为可跨进程恢复的策略状态。

### `session/transcript.py`

append-only JSONL transcript 读写器。

当前状态：

- 每条事件写为独立 JSON 行。
- 保留 `0.0` 这类合法时间戳。
- 读取时校验必要字段，坏行会抛出明确的 `ValueError`。
- 使用 `ensure_ascii=False`，便于保留中文会话内容。

### `session/recovery.py`

从 transcript 重建模型上下文。

当前状态：

- 将 `user_message`、`assistant_message`、`tool_call_result` 恢复为 LiteLLM/OpenAI 风格 messages。
- 失败工具结果会以 `[ERROR]` 前缀回填给模型。
- 非模型上下文事件如 `session_started`、`permission_decision`、`conversation_title`、`runtime_error` 会被忽略。
- 不从磁盘恢复 `reasoning_content`；活跃会话内存中会保留带 `tool_calls` 的 assistant `reasoning_content`，用于兼容 DeepSeek 工具调用后的上下文拼接要求。

### `tools/types.py`

工具运行时共享类型。

主要职责：

- 定义 `ToolResult`。
- 定义 `ToolMeta`。
- 定义 `ToolPermissionError`。
- 定义 `ToolExecutionContext`。
- 将项目根目录、session id、安全策略、熔断器和 macOS 沙箱执行器传给各工具。

当前状态：

- 类型层已经能支持当前工具调用。
- 已支持工具元信息和权限 metadata。
- 后续可继续扩展事件 metadata 和调度策略。

### `tools/filesystem/read_file.py`

读取项目内文件。

当前状态：

- 使用 Pydantic 校验参数。
- 通过 `SecurityPolicy.resolve_path()` 限制路径必须在项目根目录内。
- 支持 UTF-8 读取，错误字符替换。

### `tools/filesystem/write_file.py`

写入项目内文件。

当前状态：

- 使用 Pydantic 校验参数。
- 支持覆盖写入和追加写入。
- 写入前会通过路径策略检查。
- 写入前会检查受保护路径。
- 工具元信息标记为 mutating，并要求上层确认。
- 会自动创建父目录。

注意事项：

- WebSocket 主路径会在写入前发出权限确认，批准后才重试执行。
- CLI 目前没有交互式权限确认，遇到该工具会收到权限错误文本。
- 目前没有 dry-run。
- 后续应纳入 mutating tool 调度策略。

### `tools/filesystem/edit_file.py`

按行范围替换文件内容。

当前状态：

- 使用 Pydantic 校验参数。
- 支持 1-based 行号。
- 校验 `start_line <= end_line`。
- 校验结束行不能超过文件总行数。
- 写入前会通过路径策略检查。
- 写入前会检查受保护路径。
- 工具元信息标记为 mutating，并要求上层确认。

注意事项：

- 目前没有 dry-run 预览。
- replacement 末尾换行处理还比较简单，后续需要更精细地保持原文件换行风格。
- WebSocket 主路径会在编辑前发出权限确认，批准后才重试执行。
- CLI 目前没有交互式权限确认，遇到该工具会收到权限错误文本。

### `tools/search/grep.py`

项目内文本搜索。

当前状态：

- 优先调用 `rg`。
- 如果没有 `rg`，回退到 `grep -R`。
- 搜索路径会先经过项目根目录限制。
- 返回 exit code、stdout、stderr。

注意事项：

- 当前是在宿主机直接运行 `rg/grep`，不走 macOS 命令沙箱。
- `max_count` 只应用到 `rg --max-count`，回退到 `grep` 时还没有等价限制。

### `tools/shell/run_command.py`

命令执行工具。

当前状态：

- 先通过 `SecurityPolicy.check_command()` 做危险命令和白名单检查。
- 非白名单但未命中危险规则的命令会返回 `ask` 权限 metadata。
- 被拒绝时记录到 `CircuitBreaker`。
- 连续拒绝达到阈值时，在错误文本和 metadata 中提示会话挂起。
- 即使命中白名单，`run_command` 也需要用户确认后才会进入 macOS 沙箱执行。
- 允许执行时交给 `SecureMacOSSandboxExecutor.run()`。
- 工具 schema 中的 `timeout` 已传给 macOS sandbox executor。
- 工具 schema 支持可选 `cwd`，会通过 `SecurityPolicy.resolve_command_cwd()` 限制在 active workspace 内部，并拒绝文件、越界路径和受保护目录。

注意事项：

- 权限确认和 approved retry 已在 WebSocket 主路径接入。
- 命令策略仍是硬编码，后续需要配置化。

### `tools/network/web_fetch.py`

网页抓取工具。

当前状态：

- 默认关闭。
- 需要设置 `ENABLE_WEB_FETCH=true` 才允许执行。
- 使用标准库 `urllib.request` 抓取最多 200KB 内容。

注意事项：

- 暂未接入 URL allowlist/denylist。
- 暂未做 SSRF 防护。
- 阶段 2 中应继续保持默认关闭。

### `tools/interaction/ask_user.py`

模型主动澄清提问工具。

当前状态：

- schema 和入参定义拆到 `tools/interaction/ask_user_spec.py`。
- handler 只负责校验问题、规范化选项，并返回 `user_interaction_action=ask` metadata。
- WebSocket runtime 会把该 metadata 转换为 `clarification_request`，等待 `clarification_response` 后把用户回答回填给模型继续执行。

注意事项：

- WebSocket 主路径支持等待用户回答；CLI 路径暂时没有交互式澄清 UI。

### `tools/toolkit.py`

旧工具入口兼容层。

当前状态：

- 不再承载核心工具逻辑。
- 通过 `ToolRegistry` 提供旧接口兼容。

## 安全模块

### `security/policy.py`

安全策略第一版。

当前能力：

- `resolve_path()`：限制路径必须位于项目根目录内。
- `ensure_writable_path()`：阻止写入 `.env`、`.git`、`.venv`、`__pycache__` 等受保护位置。
- `check_command()`：命令安全检查。
- `check_command()` 输出 `allow/deny/ask` 决策。
- 危险模式拦截，包括：
  - `rm -rf /`
  - fork bomb
  - `mkfs`
  - `dd if=`
  - `shutdown`
  - `reboot`
  - `curl | sh`
  - `wget | sh`
- 静态命令白名单，包括：
  - `ls`
  - `pwd`
  - `cat`
  - `head`
  - `tail`
  - `echo`
  - `rg`
  - `grep`
  - `find`
  - `python`
  - `python3`
  - `pytest`
  - `pip`
  - `npm`
  - `node`
  - `git`
  - `make`

当前不足：

- 白名单还是硬编码。
- 没有按工作模式区分 `default/confirm/auto_deny`。
- 没有独立 allow/deny rule 配置文件。
- shell 命令对受保护路径的检测还是基于命令文本，尚不能替代完整系统级文件访问控制。

### `security/circuit_breaker.py`

熔断器第一版。

当前能力：

- 按 `(session_id, risk_category)` 计数。
- 连续拒绝达到阈值后返回挂起信号。
- 成功执行后可重置对应类别计数。
- 恢复 session 时可按类别或 session 重置熔断计数。

当前不足：

- 挂起状态目前只保存在当前 WebSocket 连接内，还没有跨进程或重启持久化。
- 还没有更细的恢复审计或恢复原因记录。

## 沙箱模块

### `sandbox/macos_executor.py`

macOS 原生命令沙箱执行器。

当前能力：

- 使用 `/usr/bin/sandbox-exec` 和 Seatbelt profile。
- 命令在真实项目目录中执行。
- 可接收 workspace 内部 cwd 来改变 shell 执行目录，但 Seatbelt 写入根仍然是 active workspace root 和临时目录。
- 默认禁止网络访问。
- 允许全局读，用于读取系统工具、解释器、依赖和项目文件。
- 只允许写项目目录、`/tmp`、`/private/tmp`、`/private/var/folders` 和 `/dev/null`。
- 设置默认超时 `20s`。

当前不足：

- 允许命令产生的项目内文件变更真实落到项目目录。
- 目前只支持 macOS/Darwin；非 macOS 会返回不可用错误。
- 如果父进程本身已经处在受限沙箱中，`sandbox-exec` 可能返回 `sandbox_apply: Operation not permitted`，需要在正常终端/桌面应用运行环境中做端到端验证。
- 当前只依赖 Seatbelt profile、命令策略和基础受保护路径规则。
- 还没有系统级精细文件写入策略，例如只允许写某些扩展名或只允许写模型声明的路径。

### 已移除的 Docker 沙箱

阶段 2 早期使用 Docker executor。当前主路径已替换为 macOS 原生沙箱，Docker 依赖和旧 executor 已移除。

## WebSocket 服务

### `server/protocol/`

协议层。

当前职责：

- `events.py` 统一生成带 `schema_version`、`request_id`、`timestamp` 的 WebSocket event envelope。
- `events.py` 负责解析客户端 JSON packet，并在非法 JSON 或非对象输入时返回 `None`。
- `serialization.py` 负责把 LiteLLM/Pydantic/普通对象转换为 dict，供 protocol、streaming 和 title processor 复用。

设计边界：

- protocol 层只描述传输格式，不直接读写 transcript，也不执行模型或工具。

### `server/runtime/`

运行时支撑层。

当前职责：

- `model_config.py` 统一模型名、低成本模型、`API_KEY`/`API_BASE`、reasoning effort 和 DeepSeek thinking 参数。
- `model_stream.py` 负责 streaming delta 提取、tool call 增量拼接、assistant message 构造，以及历史 `reasoning_content` 清理。
- `session_state.py` 负责 WebSocket 内存态 session、挂起/恢复状态，以及首条非空用户输入到来时的延迟持久化。
- `websocket_context.py` 承载 WebSocket 连接内会反复变化的 workspace、session store、session id、session state、tool registry 和 messages。
- `transcript_events.py` 统一 transcript event 写入、assistant transcript payload 清理和用户拒绝 tool call 时的结构化结果。
- `turn_runner.py` 承载单轮模型 streaming、tool call 执行、权限等待、approve/deny 重试、tool result event 和 session suspension 事件。

设计边界：

- runtime 层承载可复用的 Agent 运行支撑能力，但不直接绑定具体 WebSocket packet 类型。

### `server/processors/`

业务处理器层。

当前职责：

- `request_dispatcher.py` 负责 `open_workspace`、`new_session`、`list_sessions`、`delete_session`、`resume_session`、`conversation_title_request` 等控制类 packet 分发。
- `title_processor.py` 负责根据首条用户消息调用低成本模型生成短标题，并统一清理与截断标题文本。
- 标题来源标记为 `low-cost-first-user`。

设计边界：

- processors 适合继续承载 `conversation_title_request`、未来 compact/summarize、workspace/project scan 等明确业务请求。

### `server/views/`

客户端视图投影层。

当前职责：

- `session_summary.py` 把 `SessionRecord` + transcript events 投影为历史会话列表摘要。
- 只返回有真实 user/assistant 消息的会话，避免空会话污染历史列表。
- 生成恢复会话时前端可展示的 user/assistant 消息。

设计边界：

- views 只做展示数据投影，不改变 transcript 原始事件。

### `server/app.py`

FastAPI WebSocket transport 和请求分发入口。

当前能力：

- 提供 `/agent/ws`。
- 接收 `user_input`。
- 推送带 `schema_version` 的 `ready`，并携带工具元信息和 `session_state`。
- `ready` 会携带当前 workspace 信息。
- 支持 `open_workspace` 打开经过验证的目录；成功后会创建新的 session，并把 `SessionStore` 和 `ToolRegistry` 绑定到新的 workspace root。
- 支持 `new_session` 创建新会话。
- WebSocket 新会话先只存在于内存；只有第一条非空 `user_input` 到达时才写入 `.codex-mini/sessions/`，避免启动、切 workspace、点击新对话产生空 transcript。
- 支持 `list_sessions` 返回历史会话摘要。
- 支持带 `session_id` 的 `resume_session` 从磁盘恢复历史会话上下文和可展示消息。
- 支持 `conversation_title_request`，当前标题生成策略为根据首条 user 消息调用低成本模型生成短标题，返回前清理与截断，来源标记为 `low-cost-first-user`。
- DeepSeek 模型请求会按官方 thinking 参数兼容：`off` 显式发送 `thinking.disabled`，开启思考时发送 `thinking.enabled` 并把 `low/medium` 映射为 `high`、`xhigh/max` 映射为 `max`。
- 每轮推送带 `session_id`、`turn_id`、`request_id`、`schema_version`、`timestamp` 的 `turn_started`。
- 根据消息结果推送：
  - `session_created`
  - `workspace_changed`
  - `workspace_error`
  - `sessions_list`
  - `tool_call_started`
  - `tool_call_result`
  - `permission_request`
  - `permission_decision_ack`
  - `clarification_request`
  - `clarification_response_ack`
  - `session_suspended`
  - `session_blocked`
  - `session_resumed`
  - `assistant_token`
  - `final_answer`
  - `conversation_title`
- 收到 `permission_decision` 且 `approved=true` 后，会用 `approved=True` 重试原工具调用。
- 收到 `permission_decision` 且 `approved=false` 后，会返回结构化 deny 结果并把结果回填给模型。
- 收到 `clarification_response` 后，会把用户回答作为 `ask_user` 的工具结果回填给模型。
- 收到 `resume_session` 后，会清理 session 挂起状态和对应熔断计数。
- WebSocket 路径已经用 LiteLLM `stream=True` 推送真实 `assistant_token`。
- WebSocket 路径会把用户消息、assistant 消息、工具结果、权限决定、挂起/恢复和标题事件写入 transcript。
- workspace 切换会写入 `workspace_opened` transcript 事件，并在新 workspace 下启动新的 session。
- 协议封包、模型配置、streaming helper、turn runner、session state、标题生成和 session view 已下沉到 `server/protocol/`、`server/runtime/`、`server/processors/`、`server/views/`。
- 控制类 WebSocket packet 已下沉到 `server/processors/request_dispatcher.py`，连接内可变状态由 `server/runtime/websocket_context.py` 承载。

当前不足：

- 还缺真实 WebSocket 端到端集成测试。
- streaming 还没有实现取消、背压或客户端断线后的任务恢复。
- session transcript 可以跨连接恢复，但 session suspension 等运行控制状态还没有完整跨进程持久化。
- `server/app.py` 已降为较薄的 FastAPI transport/request shell；后续更适合补端到端测试，而不是继续拆纯文件。

## 桌面客户端

### `desktop/`

Electron + Vue + TypeScript 本地客户端。

当前能力：

- 通过 WebSocket 连接 Python runtime。
- 展示聊天消息、assistant streaming、权限弹窗、工具结果和 session 状态。
- 支持新建会话、恢复会话、历史会话列表和模型生成标题展示。
- 支持通过 Electron 原生目录选择器打开工作区，并把用户选择的路径交给 Python runtime 验证与切换。
- Markdown 渲染使用 `markdown-it` + `DOMPurify`，并保持工具结果 UI 与 assistant 正文分离。
- 前端类型定义覆盖 runtime event 和 client packet。
- 前端 runtime store 已能保存 `workspace` 状态，并发送 `open_workspace` packet；原生目录选择 UI 已接入 TitleBar 操作区。

当前不足：

- 桌面端还缺自动化 smoke test。
- 历史会话和 runtime 操作仍是阶段 2 alpha 体验，后续需要继续打磨状态同步、错误提示和空状态。

## 当前运行链路

### CLI 链路

```text
make run
  |
  v
agent_loop.py
  |
  v
load_dotenv()
  |
  v
ToolRegistry(project_root)
  |
  v
LangGraph: model -> tools -> model
  |
  v
LiteLLM completion(tools=registry.schemas())
  |
  v
模型返回 tool_calls
  |
  v
registry.execute(name, arguments)
  |
  v
工具执行 / 安全检查 / macOS 原生沙箱
  |
  v
工具结果回填给模型
  |
  v
最终 assistant 文本输出到终端
```

### WebSocket 链路

```text
make run-server
  |
  v
FastAPI server/app.py
  |
  +--> server/protocol/events.py
  +--> server/runtime/model_config.py
  +--> server/runtime/model_stream.py
  +--> server/runtime/session_state.py
  +--> server/runtime/turn_runner.py
  +--> server/runtime/websocket_context.py
  +--> server/processors/request_dispatcher.py
  +--> server/processors/title_processor.py
  +--> server/views/session_summary.py
  |
  v
客户端连接 /agent/ws
  |
  v
服务端发送 ready
  |
  v
客户端发送 user_input
  |
  v
LiteLLM streaming completion
  |
  v
实时推送 assistant_token，并收集 assistant/tool_calls
  |
  v
推送 tool_call_started / tool_call_result / permission_request
  |
  v
如果需要权限，等待 permission_decision 并按需重试或返回 deny
  |
  v
如果 session_suspended，阻断后续 user_input，等待 resume_session
  |
  v
写入 .codex-mini/sessions transcript / index
  |
  v
final_answer
```

## 已验证状态

最近一次本地验证结果：

- `.venv/bin/python -m compileall agent_loop.py tools security sandbox server session tests` 通过。
- `.venv/bin/python -m unittest discover -s tests` 通过。
- 当前单测数量：95。
- `ToolRegistry` 能加载 7 个工具 schema。
- `read_file README.md` 可正常执行。
- 读取 `/etc/passwd` 会被路径越界策略拦截。
- 执行 `rm -rf /` 会被危险命令策略拦截。
- 非白名单命令会返回 `permission_action=ask`。
- 白名单命令也会在 `run_command` 执行前请求用户确认。
- `write_file` / `edit_file` 在未批准时会返回 `permission_action=ask`。
- WebSocket event helper、streaming tool call 拼接、session suspend/resume 状态有单元测试覆盖。
- session store、JSONL transcript、历史会话摘要、模型上下文恢复和首条用户提问标题生成有单元测试覆盖。
- `.env` 和 `.git` 相关受保护路径会被策略拦截。
- WebSocket 事件构造和基础字段可编译通过。

## 当前 Git 工作区状态

本文档不再记录瞬时 `git status`。实际工作区状态以本地 `git status --short` 为准，避免架构说明随临时改动过期。

## 阶段完成度评估

### 已完成

- 工具模块化拆分。
- `ToolRegistry` 注册与执行分发。
- 工具元信息。
- CLI Agent 接入新工具运行时。
- 基础路径安全检查。
- 基础受保护路径写入拦截。
- 基础命令白名单和危险模式拦截。
- `allow/deny/ask` 命令决策。
- 熔断器计数逻辑。
- 熔断器 reset 机制。
- macOS 原生沙箱真实 workspace 模式。
- WebSocket 服务骨架。
- WebSocket 真实 token streaming。
- WebSocket 权限 ask/approve/deny/retry 主路径。
- WebSocket 模型主动澄清 `ask_user` / `clarification_request` 主路径。
- `session_suspended` 状态记录、后续 turn 阻断和 `resume_session` 恢复事件。
- mutating 工具批准前阻断。
- SQLite session index。
- append-only JSONL transcript。
- transcript 到模型上下文的恢复。
- WebSocket session 创建、列表、磁盘恢复。
- WebSocket workspace 骨架、`open_workspace` 协议和 workspace 事件类型。
- 基于首条用户提问的 conversation title 生成。
- Electron/Vue 桌面客户端基础壳和 runtime WebSocket 接入。
- Electron 原生打开工作区入口。
- 桌面端 Markdown 渲染与基础清洗。
- README 基础运行说明。
- 基础 `unittest` 测试。

### 部分完成

- `run_command` 已接 macOS 原生沙箱，真实 workspace 改动会落盘，但策略细节还需完善。
- WebSocket 事件和状态机主路径已建立，但还缺真实端到端集成测试、取消、背压和断线恢复。
- workspace 当前完成后端协议闭环、Electron 原生目录选择入口和 workspace 内 `run_command.cwd`，尚未接入额外 allowed roots。
- 权限系统已有 `allow/deny/ask` 决策，但还不是完整可配置决策引擎。
- session transcript 可以恢复模型上下文，但运行控制状态、取消状态和更完整的 checkpoint/restore 还未产品化。
- 桌面客户端已有 runtime shell，但还需要 smoke test、交互 polish 和错误状态收口。

### 未完成

- 工具并发/串行调度。
- 配置化安全策略。
- `edit_file` dry-run。
- WebSocket 端到端集成测试。
- 显式 `add-dir`/allowed roots。
- 桌面客户端 smoke test。
- macOS 沙箱隔离回归测试。
- cancellation/backpressure。
- persistent suspension/checkpoint state。

## 下一步建议

建议按以下顺序继续收口，优先把“本地 runtime + 桌面客户端”这条线打通：

1. 冻结 runtime 协议。
   - 统一 event schema、session state、permission flow、suspension/resume、tool metadata、session list/resume、title event
2. 继续收口桌面客户端壳。
   - TypeScript + Node + Electron/Vue，负责聊天、tool timeline、diff、审批、Markdown、历史会话、命令输出
3. 再留出 IDE 扩展接口。
   - 复用同一 runtime 协议，不重复造一套后端
4. 补强 Python runtime。
   - 配置化 policy、工具调度、`edit_file` dry-run、checkpoint/restore、persistent suspension、cancellation/backpressure
5. 做集成测试和回归测试。
   - 桌面客户端 smoke test、runtime event test、macOS sandbox test

# 当前项目架构状况

本文档记录 Codex-mini 当前阶段的项目架构、模块职责、运行链路、已完成能力和仍待收口事项。若本文件与根目录 `AGENTS.md` 的项目状态描述不一致，应同步更新两者。

## 项目定位

Codex-mini 是一个 Python 版 Codex 类本地 Agent 学习项目，目前正处于阶段 2：从单体工具函数升级为可扩展的 Agent 工具运行时，并逐步走向本地桌面客户端 + Python runtime 的形态。

当前状态可以概括为：

- CLI Agent 主流程已经接入新的工具注册与分发机制。
- 工具层已经拆成 `tools/core/` 核心层和按领域组织的工具包。
- 安全策略、命令白名单、危险命令拦截和熔断器已经有第一版实现。
- `run_command` 已改为通过 macOS 原生沙箱在真实项目 workspace 中执行。
- FastAPI WebSocket 服务已经作为本地事件传输原型接入，但它的最终角色应是桌面客户端/IDE 扩展的 runtime bridge。
- WebSocket runtime contract 已有第一版：schema version、session state、真实 streaming、权限确认重试、session 挂起/阻断/恢复、session 创建/列表/磁盘恢复、基于首条用户提问的模型标题。
- 会话持久化已有第一版：`.codex-mini/sessions/index.sqlite` 作为索引，`transcripts/*.jsonl` 作为 append-only 事件流。
- workspace 后端骨架已有第一版：验证用户选择的工作区目录，并在 `open_workspace` 时重建 session store、tool registry 和 workspace payload。
- `run_command.cwd` 已有第一版：命令可以在 workspace 内部子目录执行，但 cwd 不会扩大 sandbox 写入边界。
- workspace/permission 专题架构已有目标文档，且 `WorkspaceContext v2`、统一 filesystem policy、network policy、policy-driven Seatbelt、prefix exec policy、permission mode 和 trust-gated project config 第一片已落地：`selected_root`、`project_root`、`current_dir`、session-only/trusted trust model、additional root model、workspace snapshot、`PermissionMode`、`FileSystemPolicy`、`PermissionProfile`、`ApprovalPolicy`、`NetworkPolicy` 和 `ExecPolicy` 已进入代码。
- `server/` 已从单体 WebSocket 文件开始拆分：`protocol/` 负责事件协议，`runtime/` 负责模型请求、streaming 和 session 状态，`processors/` 负责业务处理器，`views/` 负责给客户端展示的投影数据。
- 桌面客户端已经进入 Electron/Vue 本地 runtime client 方向，负责聊天、审批、Markdown 渲染、历史会话、工作区打开和会话操作。
- 已建立基础 `unittest` 测试，覆盖安全策略、工具注册表、WebSocket 事件、会话存储和恢复。

## 架构判断边界

当前项目已经完成的是“模块架构更新”：

- `server/app.py` 不再承担全部 WebSocket 业务细节，核心逻辑已经下沉到 `server/protocol/`、`server/runtime/`、`server/processors/`、`server/views/`。
- `tools/` 已拆成 core runtime 与领域工具包，工具 metadata、approval metadata、runner、router 和 catalog 都有独立边界。
- `session/`、`workspace/`、`security/`、`sandbox/` 已经成为可继续演进的后端域模块。

后续实现必须继续遵守这些架构约束：

- 不写旧协议、旧 schema、过渡 payload 的兼容性胶水代码，除非先明确提出并获得批准的迁移方案。
- 遵循最小职责原则：一个模块只承担一个清晰职责，新逻辑必须放到拥有该职责的领域模块，不能混进 transport、UI、持久化或 policy 层。
- 保持边界清晰：request dispatcher、model runtime、session store、workspace policy、desktop presentation 之间通过明确接口协作，不直接依赖彼此内部细节。
- 不用“先跑起来”的临时耦合替代架构设计；如果一个改动需要跨多个边界，应优先拆成小切片落地。

workspace/permission 已经开始 v2 落地，目前完成了 workspace 身份模型和统一 filesystem policy 的第一片。当前真实状态是：

- `workspace/models.py` 已经拆出 `selected_root`、`project_root`、`current_dir`、`WorkspaceTrust`、`WorkspaceSnapshot` 和 `AdditionalRoot`。
- workspace payload 只发 `selected_root`、`project_root`、`current_dir`、`trust`、`additional_roots` 等 v2 字段；不再发旧 `root` / `allowed_roots` 字段。
- `project_root` 已可在打开 Git repo 子目录时指向 canonical git root，`selected_root` 保持用户实际选择目录。
- `current_dir` 默认等于 `selected_root`，并可通过 `change_directory` 在已授权目录内切换；该操作不创建新 session。
- `security/permissions.py` 已提供 `FileSystemPolicy`、`PermissionProfile`、`ApprovalPolicy` 和 `NetworkPolicy`。
- `workspace/models.py` 已提供 `PermissionMode`，前端可通过 `set_permission_mode` 切换全局 runtime permission mode：`request_approval`、`auto_approve`、`full_access` 和 `custom`；后端会在 workspace 切换、新建会话和恢复会话时重新应用当前全局模式。
- `read_file`、`write_file`、`edit_file`、`grep` 和 `run_command.cwd` 已通过 `SecurityPolicy` 走 `current_dir + filesystem policy` 解析。
- `.env` 默认禁止 read/write；`.git`、`.codex-mini`、`.venv`、`__pycache__` 默认禁止 write；显式 `full_access` 模式会放开本机文件访问。
- `additional_roots` 已有模型，并可通过 `add_dir` 从 desktop protocol 显式加入 read/write 根；`FileSystemPolicy` 与 Seatbelt profile 会立即继承这些根。
- `resume_session` 只接受当前 v2 workspace snapshot：从 session metadata 和最后一次 `workspace_policy_changed` 事件取 `selected_root`、`project_root`、`current_dir`、`additional_roots`，缺失字段、旧字段或失效路径都会返回 `workspace_error`。
- `workspace/instructions.py` 会从 `project_root` 到 `current_dir` 分层加载 `AGENTS.md`，且不会越过项目根去读取 external additional root 的项目文档。
- 命令策略已拆到 `security/exec_policy.py`，支持 prefix allow/ask/deny、session allow、危险命令拒绝和 prefix suggestion。
- `permission_decision` transcript 事件会记录批准时的 workspace snapshot；恢复 session 时只会恢复与当前 workspace 安全边界一致的 session allow prefix，`policy.permission_mode` 由当前全局 runtime preference 重新应用，不参与 allowlist 匹配。
- `workspace/trust.py` 与 `workspace/project_config.py` 已提供第一版 trust gate：默认 `session_only` 不加载 `.codex-mini/config.toml`，trusted project 才能加载白名单 permission / exec policy 配置，敏感字段和过宽 allow rule 会被拒绝。
- WebSocket runtime 已提供 `trust_workspace` / `untrust_workspace` 控制事件，显式更新用户侧 trust store，刷新 workspace policy，并通过 `workspace_policy_changed` 通知前端。
- 桌面标题栏已展示当前 workspace trust level，并提供最小 trust/untrust 操作入口。
- `sandbox/macos_executor.py` 已按 filesystem/network policy 生成第一版 Seatbelt profile，不再使用固定写入模板；显式 `full_access` 模式会绕过 Seatbelt 并开启命令网络。

因此后续讨论时应区分两句话：

- “项目模块架构已经更新”是已完成事实。
- “workspace/permission v2 完整实现”不是当前事实，目前完成了 PR1、PR2、PR3、PR4、PR5、PR6 和 PR7 第二片，以及 session allowlist 恢复边界第一片；还差 project-local policy 持久化/编辑和更细 sandbox 回归。

## 顶层目录结构

```text
.
├── agent_loop.py
├── main.py
├── context/
│   ├── environment_context.py
│   ├── permissions_context.py
│   ├── model_context.py
│   └── user_context.py
├── context_manager/
│   ├── history.py
│   ├── truncation.py
│   └── updates.py
├── tools/
│   ├── __init__.py
│   ├── core/
│   │   ├── catalog.py
│   │   ├── context.py
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
│   ├── recovery.py
│   └── turn_context.py
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
              core registry schemas
                      |
                      v
              ToolRouter + ToolRunner
                      |
       +--------------+--------------+----------------+
       |              |              |                |
       v              v              v                v
  tools/*      security/policy.py   sandbox/macos_executor.py   context/session
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
- 根据 `MODEL_NAME` 读取主模型 id，`MODEL_PROVIDER` 仅用于服务商相关行为（例如 DeepSeek `/models` 拉取和 thinking 参数判断）。
- 构建 LangGraph 状态机。
- 把 core registry 的 `schemas()` 暴露给模型。
- 当模型发起 tool call 时，通过 `ToolRouter` 解析，并由 `ToolRunner` 执行工具。
- 将工具结果作为 `role=tool` 消息回填给模型。

当前状态：

- 已接入 `build_default_registry()` 和 `ToolRunner`。
- 仍是同步调用模型。
- 还没有真实 token streaming。
- 工具已具备 read-only/mutating/parallel 元信息，但调度目前仍是顺序执行。

### `context/`、`context_manager/` 与 `session/turn_context.py`

上下文系统已开始按官方 Codex 风格拆层。

主要职责：

- `context/environment_context.py` 保存 selected root、project root、current dir、git root 等环境快照。
- `context/permissions_context.py` 保存审批策略、沙箱类型、受保护路径和网络策略快照。
- `context/model_context.py` 保存本轮模型名、reasoning effort 和原始请求配置。
- `context/user_context.py` 保存系统提示词和本轮用户输入。
- `context_manager/history.py` 管理模型可见 messages，并负责清理不应回传的历史 `reasoning_content`。
- `session/turn_context.py` 聚合 session、turn、workspace、context fragments、registry、runner 和 diff tracker。

当前状态：

- WebSocket 主路径每个 `user_input` 都会创建一个 `TurnContext`。
- `run_turn()` 直接接收 `TurnContext`，不再散传 registry、runner、messages、session id、turn id 等参数。
- 工具调用会通过 `tools/core/context.py` 中升级后的 `ToolInvocation` 携带 session、turn、workspace 和 approval 状态。
- mutating 工具通过 `ToolInvocation.diff_tracker` 记录本轮 touched paths。

### `tools/core/`

工具系统已按 Codex 风格拆成 core 层和领域工具包，开发阶段不保留旧 project-root 构造入口。

主要职责：

- `tools/core/protocol.py` 定义工具最小协议和函数式工具适配器。
- `tools/core/registry.py` 只负责注册、查找、schema 和 metadata 暴露。
- `tools/core/catalog.py` 负责装配默认内置工具，替代 registry 内部硬编码。
- `tools/core/runner.py` 统一 JSON 参数解析、审批适配、异常包装和 `ToolResult` 归一化。
- `tools/core/router.py` 把模型返回的 tool call 解析为内部 `ToolInvocation`。
- 调用方直接组合 `build_default_registry()`、`create_tool_context()` 和 `ToolRunner`。

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
- session suspension 状态会写入 transcript，并在磁盘会话恢复时重建；active turn / cancellation 仍是连接内运行态。

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

### `tools/core/types.py`

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
- `write_file` 仍是直接写入工具，没有 dry-run。
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
- 支持 `dry_run=true`，返回 unified diff 预览且不写入文件。

注意事项：

- dry-run 仍会走同一套路径和范围校验，避免预览与真实写入边界不一致。
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

- 先通过 `SecurityPolicy.check_command()` 委托 `ExecPolicy` 做危险命令、prefix rule、简单只读命令和常见高风险命令检查。
- 需要用户确认的命令会返回 `ask` 权限 metadata，可携带 `suggested_prefix_rule`。
- 被拒绝时记录到 `CircuitBreaker`。
- 连续拒绝达到阈值时，在错误文本和 metadata 中提示会话挂起。
- `pwd`、`ls`、`rg`、`grep`、`cat`、`head`、`tail` 等简单只读探索命令会跳过确认；当前 workspace 内的窄范围只读 `find ... | sort` 管道和 `git status/log/diff` 等只读 Git 查询也可跳过确认；带 shell 组合符、外部路径、父目录路径、重定向、`find -exec/-delete`、Git 变更子命令或递归/跟随选项的命令仍会请求确认。
- `git`、`npm`、`python`、`node`、`make` 等常见但可能改变项目状态的命令默认仍需要用户确认；`auto_approve` / `full_access` 可自动批准非危险命令，session allow prefix 可跳过后续同类确认。
- 允许执行时交给 `SecureMacOSSandboxExecutor.run()`；`full_access` 模式传入 `sandbox_enabled=False`，直接通过 `/bin/sh -lc` 执行。
- 工具 schema 中的 `timeout` 已传给 macOS sandbox executor。
- 工具 schema 支持可选 `cwd`，会通过 `SecurityPolicy.resolve_command_cwd()` 限制在 active workspace 内部，并拒绝文件、越界路径和受保护目录。

注意事项：

- 权限确认和 approved retry 已在 WebSocket 主路径接入。
- 项目级命令策略配置已有 trust-gated project config 第一片，后续需要补运行时批准规则持久化和策略编辑入口。

### `tools/network/web_fetch.py`

网页抓取工具。

当前状态：

- 默认允许抓取普通公网 HTTP/HTTPS 页面，不再要求用户逐次审批。
- 请求前会拒绝 `localhost`、内网 IP、非公网解析结果和非 Web 协议地址。
- 使用标准库 `urllib.request` 抓取最多 200KB 内容。

注意事项：

- 暂未接入 URL allowlist/denylist。
- 已拦截 localhost、内网 IP 和非公网解析结果；更完整的 DNS 重绑定防护后续继续收口。

### `tools/interaction/ask_user.py`

模型主动澄清提问工具。

当前状态：

- schema 和入参定义拆到 `tools/interaction/ask_user_spec.py`。
- handler 只负责校验问题、规范化选项，并返回 `user_interaction_action=ask` metadata。
- WebSocket runtime 会把该 metadata 转换为 `clarification_request`，等待 `clarification_response` 后把用户回答回填给模型继续执行。

注意事项：

- WebSocket 主路径支持等待用户回答；CLI 路径暂时没有交互式澄清 UI。

## 安全模块

### `security/permissions.py`

workspace filesystem 和 permission 基础模型。

当前能力：

- `PermissionProfile`：已有 `read_only`、`workspace_write`、`danger_no_sandbox` 档位。
- `ApprovalPolicy`：已有 `ask-before-mutating`、`auto` 和 `never` 枚举。
- `FileSystemPolicy`：按 `selected_root`、`current_dir`、readable roots、writable roots 统一解析路径。
- `FileSystemPolicy.danger_full_access()`：用户显式选择完全访问后，允许访问本机文件系统。
- 相对路径默认以 `current_dir` 为基准。
- `.env` 默认禁止读取和写入。
- `.git`、`.codex-mini`、`.venv`、`__pycache__` 默认禁止写入。
- additional roots 可表达 read-only 或 writable，并已通过 `add_dir` 接入 WebSocket/desktop 第一版。

当前不足：

- 已有 trust-gated project-local permission / exec policy config 第一片，但还没有 glob rule、策略编辑 UI 或完整持久化体验。
- additional roots 已写入 workspace payload，并会在 session resume 时严格重新验证；丢失或失效目录会让恢复失败并返回 `workspace_error`，不做 warning 式修复。
- `danger_no_sandbox` 已接到显式 `full_access` runtime mode；不得由项目配置静默启用。

### `security/policy.py`

安全策略门面和命令检查。

当前能力：

- `resolve_path()`：限制路径必须位于 filesystem policy 已知根目录内。
- `resolve_read_path()`：解析路径并检查 read policy。
- `resolve_write_path()` / `ensure_writable_path()`：解析路径并检查 write policy。
- `resolve_command_cwd()`：用 filesystem policy 检查命令 cwd，cwd 不扩大权限。
- `check_command()`：委托 `ExecPolicy` 做命令安全检查。
- `check_command()` 输出 `allow/deny/ask` 决策。

### `security/exec_policy.py`

prefix exec policy 和命令启发式。

当前能力：

- `ExecPolicyRule` 支持 prefix allow/ask/deny。
- `ExecPolicy.with_session_allow()` 可构建本会话允许规则。
- 危险命令直接 deny，包括 `rm -rf /`、fork bomb、`mkfs`、`dd if=`、`shutdown`、`reboot`、`curl | sh`、`wget | sh`、`sudo`、`chmod -R 777`。
- permission metadata 可携带 `suggested_prefix_rule`，例如 `["ruff", "check"]`、`["npm", "run", "test"]`。
- `python -c`、shell wrapper、`node -e`、destructive 命令不会建议持久 prefix rule。
- `ApprovalPolicy.NEVER` 下需要 ask 的命令会降级为 deny。
- WebSocket `permission_decision` 支持 `scope=session` 与 `prefix_rule`，批准后把匹配的 suggested prefix 加入当前会话 allow rules。
- resume 会从 transcript 恢复同 workspace 安全边界下的 session allow prefix；workspace 后续变化时不继承旧批准，当前全局 permission mode 不影响该匹配。

当前不足：

- trusted project 可加载 project-local exec policy 配置文件第一片。
- 还没有把运行时批准产生的 prefix rules 持久化到 trusted project/user config。
- known safe commands 仍是内置集合，还没有按工作模式区分。
- shell 命令对受保护路径的检测还是基于命令文本，尚不能替代完整系统级文件访问控制。
- shell command segment 解析仍是第一版启发式，不是完整 shell AST。

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
- 可接收 workspace 内部 cwd 来改变 shell 执行目录，cwd 仍需通过 `FileSystemPolicy.resolve_command_cwd()`。
- `run()` 接收 `FileSystemPolicy` 和 `NetworkPolicy`，并据此生成 Seatbelt profile。
- 默认禁止网络访问；只有 `NetworkPolicy.ENABLED` 才生成 network allow。
- macOS 二进制启动需要读取 dyld/cryptex/runtime 等非稳定公开路径，因此命令沙箱放开 `file-read*` 以保证进程可启动；workspace 内 `.env` 等受保护读路径仍用显式 deny 拦截。
- 只允许写 filesystem policy writable roots、`/tmp`、`/private/tmp`、`/private/var/folders` 和 `/dev/null`。
- 设置默认超时 `20s`。

当前不足：

- 允许命令产生的项目内文件变更真实落到项目目录。
- 目前只支持 macOS/Darwin；非 macOS 会返回不可用错误。
- 如果父进程本身已经处在受限沙箱中，`sandbox-exec` 可能返回 `sandbox_apply: Operation not permitted`，需要在正常终端/桌面应用运行环境中做端到端验证。
- 当前 Seatbelt 对 `run_command` 的读隔离是受保护路径级别，不是完整 readable roots 隔离；完整读隔离需要后续更底层的 sandbox adapter 或进程启动白名单研究。
- 还没有系统级精细文件写入策略，例如只允许写某些扩展名或只允许写模型声明的路径。
- 当前只实现 roots + protected name 级别的 profile 生成；复杂 glob/special path 展开还没有做。

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

- `model_config.py` 统一模型名、低成本模型、`API_KEY`/`API_BASE`、reasoning effort、DeepSeek `/models` 动态模型列表和 DeepSeek thinking 参数。
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
- 支持 `open_workspace` 打开经过验证的目录；成功后会创建新的 session，并把 `SessionStore` 绑定到 `project_root`、把工具执行边界绑定到 `selected_root`。
- 支持 `change_directory` 在已授权目录内切换当前执行目录；成功后推送 `workspace_policy_changed`，不创建新 session。
- 支持 `add_dir` 显式加入 read/write additional root；成功后刷新工具 runner 的 filesystem policy，并保留会话级 exec policy。
- 支持 `new_session` 创建新会话。
- WebSocket 新会话先只存在于内存；只有第一条非空 `user_input` 到达时才写入 `.codex-mini/sessions/`，避免启动、切 workspace、点击新对话产生空 transcript。
- 支持 `list_sessions` 返回历史会话摘要。
- 支持带 `session_id` 的 `resume_session` 从磁盘恢复历史会话上下文、可展示消息和重新验证后的 workspace policy。
- 支持 `conversation_title_request`，当前标题生成策略为根据首条 user 消息调用低成本模型生成短标题，返回前清理与截断，来源标记为 `low-cost-first-user`。
- DeepSeek 模型请求会按官方 thinking 参数兼容：`off` 显式发送 `thinking.disabled`，开启思考时发送 `thinking.enabled` 并把 `low/medium` 映射为 `high`、`xhigh/max` 映射为 `max`。
- 每轮推送带 `session_id`、`turn_id`、`request_id`、`schema_version`、`timestamp` 的 `turn_started`。
- 根据消息结果推送：
  - `session_created`
  - `workspace_changed`
  - `workspace_policy_changed`
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
- `permission_request` metadata 会携带当前 `current_dir`、`permission_profile`、`approval_policy` 和 `network_policy`，前端权限弹窗可直接展示当前安全边界。
- 收到 `resume_session` 后，会清理 session 挂起状态和对应熔断计数。
- WebSocket 路径已经用 LiteLLM `stream=True` 推送真实 `assistant_token`。
- WebSocket 路径会把用户消息、assistant 消息、工具结果、权限决定、挂起/恢复和标题事件写入 transcript。
- workspace 切换会写入 `workspace_opened` transcript 事件，并在新 workspace 下启动新的 session；workspace policy 变化会写入 `workspace_policy_changed` transcript 事件。
- 协议封包、模型配置、streaming helper、turn runner、session state、标题生成和 session view 已下沉到 `server/protocol/`、`server/runtime/`、`server/processors/`、`server/views/`。
- 控制类 WebSocket packet 已下沉到 `server/processors/request_dispatcher.py`，连接内可变状态由 `server/runtime/websocket_context.py` 承载。

当前不足：

- 已有 TestClient 级 `/agent/ws` 集成测试，但还缺真实桌面 smoke test。
- streaming 等待点已有取消和忙碌反馈第一版，但流式模型请求本身还没有强制中断或客户端断线后的任务恢复。
- session transcript 可以跨连接恢复模型上下文和 suspension 状态，但完整 checkpoint/restore 还未产品化。
- `server/app.py` 已降为较薄的 FastAPI transport/request shell；后续更适合补 smoke/integration 测试，而不是继续拆纯文件。

## 桌面客户端

### `desktop/`

Electron + Vue + TypeScript 本地客户端。

当前能力：

- 通过 WebSocket 连接 Python runtime。
- 展示聊天消息、assistant streaming、权限弹窗、工具结果和 session 状态。
- 支持新建会话、恢复会话、历史会话列表和模型生成标题展示。
- 支持通过 Electron 原生目录选择器打开工作区、切换当前目录、加入额外目录，并把用户选择的路径交给 Python runtime 验证与切换。
- Markdown 渲染使用 `markdown-it` + `DOMPurify`，并保持工具结果 UI 与 assistant 正文分离。
- 前端类型定义覆盖 runtime event 和 client packet。
- 前端 runtime store 已能保存 `workspace` 状态，并发送 `open_workspace`、`change_directory`、`add_dir`、`set_permission_mode` packet；原生目录选择 UI 已接入 TitleBar 操作区。
- TitleBar 已展示 workspace trust；权限弹窗已简化为命令确认；ChatComposer 已提供权限模式切换入口。

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
build_default_registry()
create_tool_context(project_root)
ToolRunner(registry, context)
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
runner.execute(name, arguments)
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

- `.venv/bin/python -m unittest discover -s tests` 当前通过。
- 当前单测数量：270。
- `ToolRegistry` 能加载 12 个工具 schema。
- `read_file README.md` 可正常执行。
- `list_files`、`file_search`、`run_tests`、`update_plan` 和 `create_task_list` 已进入默认工具目录。
- `run_tests` 已走 command executor、filesystem policy、network policy 和 sandbox mode，不再直接 `subprocess.run`。
- `update_plan` / `create_task_list` 已标记为写 runtime task state 的 mutating 工具，默认需要审批。
- 读取 `/etc/passwd` 会被路径越界策略拦截。
- 执行 `rm -rf /` 会被危险命令策略拦截。
- 非白名单命令会返回 `permission_action=ask`。
- 简单只读探索命令、窄范围只读 `find ... | sort` 管道和只读 Git 查询可直接进入 `run_command`，常见但可能变更项目的命令仍会在执行前请求用户确认。
- `write_file` / `edit_file` 在未批准时会返回 `permission_action=ask`。
- WebSocket event helper、streaming tool call 拼接、session suspend/resume 状态有单元测试覆盖。
- WebSocket `task_list -> final_answer` 事件序列有集成测试覆盖。
- session store、JSONL transcript、历史会话摘要、模型上下文恢复和首条用户提问标题生成有单元测试覆盖。
- `.env` 和 `.git` 相关受保护路径会被策略拦截。
- macOS Seatbelt profile 会按 filesystem/network policy 生成，测试覆盖 readable/writable roots、additional roots 和默认 network restricted。
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
- `session_suspended` 状态记录、后续 turn 阻断、持久挂起恢复和 `resume_session` 恢复事件。
- `cancel_turn`、`session_busy` 和 turn 运行状态字段第一版。
- mutating 工具批准前阻断。
- SQLite session index。
- append-only JSONL transcript。
- transcript 到模型上下文的恢复。
- WebSocket session 创建、列表、磁盘恢复。
- WebSocket workspace 骨架、`open_workspace` 协议和 workspace 事件类型。
- `WorkspaceContext v2` 第一片：`selected_root`、`project_root`、`current_dir`、session-only trust、workspace snapshot 和 canonical workspace payload。
- `FileSystemPolicy`、`PermissionProfile`、`ApprovalPolicy`、`NetworkPolicy` 第一版。
- read/write/edit/grep/run_command cwd 统一走 `current_dir + filesystem policy`。
- `change_directory`、`add_dir` 和 `workspace_policy_changed` 第一版。
- `.env` read/write 保护和 `.codex-mini` write 保护。
- 基于首条用户提问的 conversation title 生成。
- user turn 进入模型前的 `task_list` 生成和前端事件。
- `edit_file` dry-run 预览。
- `list_files` 文件枚举、`file_search` 文件名模糊搜索、`run_tests` 测试摘要工具。
- `update_plan` / `create_task_list` 轻量任务追踪工具。
- Electron/Vue 桌面客户端基础壳和 runtime WebSocket 接入。
- Electron 原生打开工作区入口。
- 桌面端 Markdown 渲染与基础清洗。
- README 基础运行说明。
- 基础 `unittest` 测试。
- `/agent/ws` TestClient 集成测试第一版。

### 部分完成

- `run_command` 已接 macOS 原生沙箱和 policy-driven Seatbelt 第一版，真实 workspace 改动会落盘；显式 `full_access` 模式会绕过 Seatbelt。
- WebSocket 事件和状态机主路径已建立，已有 TestClient 集成测试、等待点取消和忙碌反馈第一版；还缺断线恢复和更完整的流式中断。
- workspace 当前完成后端协议闭环、Electron 原生目录选择入口、workspace 内 `run_command.cwd`、`change_directory`、`add_dir`、resume 重新验证、project instructions 分层加载和 `selected_root/project_root/current_dir` 分离。
- 权限系统已有 `allow/deny/ask` 决策、统一 filesystem policy、prefix exec policy、runtime permission mode 和 policy-driven Seatbelt 第一版，但还不是完整可编辑 project policy。
- session transcript 可以恢复模型上下文和持久挂起状态，但更完整的 checkpoint/restore 还未产品化。
- `run_tests` 已接入 sandbox/permission 边界，但测试框架识别和失败解析仍是第一版启发式实现。
- 桌面客户端已有 runtime shell，但还需要 smoke test、交互 polish 和错误状态收口。

### 未完成

- 工具并发/串行调度。
- 可编辑 project policy UI。
- `WorkspaceContext v2` 后续：workspace policy 编辑和跨 session 运行时 allowlist 持久化。
- 桌面客户端 smoke test。
- 更完整的 macOS 沙箱隔离回归测试。
- 流式模型请求的强制中断与断线恢复。
- 完整 checkpoint/restore。

## 下一步建议

建议按以下顺序继续收口，优先把“本地 runtime + 桌面客户端”这条线打通：

1. 补 project trust 产品化闭环。
   - trust/untrust 控制事件和桌面端状态展示已完成第一版；下一步补策略编辑入口和跨 session allowlist 持久化。
2. 继续收口桌面客户端壳。
   - UI 已提供 `change_directory`、`add_dir` 和权限模式切换入口；下一步补更完整错误状态、模式说明和 smoke test。
3. 做集成测试和回归测试。
   - WebSocket workspace/permission tests、desktop smoke test、macOS sandbox policy tests。

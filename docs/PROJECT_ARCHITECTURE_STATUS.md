# 当前项目架构状况

本文档记录 Codex-mini 当前阶段的项目架构、模块职责、运行链路、已完成能力和仍待收口事项。

## 项目定位

Codex-mini 是一个 Python 版 Codex 类 Agent 学习项目，目前正处于阶段 2：从单体工具函数升级为可扩展的 Agent 工具运行时，并逐步走向本地桌面客户端 + Python runtime 的形态。

当前状态可以概括为：

- CLI Agent 主流程已经接入新的工具注册与分发机制。
- 工具层已经从 `tools/toolkit.py` 拆分为多个独立工具模块。
- 安全策略、命令白名单、危险命令拦截和熔断器已经有第一版实现。
- `run_command` 已改为通过 Docker 执行器在真实项目 workspace 中执行。
- FastAPI WebSocket 服务已经作为本地事件传输原型接入，但它的最终角色应是桌面客户端/IDE 扩展的 runtime bridge。
- WebSocket runtime contract 已有第一版：schema version、session state、真实 streaming、权限确认重试、session 挂起/阻断/恢复。
- 已建立基础 `unittest` 测试，覆盖安全策略和工具注册表。

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
│   ├── read_file.py
│   ├── write_file.py
│   ├── edit_file.py
│   ├── grep.py
│   ├── run_command.py
│   └── web_fetch.py
├── security/
│   ├── __init__.py
│   ├── policy.py
│   └── circuit_breaker.py
├── sandbox/
│   ├── __init__.py
│   ├── docker_executor.py
│   └── executor.py
├── server/
│   ├── __init__.py
│   └── app.py
├── tests/
│   ├── test_security_policy.py
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
       +--------------+--------------+
       |              |              |
       v              v              v
  tools/*.py   security/policy.py   sandbox/docker_executor.py
       |              |              |
       +--------------+--------------+
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

### `tools/registry.py`

工具注册与分发中心。

主要职责：

- 初始化 `ToolExecutionContext`。
- 注册默认工具。
- 对外提供 OpenAI/LiteLLM tool schema。
- 解析工具 JSON 参数。
- 将工具调用路由到对应 handler。
- 捕获权限错误和运行时错误，统一包装成 `ToolResult`。

当前注册工具：

- `read_file`
- `write_file`
- `edit_file`
- `grep`
- `run_command`
- `web_fetch`

当前状态：

- registry/router 基础能力已完成。
- 还没有独立 `router.py` 或 `pipeline.py`。
- 已加入工具元信息：`is_read_only`、`is_mutating`、`supports_parallel`、`requires_approval`。
- 权限错误已带 `metadata`，可表达 `ask/deny`、风险类别、命令和 session suspension 状态。
- WebSocket 层已经消费 `ask/deny/session_suspended` 等 metadata，并据此推送权限、结果、挂起和恢复相关事件。

### `tools/types.py`

工具运行时共享类型。

主要职责：

- 定义 `ToolResult`。
- 定义 `ToolMeta`。
- 定义 `ToolPermissionError`。
- 定义 `ToolExecutionContext`。
- 将项目根目录、session id、安全策略、熔断器和 Docker 执行器传给各工具。

当前状态：

- 类型层已经能支持当前工具调用。
- 已支持工具元信息和权限 metadata。
- 后续可继续扩展事件 metadata 和调度策略。

### `tools/read_file.py`

读取项目内文件。

当前状态：

- 使用 Pydantic 校验参数。
- 通过 `SecurityPolicy.resolve_path()` 限制路径必须在项目根目录内。
- 支持 UTF-8 读取，错误字符替换。

### `tools/write_file.py`

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

### `tools/edit_file.py`

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

### `tools/grep.py`

项目内文本搜索。

当前状态：

- 优先调用 `rg`。
- 如果没有 `rg`，回退到 `grep -R`。
- 搜索路径会先经过项目根目录限制。
- 返回 exit code、stdout、stderr。

注意事项：

- 当前是在宿主机直接运行 `rg/grep`，不是 Docker 沙箱。
- `max_count` 只应用到 `rg --max-count`，回退到 `grep` 时还没有等价限制。

### `tools/run_command.py`

命令执行工具。

当前状态：

- 先通过 `SecurityPolicy.check_command()` 做危险命令和白名单检查。
- 非白名单但未命中危险规则的命令会返回 `ask` 权限 metadata。
- 被拒绝时记录到 `CircuitBreaker`。
- 连续拒绝达到阈值时，在错误文本和 metadata 中提示会话挂起。
- 即使命中白名单，`run_command` 也需要用户确认后才会进入 Docker 执行。
- 允许执行时交给 `SecureDockerExecutor.run()`。
- 工具 schema 中的 `timeout` 已传给 Docker executor。

注意事项：

- 权限确认和 approved retry 已在 WebSocket 主路径接入。
- 命令策略仍是硬编码，后续需要配置化。

### `tools/web_fetch.py`

网页抓取工具。

当前状态：

- 默认关闭。
- 需要设置 `ENABLE_WEB_FETCH=true` 才允许执行。
- 使用标准库 `urllib.request` 抓取最多 200KB 内容。

注意事项：

- 暂未接入 URL allowlist/denylist。
- 暂未做 SSRF 防护。
- 阶段 2 中应继续保持默认关闭。

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

### `sandbox/docker_executor.py`

Docker 命令执行器。

当前能力：

- 使用 `python:3.11-slim` 镜像。
- 真实项目根目录以读写方式挂载到 `/workspace`。
- 容器内禁用网络。
- 设置内存限制 `512m`。
- 设置 CPU 限制 `1 CPU`。
- 设置默认超时 `20s`。
- 执行结束后清理容器。
- 仅在本地缺少镜像时拉取镜像。

当前不足：

- 容器内允许命令产生的文件变更会真实落到项目目录。
- 当前只依赖 Docker 挂载边界、禁网络、命令策略和基础受保护路径规则。
- 还没有系统级精细文件写入策略，例如只允许写某些扩展名或只允许写模型声明的路径。

### `sandbox/executor.py`

历史沙箱实现文件。

当前状态：

- 项目中仍然存在。
- 当前主路径使用的是 `sandbox/docker_executor.py`。
- 后续需要确认是否保留、迁移或删除旧实现。

## WebSocket 服务

### `server/app.py`

FastAPI WebSocket 服务。

当前能力：

- 提供 `/agent/ws`。
- 接收 `user_input`。
- 推送带 `schema_version` 的 `ready`，并携带工具元信息和 `session_state`。
- 每轮推送带 `session_id`、`turn_id`、`request_id`、`schema_version`、`timestamp` 的 `turn_started`。
- 根据消息结果推送：
  - `tool_call_started`
  - `tool_call_result`
  - `permission_request`
  - `permission_decision_ack`
  - `session_suspended`
  - `session_blocked`
  - `session_resumed`
  - `assistant_token`
  - `final_answer`
- 收到 `permission_decision` 且 `approved=true` 后，会用 `approved=True` 重试原工具调用。
- 收到 `permission_decision` 且 `approved=false` 后，会返回结构化 deny 结果并把结果回填给模型。
- 收到 `resume_session` 后，会清理 session 挂起状态和对应熔断计数。
- WebSocket 路径已经用 LiteLLM `stream=True` 推送真实 `assistant_token`。

当前不足：

- 还缺 WebSocket 端到端集成测试。
- streaming 还没有实现取消、背压或客户端断线后的任务恢复。
- session state 目前是单连接内存态，还没有跨连接恢复。

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
工具执行 / 安全检查 / Docker 沙箱
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
final_answer
```

## 已验证状态

最近一次本地验证结果：

- `.venv/bin/python -m compileall agent_loop.py tools security sandbox server tests` 通过。
- `.venv/bin/python -m unittest discover -s tests` 通过。
- 当前单测数量：19。
- `ToolRegistry` 能加载 6 个工具 schema。
- `read_file README.md` 可正常执行。
- 读取 `/etc/passwd` 会被路径越界策略拦截。
- 执行 `rm -rf /` 会被危险命令策略拦截。
- 非白名单命令会返回 `permission_action=ask`。
- 白名单命令也会在 `run_command` 执行前请求用户确认。
- `write_file` / `edit_file` 在未批准时会返回 `permission_action=ask`。
- WebSocket event helper、streaming tool call 拼接、session suspend/resume 状态有单元测试覆盖。
- `.env` 和 `.git` 相关受保护路径会被策略拦截。
- WebSocket 事件构造和基础字段可编译通过。

## 当前 Git 工作区状态

当前分支：

```text
main
```

当前状态：

- 存在本轮里程碑 1 相关修改。
- `AGENTS.md` 当前仍是未跟踪文件。
- 尚未提交当前 runtime contract 收口改动。

本轮主要修改文件：

- `tools/registry.py`
- `security/circuit_breaker.py`
- `server/app.py`
- `tests/test_security_policy.py`
- `tests/test_tool_registry.py`
- `tests/test_server_events.py`
- `docs/PROJECT_ARCHITECTURE_STATUS.md`
- `AGENTS.md`

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
- Docker 执行器真实 workspace 模式。
- WebSocket 服务骨架。
- WebSocket 真实 token streaming。
- WebSocket 权限 ask/approve/deny/retry 主路径。
- `session_suspended` 状态记录、后续 turn 阻断和 `resume_session` 恢复事件。
- mutating 工具批准前阻断。
- README 基础运行说明。
- 基础 `unittest` 测试。

### 部分完成

- `run_command` 已接 Docker，真实 workspace 改动会落盘，但策略细节还需完善。
- WebSocket 事件和状态机主路径已建立，但还缺端到端集成测试、取消、背压和断线恢复。
- 权限系统已有 `allow/deny/ask` 决策，但还不是完整可配置决策引擎。

### 未完成

- 工具并发/串行调度。
- 配置化安全策略。
- `edit_file` dry-run。
- WebSocket 集成测试。
- Docker 隔离回归测试。

## 下一步建议

建议按以下顺序继续收口，优先把“本地 runtime + 桌面客户端”这条线打通：

1. 冻结 runtime 协议。
   - 统一 event schema、session state、permission flow、suspension/resume、tool metadata
2. 先做桌面客户端壳。
   - TypeScript + Node + Electron，负责聊天、tool timeline、diff、审批、命令输出
3. 再留出 IDE 扩展接口。
   - 复用同一 runtime 协议，不重复造一套后端
4. 补强 Python runtime。
   - 真实 streaming、配置化 policy、工具调度、`edit_file` dry-run、checkpoint/restore
5. 做集成测试和回归测试。
   - 桌面客户端 smoke test、runtime event test、Docker sandbox test

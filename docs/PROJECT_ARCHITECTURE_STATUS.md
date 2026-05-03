# 当前项目架构状况

本文档记录 Codex-mini 当前阶段的项目架构、模块职责、运行链路、已完成能力和仍待收口事项。

## 项目定位

Codex-mini 是一个 Python 版 Codex 类 Agent 学习项目，目前正处于阶段 2：从单体工具函数升级为可扩展的 Agent 工具运行时，并逐步加入安全策略、Docker 沙箱、WebSocket 服务和权限确认闭环。

当前状态可以概括为：

- CLI Agent 主流程已经接入新的工具注册与分发机制。
- 工具层已经从 `tools/toolkit.py` 拆分为多个独立工具模块。
- 安全策略、命令白名单、危险命令拦截和熔断器已经有第一版实现。
- `run_command` 已改为通过 Docker 执行器在真实项目 workspace 中执行。
- FastAPI WebSocket 服务已有骨架，但权限确认和真实 token 流式输出还没有完全闭环。
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
- WebSocket 层还没有完全消费这些结构化 metadata。

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

- 目前只是标记 `requires_approval=True`，还没有在 CLI/WebSocket 中强制写入前权限确认。
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
- 目前只是标记 `requires_approval=True`，还没有在 CLI/WebSocket 中强制编辑前权限确认。

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
- 允许执行时交给 `SecureDockerExecutor.run()`。
- 工具 schema 中的 `timeout` 已传给 Docker executor。

注意事项：

- 挂起状态目前没有真正进入统一 session 状态机。
- 权限确认已有结构化 metadata，但还没有完成 permission request/retry 机制。

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

当前不足：

- 挂起状态没有持久化到 session。
- 上层 WebSocket 还没有统一发送 `session_suspended` 事件。
- 没有恢复会话 API 或事件。

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
- 推送 `ready`，并携带工具元信息。
- 每轮推送带 `session_id`、`turn_id`、`request_id`、`timestamp` 的 `turn_started`。
- 根据消息结果推送：
  - `tool_call_started`
  - `tool_call_result`
  - `permission_request`
  - `permission_decision_ack`
  - `session_suspended`
  - `assistant_token`
  - `final_answer`
- 收到 `permission_decision` 且 `approved=true` 后，会用 `approved=True` 重试原工具调用。

当前不足：

- `assistant_token` 是最终答案按空格模拟切分，不是真实模型 streaming。
- `permission_request` 目前只覆盖 registry 返回 `permission_action=ask` 的工具结果。
- `session_suspended` 已能推送事件，但还没有真正阻断后续 turn 或提供恢复事件。

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
Graph invoke
  |
  v
收集 assistant/tool 消息
  |
  v
推送 tool_call_started / tool_call_result / permission_request
  |
  v
如果需要权限，等待 permission_decision 并按需重试工具
  |
  v
模拟 assistant_token
  |
  v
final_answer
```

## 已验证状态

最近一次本地验证结果：

- `.venv/bin/python -m compileall agent_loop.py tools security sandbox server` 通过。
- `.venv/bin/python -m unittest discover -s tests` 通过。
- `ToolRegistry` 能加载 6 个工具 schema。
- `read_file README.md` 可正常执行。
- 读取 `/etc/passwd` 会被路径越界策略拦截。
- 执行 `rm -rf /` 会被危险命令策略拦截。
- 非白名单命令会返回 `permission_action=ask`。
- `.env` 和 `.git` 相关受保护路径会被策略拦截。
- WebSocket 事件构造和基础字段可编译通过。

## 当前 Git 工作区状态

当前分支：

```text
main
```

当前状态：

- 存在多处已修改文件。
- 存在多个未跟踪的新模块。
- 尚未提交阶段 2 相关改动。

主要新增模块：

- `tools/read_file.py`
- `tools/write_file.py`
- `tools/edit_file.py`
- `tools/grep.py`
- `tools/run_command.py`
- `tools/web_fetch.py`
- `tools/registry.py`
- `tools/types.py`
- `security/policy.py`
- `security/circuit_breaker.py`
- `sandbox/docker_executor.py`
- `server/app.py`
- `tests/test_security_policy.py`
- `tests/test_tool_registry.py`

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
- Docker 执行器真实 workspace 模式。
- WebSocket 服务骨架。
- README 基础运行说明。
- 基础 `unittest` 测试。

### 部分完成

- `run_command` 已接 Docker，真实 workspace 改动会落盘，但策略细节还需完善。
- WebSocket 事件已初步建立，permission approve/retry 主路径已接入，但还缺集成测试。
- 权限系统已有 `allow/deny/ask` 决策，但还不是完整可配置决策引擎。

### 未完成

- 真实 token streaming。
- `session_suspended` 恢复机制。
- 工具并发/串行调度。
- 配置化安全策略。
- `edit_file` dry-run。
- WebSocket 集成测试。
- Docker 隔离回归测试。

## 下一步建议

建议按以下顺序继续收口：

1. 增加 `session_suspended` 恢复机制，并在挂起时阻断后续 turn。
2. 接入 LiteLLM 真实 streaming。
3. 基于工具元信息实现读工具并发、写工具串行。
4. 将命令白名单和策略模式改为配置化。
5. 给 `edit_file` 补 dry-run。
6. 增加 WebSocket permission approve/retry 集成测试。
7. 增加 Docker 沙箱回归测试。

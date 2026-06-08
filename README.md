# 

# Xagent

一个用于学习和实习展示的 Codex 类本地 Agent 项目：Python runtime 负责模型循环、工具执行、安全策略、沙箱和会话持久化，Electron/Vue 桌面客户端负责聊天、审批、工具过程和历史会话体验。
<img width="940" height="828" alt="截屏2026-05-13 18 35 59" src="https://github.com/user-attachments/assets/b35d6895-5fda-4d4e-80b1-ea53b1f95b88" />


![](/Users/shajinhui/Desktop/截屏2026-05-13%2018.35.59.png)

## 标准开发流程

1. 初始化环境与依赖

```bash
make init
```

2. 配置环境变量

```bash
cp .env.example .env
```

3. 启动 Agent（CLI）

```bash
make run
```

4. 启动 WebSocket 服务（阶段 2）

```bash
make run-server
```

5. 启动桌面客户端（另开终端）

```bash
cd desktop
pnpm install
pnpm run dev
```

## 手动方式（可选）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python agent_loop.py
```

## 项目结构

- `agent_loop.py`：终端版 Agent 主循环（LangGraph 条件循环）
- `context/`：模型可见上下文片段（环境、权限、模型、用户输入）
- `context_manager/`：模型历史上下文管理与截断 helpers
- `tools/core/`：工具协议、纯注册表、路由、执行器、默认目录和共享类型
- `tools/filesystem/`：读文件、写文件、按行编辑工具
- `tools/search/`：代码搜索工具（rg/grep）
- `tools/shell/`：命令工具（macOS 原生沙箱执行）
- `tools/network/`：可选联网工具
- `tools/interaction/`：模型主动澄清提问工具
- `security/`：filesystem policy、exec policy、路径校验和熔断器
- `sandbox/macos_executor.py`：macOS Seatbelt 安全执行器
- `server/app.py`：FastAPI WebSocket 服务（`/agent/ws`）
- `session/`：SQLite 会话索引、JSONL transcript、历史会话恢复和单轮 `TurnContext`
- `desktop/`：Electron + Vue + TypeScript 桌面客户端壳
- `pyproject.toml`：项目元信息与依赖（标准 Python 项目配置）
- `Makefile`：标准化开发命令入口

更完整的当前架构状态说明见：[`docs/PROJECT_ARCHITECTURE_STATUS.md`](docs/PROJECT_ARCHITECTURE_STATUS.md)

阶段四产品化路线见：[`docs/PRODUCTIZATION_ROADMAP.md`](docs/PRODUCTIZATION_ROADMAP.md)

workspace 与权限策略专题架构见：[`docs/WORKSPACE_PERMISSION_ARCHITECTURE.md`](docs/WORKSPACE_PERMISSION_ARCHITECTURE.md)

## 说明

- 模型调用统一配置 `API_KEY`，不要按服务商拆成 `OPENAI_API_KEY` / `DEEPSEEK_API_KEY`。
- `MODEL_PROVIDER` 标识服务商，`MODEL_NAME` 是主 Agent 的原始模型 id；`LOW_COST_MODEL_PROVIDER` / `LOW_COST_MODEL_NAME` 是标题生成、路由判断等简单任务使用的低成本模型配置，默认继承主模型。
- 桌面端会从后端读取模型配置；DeepSeek 会优先通过 `${API_BASE:-https://api.deepseek.com}/models` 动态拉取模型列表，成功时直接使用接口返回的模型 id，失败时只回退到 `MODEL_OPTIONS`。`REASONING_EFFORT=off|low|medium|high|max` 可设置默认思考程度。DeepSeek 模型会额外按官方 thinking 参数处理：`off` 显式关闭 thinking，`low/medium` 映射为 `high`，`xhigh/max` 映射为 `max`。
- 阶段 2 已将 `run_command` 切换到 macOS 原生沙箱执行，并增加 prefix exec policy、风险拦截、session allow 和工具元信息。
- macOS 沙箱当前使用 `sandbox-exec`/Seatbelt：命令在真实项目目录执行，默认禁止网络，并按 `FileSystemPolicy` 生成可读/可写根目录。
- Workspace protocol 已支持 `open_workspace`、`change_directory` 和 `add_dir`；额外目录必须显式加入，Python runtime 会刷新后续工具执行的 filesystem policy。
- 项目本地策略配置使用 trust gate：默认 `session_only` 不读取 `.codex-mini/config.toml`；只有用户侧 trust store 标记为 trusted 的项目才会读取受白名单约束的 permission / exec policy 配置。
- 恢复历史会话只接受当前 v2 workspace 快照：`selected_root`、`project_root`、`current_dir`、`additional_roots` 必须齐全且可重新验证；旧 `root` / `allowed_roots` 数据或失效路径会返回 `workspace_error`，不做静默修复。
- 旧本地会话数据不迁移；需要清理时显式运行 `make clean-sessions` 删除 `.codex-mini/sessions/`。
- 模型上下文会按 `project_root -> current_dir` 分层加载 `AGENTS.md`；如果当前目录在外部 additional root 内，不会越过原项目根去加载外部项目说明。
- 如果当前进程本身已经处在受限沙箱里，`sandbox-exec` 可能返回 `sandbox_apply: Operation not permitted`；正常终端/桌面应用运行环境下再做端到端验证。
- 会话运行态会写入 `.codex-mini/sessions/`：`index.sqlite` 保存会话索引，`transcripts/*.jsonl` 保存 append-only 事件流。
- 会话标题根据首条用户提问调用模型生成，并在返回前清理与截断。
- 桌面端当前是本地 runtime client，不是最终的产品官网或独立 Web 应用。

## 测试

```bash
.venv/bin/python -m unittest discover -s tests
```

## WebSocket 联调（wscat）

```bash
wscat -c ws://127.0.0.1:8000/agent/ws
```

连接后发送：

```json
{"type":"user_input","content":"列出当前目录下的 Python 文件"}
```

服务端会推送这些事件：

- `ready`
- `session_created`
- `sessions_list`
- `turn_started`
- `tool_call_started`
- `tool_call_result`
- `permission_request`
- `permission_decision_ack`
- `clarification_request`
- `clarification_response_ack`
- `session_suspended`
- `assistant_token`
- `final_answer`
- `conversation_title`

当收到 `permission_request` 后，客户端可以发送：

```json
{"type":"permission_decision","approved":true}
```

常用客户端控制事件：

```json
{"type":"list_sessions","limit":20}
{"type":"new_session"}
{"type":"resume_session","session_id":"已有 session id"}
{"type":"conversation_title_request","messages":[{"role":"user","content":"读取 README"}]}
```

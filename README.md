# 

# Xagent

一个用于学习和实习展示的 Codex 类本地 Agent 项目：Python runtime 负责模型循环、工具执行、安全策略、沙箱和会话持久化，Electron/Vue 桌面客户端负责聊天、审批、工具过程和历史会话体验。

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
- `tools/registry.py`：工具注册、schema 暴露、执行分发
- `tools/read_file.py`：读文件工具
- `tools/write_file.py`：写文件工具
- `tools/edit_file.py`：按行编辑工具
- `tools/grep.py`：代码搜索工具（rg/grep）
- `tools/run_command.py`：命令工具（macOS 原生沙箱执行）
- `security/`：路径校验、命令白名单、熔断器
- `sandbox/macos_executor.py`：macOS Seatbelt 安全执行器
- `server/app.py`：FastAPI WebSocket 服务（`/agent/ws`）
- `session/`：SQLite 会话索引、JSONL transcript、历史会话恢复
- `desktop/`：Electron + Vue + TypeScript 桌面客户端壳
- `pyproject.toml`：项目元信息与依赖（标准 Python 项目配置）
- `Makefile`：标准化开发命令入口

更完整的当前架构状态说明见：[`docs/PROJECT_ARCHITECTURE_STATUS.md`](docs/PROJECT_ARCHITECTURE_STATUS.md)

阶段四产品化路线见：[`docs/PRODUCTIZATION_ROADMAP.md`](docs/PRODUCTIZATION_ROADMAP.md)

workspace 与权限策略专题架构见：[`docs/WORKSPACE_PERMISSION_ARCHITECTURE.md`](docs/WORKSPACE_PERMISSION_ARCHITECTURE.md)

## 说明

- 如果使用 OpenAI：配置 `OPENAI_API_KEY`，并保持 `MODEL_PROVIDER=openai`。
- 如果使用 DeepSeek：配置 `DEEPSEEK_API_KEY`，并把 `MODEL_PROVIDER=deepseek`。
- 桌面端会从后端读取模型配置；`MODEL_OPTIONS` 可扩展输入栏旁边的模型下拉列表，`REASONING_EFFORT=off|low|medium|high` 可设置默认思考程度。
- 阶段 2 已将 `run_command` 切换到 macOS 原生沙箱执行，并增加命令白名单、风险拦截和工具元信息。
- macOS 沙箱当前使用 `sandbox-exec`/Seatbelt：命令在真实项目目录执行，默认禁止网络，只允许写项目目录和临时目录，并受命令策略限制。
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

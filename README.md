# Codex-mini

一个用于学习和实习展示的 Codex 类 Agent 项目（Python 版）。

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
- `tools/run_command.py`：命令工具（Docker 沙箱执行）
- `security/`：路径校验、命令白名单、熔断器
- `sandbox/docker_executor.py`：Docker 安全执行器
- `server/app.py`：FastAPI WebSocket 服务（`/agent/ws`）
- `pyproject.toml`：项目元信息与依赖（标准 Python 项目配置）
- `Makefile`：标准化开发命令入口

更完整的当前架构状态说明见：[`docs/PROJECT_ARCHITECTURE_STATUS.md`](docs/PROJECT_ARCHITECTURE_STATUS.md)

## 说明

- 如果使用 OpenAI：配置 `OPENAI_API_KEY`，并保持 `MODEL_PROVIDER=openai`。
- 如果使用 DeepSeek：配置 `DEEPSEEK_API_KEY`，并把 `MODEL_PROVIDER=deepseek`。
- 阶段 2 已将 `run_command` 切换到 Docker 执行，并增加命令白名单、风险拦截和工具元信息。
- Docker 当前将真实项目目录以读写方式挂载到容器 `/workspace`，因此容器内允许命令产生的文件改动会落到真实项目；网络默认禁用，并受命令策略限制。

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
- `turn_started`
- `tool_call_started`
- `tool_call_result`
- `permission_request`
- `permission_decision_ack`
- `session_suspended`
- `assistant_token`
- `final_answer`

当收到 `permission_request` 后，客户端可以发送：

```json
{"type":"permission_decision","approved":true}
```

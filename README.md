# Codex 类 Agent（阶段 0 + 阶段 1）

当前仓库已包含两部分能力：

- 阶段 0：Docker 本地沙箱执行器
- 阶段 1：`LangGraph + LiteLLM` 终端版最小 Agent 循环

## 快速开始

1. 创建并激活虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 配置环境变量：

```bash
cp .env.example .env
```

4. 运行阶段 1 Agent：

```bash
python agent_loop.py
```

## 目录结构

- `agent_loop.py`：终端版 Agent 主循环（LangGraph 条件循环）
- `tools/toolkit.py`：工具定义、参数校验、工具执行
- `sandbox/executor.py`：Docker 沙箱执行器（阶段 0）
- `main.py`：沙箱自检入口

## 说明

- 如果使用 OpenAI：配置 `OPENAI_API_KEY`，并保持 `MODEL_PROVIDER=openai`。
- 如果使用 DeepSeek：配置 `DEEPSEEK_API_KEY`，并把 `MODEL_PROVIDER=deepseek`。
- 阶段 1 的 `run_command` 先使用本地 `subprocess`；阶段 2 会切到 Docker 沙箱执行。

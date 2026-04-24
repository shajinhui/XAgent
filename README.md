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

3. 启动 Agent（阶段 1）

```bash
make run
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
- `tools/toolkit.py`：工具定义、参数校验、工具执行
- `pyproject.toml`：项目元信息与依赖（标准 Python 项目配置）
- `Makefile`：标准化开发命令入口

## 说明

- 如果使用 OpenAI：配置 `OPENAI_API_KEY`，并保持 `MODEL_PROVIDER=openai`。
- 如果使用 DeepSeek：配置 `DEEPSEEK_API_KEY`，并把 `MODEL_PROVIDER=deepseek`。
- 阶段 1 的 `run_command` 先使用本地 `subprocess`；阶段 2 会切到 Docker 沙箱执行。

# Codex 类 Agent（第 0 阶段初始化）

当前仓库已完成第 0 阶段基础搭建，包含：

- Python 项目脚手架
- 基于 Docker 的本地沙箱执行器
- 基础 Git 仓库初始化

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

4. 运行沙箱自检：

```bash
python main.py
```

## 目录结构

- `sandbox/executor.py`：Docker 沙箱命令执行器
- `main.py`：最小化运行入口（自检示例）

## 说明

- 需要先安装并启动 Docker Desktop。
- 命令会在隔离容器内执行，并包含超时与基础危险命令拦截。

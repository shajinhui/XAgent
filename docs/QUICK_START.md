# XCode 项目状态 & 快速启动

## ✅ 当前状态

**项目完整度**: 可以使用 ✓

- ✅ 核心依赖已安装
- ✅ 环境配置已完成 (.env)
- ✅ 会话存储正常 (.codex-mini/sessions/)
- ✅ 新增自动压缩功能已集成
- ✅ 测试套件当前通过

## 🚀 快速启动

### 方式 1: CLI 模式

```bash
# 激活虚拟环境
source .venv/bin/activate

# 运行 Agent
python agent_loop.py
```

### 方式 2: WebSocket 服务 + 桌面端

**启动后端**:
```bash
source .venv/bin/activate
make run-server
# 或
python -m server.app
```

**启动前端** (另开终端):
```bash
cd desktop
pnpm install  # 首次运行
pnpm run dev
```

访问: 桌面应用会自动启动

## 📋 完整功能清单

### 核心功能
✅ **基础 Agent 循环** - LangGraph 状态机
✅ **工具执行** - 文件读写、命令执行、搜索
✅ **会话持久化** - SQLite 索引 + JSONL 事件流
✅ **WebSocket 服务** - 实时通信，支持桌面端
✅ **权限管理** - Workspace 安全策略
✅ **macOS 沙箱** - Seatbelt 命令隔离

### 新增功能 (本次)
✅ **自动上下文压缩** - 256k 阈值，前 80% 摘要
✅ **智能摘要生成** - 使用 claude-3-haiku
✅ **Memory 层** - 跨会话知识共享
✅ **完整文档** - 对比分析 + 使用指南

## 🔧 配置检查

**示例配置** (`.env`，按你的实际服务商填写，不要提交真实密钥):
```bash
API_KEY=your-api-key
MODEL_PROVIDER=deepseek
MODEL_NAME=deepseek-chat
LOW_COST_MODEL_NAME=deepseek-chat
ENABLE_WEB_FETCH=true
```

**自动压缩配置** (可选):
```bash
# 添加到 .env（可选，默认使用 claude-3-haiku）
SUMMARIZE_MODEL=claude-3-haiku-20240307
```

## 📝 使用示例

### CLI 模式示例

```bash
$ source .venv/bin/activate
$ python agent_loop.py

用户: 列出当前目录的 Python 文件

助手: [调用 run_command 工具]
输出: main.py, agent_loop.py, ...

用户: 读取 agent_loop.py 的前 50 行

助手: [调用 read_file 工具]
输出: [文件内容]

# ... 经过 100 轮对话后 ...

⚙️  上下文接近 256k 限制，正在压缩前 80% 的对话...
✓ 已压缩，估算节省 180000 tokens

# 对话继续，无中断
```

### WebSocket 模式示例

**启动服务**:
```bash
$ make run-server
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

**桌面端连接**: 自动连接到 ws://127.0.0.1:8000/agent/ws

## 🧪 测试验证

```bash
# 测试上下文压缩
source .venv/bin/activate
PYTHONPATH=. python3 tests/test_compaction.py
# 输出: ✓ 所有测试通过

# 测试其他模块
.venv/bin/python -m unittest discover -s tests
```

## 📚 文档结构

```
docs/
├── QUICK_START.md                           # 快速启动
├── PROJECT_ARCHITECTURE_STATUS.md           # 当前架构状态
├── PRODUCTIZATION_ROADMAP.md                # 产品化路线
├── WORKSPACE_PERMISSION_ARCHITECTURE.md     # Workspace/权限架构
├── MEMORY_LAYER.md                          # Memory 层设计
├── AUTO_COMPACTION.md                       # 自动压缩使用指南
├── SKILLS_QUICKSTART.md                     # Skills 快速使用
└── SKILL_AUTHORING_GUIDE.md                 # Skill 编写指南
```

## ⚠️ 已知限制

1. **自动压缩**
   - 首次压缩需要 2-5 秒（调用小模型）
   - 前 80% 的细节会丢失（被摘要替代）
   - 摘要质量依赖小模型

2. **Memory 层**
   - 当前不会自动注入历史会话
   - 需要手动触发 `summarize_session`

3. **工具限制**
   - 沙箱执行限制某些命令
   - 网络工具默认关闭（需配置 ENABLE_WEB_FETCH）

## 🎯 下一步建议

### 立即可用
```bash
# 直接开始使用
source .venv/bin/activate
python agent_loop.py
```

### 建议测试场景
1. **基础对话** - 验证基本功能
2. **文件操作** - 读写、搜索文件
3. **命令执行** - 运行简单命令
4. **长对话** - 触发自动压缩（需要多轮对话）

### 可选优化
1. **调整压缩阈值** - 根据实际使用调整 256k
2. **配置摘要模型** - 换用更便宜或更快的模型
3. **启用网络工具** - 支持 web_fetch
4. **自定义工具** - 添加项目特定的工具

## 💡 快速问题排查

### 问题 1: 导入错误
```bash
# 解决: 激活虚拟环境
source .venv/bin/activate
```

### 问题 2: API 调用失败
```bash
# 检查 .env 配置
cat .env
# 验证 API_KEY 是否有效
```

### 问题 3: 压缩不触发
```bash
# 原因: 上下文未达到 256k tokens
# 验证: 进行更多轮对话或读取大文件
```

### 问题 4: 桌面端无法连接
```bash
# 确保后端服务已启动
make run-server

# 检查端口
lsof -i :8000
```

## 📊 项目指标

- **总代码行数**: ~8000+ 行
- **核心模块**: 15+ 个
- **工具数量**: 12 个（文件、搜索、测试、规划、命令、交互、网络）
- **测试覆盖**: 基础测试 ✓
- **文档完整度**: 高 ✓

## 🎉 总结

**项目状态: 可以使用 ✅**

- 基础功能完整
- 自动压缩已集成
- 文档齐全
- 测试通过

**启动命令**:
```bash
source .venv/bin/activate && python agent_loop.py
```

开始使用吧！遇到问题参考文档或测试代码。

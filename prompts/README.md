# 系统提示词模块

提供模板加载、提示词组装功能，用于构建 AI 代理的系统提示词。

## 目录结构

```
prompts/
├── __init__.py              # 模块导出
├── base.py                  # 模板加载器（编译时加载）
├── builder.py               # 提示词组装器
└── templates/               # Markdown 提示词模板
    ├── core_prompt.md       # 核心系统提示词
    ├── personality/         # 个性化模板
    │   ├── default.md       # 简洁、直接、友好
    │   ├── friendly.md      # 团队士气优先
    │   └── pragmatic.md     # 务实的工程师
    └── tools/               # 工具使用指引
        ├── shell.md         # Shell 命令规范
        ├── file_operations.md  # 文件操作规范
        └── git.md           # Git 操作规范
```

## 使用方法

### 基本使用

```python
from prompts import PromptBuilder, SystemPromptConfig

# 创建配置
config = SystemPromptConfig(
    personality="default",  # 可选: default/friendly/pragmatic
    include_tools=True      # 是否包含工具使用指引
)

# 构建提示词
builder = PromptBuilder()
system_prompt = builder.build(config)
```

### 集成到 agent_loop.py

```python
from prompts import PromptBuilder, SystemPromptConfig
from workspace.instructions import render_system_prompt_with_project_instructions
from workspace.models import WorkspaceContext

def build_system_message(workspace: WorkspaceContext) -> dict:
    """构建完整的系统消息，包含项目说明。"""
    # 1. 构建基础提示词
    config = SystemPromptConfig(personality="default", include_tools=True)
    builder = PromptBuilder()
    base_prompt = builder.build(config)
    
    # 2. 追加项目说明（AGENTS.md）
    final_prompt, _ = render_system_prompt_with_project_instructions(
        base_prompt, workspace
    )
    
    return {"role": "system", "content": final_prompt}
```

### 添加工作空间上下文

```python
# 可以在构建时添加工作空间上下文和项目说明
workspace_context = f"当前工作目录: {workspace.current_dir}"
project_instructions = "### 项目说明\n使用 TypeScript 编写代码"

system_prompt = builder.build(
    config,
    workspace_context=workspace_context,
    project_instructions=project_instructions
)
```

## 组装顺序

提示词按以下顺序组装：

1. **核心提示词** - 身份定义、能力说明、工作方式
2. **工具使用指引** - Shell、文件操作、Git 规范（可选）
3. **工作空间上下文** - 当前工作目录等信息
4. **项目说明** - AGENTS.md 文件内容

## 个性化模板

- `default`: 简洁、直接、友好的默认风格
- `friendly`: 优化团队士气，温暖鼓励的语气
- `pragmatic`: 深度务实的软件工程师风格

## 核心提示词内容

参考 Codex 的设计，包含：

- **身份定义**: 你是 XCode，运行在用户计算机上的编码代理
- **能力说明**: 接收提示词、调用工具、与用户沟通
- **工作方式**:
  - 响应性：工具调用前发送简短前言
  - 任务执行：自主解决查询，完成后再返回用户
  - 代码准则：从根本原因修复问题，避免不必要的复杂性
  - 验证工作：使用测试验证变更
- **输出格式**: 文件引用格式、Markdown 规范、语气要求
- **AGENTS.md 规范**: 优先级、作用域规则

## 设计原则

- **编译时加载**: 模板在模块导入时加载为常量，避免运行时 I/O
- **模块化**: 核心提示词、个性化、工具指引分离
- **可扩展**: 易于添加新的个性化模板或工具指引
- **清晰边界**: 提示词模块只负责提示词构建，不涉及工具执行或会话管理

## 测试

```bash
# 运行单元测试
.venv/bin/python -m unittest tests.test_prompts -v
```

测试覆盖：
- 模板加载
- 提示词组装
- 个性化替换
- 工具指引包含/排除
- 组装顺序验证

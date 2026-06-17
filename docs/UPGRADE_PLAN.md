# Codex-mini 升级实施计划

## 🎯 目标

**将 Codex-mini 从"编程助手"升级为"独立开发者"**

当前能力: 30% → 目标能力: 70% (MVP) → 最终: 85% (生产级)

---

## 📅 时间表

### 第一阶段: MVP (1 周)

**目标**: 能独立完成简单项目
**时间**: 2024-06-13 ~ 2024-06-19

### 第二阶段: 完整版 (2 周)

**目标**: 能完成中等复杂项目
**时间**: 2024-06-20 ~ 2024-07-03

### 第三阶段: 优化 (1 周)

**目标**: 稳定性和性能优化
**时间**: 2024-07-04 ~ 2024-07-10

**总计**: 4 周

---

## 第一阶段: MVP 实施计划

### Day 1: 文件浏览工具

**任务**: 实现 `list_files` 工具

**实现**:

```python
# tools/filesystem/list_files.py
from pathlib import Path
from typing import Optional
from tools.core.protocol import Tool

def execute(directory: str = ".", pattern: str = "**/*",
            max_files: int = 100) -> str:
    """列出目录下的文件

    Args:
        directory: 目录路径
        pattern: glob 模式 (如 **/*.py)
        max_files: 最多返回文件数
    """
    try:
        path = Path(directory)
        if not path.exists():
            return f"目录不存在: {directory}"

        files = [
            str(f.relative_to(directory))
            for f in path.glob(pattern)
            if f.is_file()
        ]

        if len(files) > max_files:
            return (
                f"找到 {len(files)} 个文件，只显示前 {max_files} 个：\n" +
                "\n".join(files[:max_files]) +
                f"\n\n... 还有 {len(files) - max_files} 个文件未显示"
            )

        return "\n".join(files) if files else "未找到文件"

    except Exception as e:
        return f"错误: {str(e)}"

TOOL = Tool(
    name="list_files",
    description="列出目录下的文件，支持 glob 模式匹配",
    input_schema={
        "type": "object",
        "properties": {
            "directory": {
                "type": "string",
                "description": "目录路径，默认当前目录"
            },
            "pattern": {
                "type": "string",
                "description": "文件匹配模式，如 **/*.py, src/**/*.ts"
            },
            "max_files": {
                "type": "integer",
                "description": "最多返回文件数，默认 100"
            }
        }
    },
    execute=execute
)
```

**集成**:

```python
# tools/core/catalog.py
from tools.filesystem.list_files import TOOL as list_files_tool

def builtin_tools() -> list[Tool]:
    return [
        read_file_tool,
        write_file_tool,
        edit_file_tool,
        list_files_tool,  # 新增
        # ...
    ]
```

**测试**:

```python
# tests/test_list_files.py
def test_list_files():
    result = list_files_tool.execute(directory=".", pattern="*.py")
    assert "agent_loop.py" in result

def test_list_files_glob():
    result = list_files_tool.execute(pattern="**/*.md")
    assert "README.md" in result
```

**验收**: 运行测试通过，Agent 能列出项目文件

---

### Day 2: 目录树工具

**任务**: 实现 `directory_tree` 工具

**实现**:

```python
# tools/filesystem/tree.py
from pathlib import Path

def execute(directory: str = ".", max_depth: int = 3,
            show_hidden: bool = False) -> str:
    """生成目录树

    Args:
        directory: 根目录
        max_depth: 最大深度
        show_hidden: 是否显示隐藏文件
    """
    try:
        path = Path(directory)
        lines = [str(path) + "/"]
        _build_tree(path, "", max_depth, show_hidden, lines)
        return "\n".join(lines[:200])  # 限制行数
    except Exception as e:
        return f"错误: {str(e)}"

def _build_tree(path: Path, prefix: str, depth: int,
                show_hidden: bool, lines: list):
    if depth <= 0:
        return

    try:
        entries = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        if not show_hidden:
            entries = [e for e in entries if not e.name.startswith(".")]

        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            current = "└── " if is_last else "├── "
            lines.append(prefix + current + entry.name)

            if entry.is_dir():
                extension = "    " if is_last else "│   "
                _build_tree(entry, prefix + extension, depth - 1,
                           show_hidden, lines)
    except PermissionError:
        pass

TOOL = Tool(
    name="directory_tree",
    description="生成目录树结构，快速查看项目布局",
    input_schema={
        "type": "object",
        "properties": {
            "directory": {"type": "string"},
            "max_depth": {"type": "integer"},
            "show_hidden": {"type": "boolean"}
        }
    },
    execute=execute
)
```

**验收**: Agent 能显示项目结构树

---

### Day 3: 测试工具

**任务**: 实现 `run_tests` 工具

**实现**:

```python
# tools/testing/run_tests.py
import subprocess
import re
from dataclasses import dataclass

@dataclass
class TestResult:
    passed: int
    failed: int
    errors: list[str]
    output: str
    success: bool

def execute(pattern: str = "", verbose: bool = False) -> str:
    """运行测试并解析结果

    Args:
        pattern: 测试文件模式，如 tests/test_*.py
        verbose: 是否显示详细输出
    """
    try:
        cmd = ["python", "-m", "unittest", "discover", "-s", "tests"]
        if pattern:
            cmd.extend(["-p", pattern])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        output = result.stdout + result.stderr
        parsed = _parse_unittest_output(output)

        summary = [
            f"测试结果: {'✓ 通过' if parsed.success else '✗ 失败'}",
            f"通过: {parsed.passed}",
            f"失败: {parsed.failed}",
        ]

        if parsed.errors:
            summary.append(f"\n错误详情:")
            for error in parsed.errors[:3]:  # 只显示前 3 个
                summary.append(f"  - {error}")

        if verbose:
            summary.append(f"\n完整输出:\n{output}")

        return "\n".join(summary)

    except subprocess.TimeoutExpired:
        return "错误: 测试超时 (30秒)"
    except Exception as e:
        return f"错误: {str(e)}"

def _parse_unittest_output(output: str) -> TestResult:
    """解析 unittest 输出"""
    # 匹配 "Ran 5 tests"
    ran_match = re.search(r"Ran (\d+) test", output)
    total = int(ran_match.group(1)) if ran_match else 0

    # 匹配 "FAILED (failures=2)"
    failed_match = re.search(r"failures?=(\d+)", output)
    failed = int(failed_match.group(1)) if failed_match else 0

    # 提取错误信息
    errors = re.findall(r"(FAIL|ERROR): (.*)", output)
    error_msgs = [f"{t}: {msg}" for t, msg in errors]

    success = "OK" in output or (total > 0 and failed == 0)
    passed = total - failed

    return TestResult(
        passed=passed,
        failed=failed,
        errors=error_msgs,
        output=output,
        success=success
    )

TOOL = Tool(
    name="run_tests",
    description="运行测试并解析结果，自动验证代码正确性",
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "verbose": {"type": "boolean"}
        }
    },
    execute=execute
)
```

**验收**: Agent 能运行测试并理解结果

---

### Day 4-5: 任务规划 (简单版)

**任务**: 实现 TaskList 工具

**实现**:

```python
# tools/planning/task_list.py
import json
from pathlib import Path
from typing import Optional

class TaskList:
    def __init__(self, storage_path: str = ".codex-mini/tasks.json"):
        self.path = Path(storage_path)
        self.tasks = self._load()

    def _load(self) -> list[dict]:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.tasks, indent=2))

    def create(self, goal: str) -> str:
        """创建任务列表"""
        # 让模型分解任务（使用 LiteLLM）
        tasks = self._decompose_with_model(goal)
        self.tasks = tasks
        self._save()
        return self.format()

    def _decompose_with_model(self, goal: str) -> list[dict]:
        """使用模型分解任务"""
        import litellm

        response = litellm.completion(
            model="gpt-4o-mini",  # 用小模型
            messages=[{
                "role": "user",
                "content": f"""将以下目标分解为 3-8 个具体的子任务：

目标: {goal}

要求:
1. 每个任务要具体可执行
2. 按逻辑顺序排列
3. 只返回 JSON 数组格式
4. 格式: [{{"id": 1, "title": "任务标题", "description": "详细描述", "status": "pending"}}]

只返回 JSON，不要其他文字。"""
            }],
            max_tokens=500,
            temperature=0
        )

        content = response.choices[0].message.content
        # 提取 JSON
        import re
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return []

    def mark_done(self, task_id: int) -> str:
        """标记任务完成"""
        for task in self.tasks:
            if task["id"] == task_id:
                task["status"] = "done"
                self._save()
                return f"✓ 任务 {task_id} 已完成"
        return f"任务 {task_id} 不存在"

    def format(self) -> str:
        """格式化显示"""
        if not self.tasks:
            return "任务列表为空"

        lines = ["任务列表:\n"]
        for task in self.tasks:
            status = "✓" if task["status"] == "done" else "□"
            lines.append(f"{status} {task['id']}. {task['title']}")
            if task.get("description"):
                lines.append(f"   {task['description']}")
        return "\n".join(lines)

# 全局实例
_task_list = TaskList()

def create_task_list(goal: str) -> str:
    """创建任务列表"""
    return _task_list.create(goal)

def mark_task_done(task_id: int) -> str:
    """完成任务"""
    return _task_list.mark_done(task_id)

def show_tasks() -> str:
    """显示任务"""
    return _task_list.format()

# 注册工具
CREATE_TOOL = Tool(
    name="create_task_list",
    description="将目标分解为任务列表，用于规划复杂工作",
    input_schema={
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "要完成的目标"}
        },
        "required": ["goal"]
    },
    execute=create_task_list
)

MARK_TOOL = Tool(
    name="mark_task_done",
    description="标记任务为完成",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "integer"}
        },
        "required": ["task_id"]
    },
    execute=mark_task_done
)

SHOW_TOOL = Tool(
    name="show_tasks",
    description="显示当前任务列表",
    input_schema={"type": "object", "properties": {}},
    execute=show_tasks
)
```

**验收**: Agent 能分解任务并逐步执行

---

### Day 6-7: 集成测试 & Bug 修复

**任务**:
1. 端到端测试所有新工具
2. 修复发现的 bug
3. 性能优化
4. 文档更新

**测试场景**:

```python
# tests/test_integration.py
def test_complete_workflow():
    """测试完整工作流"""

    # 1. 创建任务
    result = create_task_list("实现一个简单的计算器")
    assert "任务列表" in result

    # 2. 列出文件
    files = list_files(directory=".", pattern="*.py")
    assert len(files) > 0

    # 3. 运行测试
    test_result = run_tests()
    assert "测试结果" in test_result

    # 4. 标记完成
    done = mark_task_done(1)
    assert "已完成" in done
```

---

## 第二阶段: 完整版实施计划

### Week 2: Git 工具集

**Day 8-9**: 实现 Git 工具

```python
# tools/git/operations.py

def git_status() -> str:
    """查看状态"""

def git_diff(file: str = "") -> str:
    """查看修改"""

def git_commit(message: str, files: list = None) -> str:
    """提交代码"""

def git_branch(name: str = "", create: bool = False) -> str:
    """分支操作"""
```

---

### Week 2: 错误恢复机制

**Day 10-11**: 实现自动重试

```python
# agent_loop.py 增强

def call_tools_with_retry(state: AgentState, runner: ToolRunner,
                          max_retries: int = 2) -> AgentState:
    """执行工具，失败时自动重试"""

    for tool_call in last_message["tool_calls"]:
        for retry in range(max_retries + 1):
            result = runner.execute(...)

            if result.ok:
                break

            if retry < max_retries:
                # 分析错误并生成修复提示
                error_analysis = analyze_error(result.content)
                # 注入修复提示到下一轮
                inject_error_context(state, error_analysis)
                continue

            # 最终失败
            result.content = f"[ERROR after {max_retries} retries] {result.content}"
```

---

### Week 3: 代码分析工具

**Day 12-13**: 实现代码分析

```python
# tools/analysis/analyze.py

def analyze_imports(file: str) -> str:
    """分析导入"""

def find_references(symbol: str) -> str:
    """查找引用"""

def get_definitions(file: str) -> str:
    """获取定义"""
```

---

### Week 3: 批量操作 & 其他工具

**Day 14-15**: 实现批量操作

```python
# tools/filesystem/batch.py

def batch_edit(files: list[str], operation: str) -> str:
    """批量修改"""

# tools/dependencies/manage.py

def install_package(name: str) -> str:
    """安装包"""
```

---

## 第三阶段: 优化

### Week 4: 稳定性优化

**Day 16-18**:
1. 性能分析与优化
2. 工具执行超时处理
3. 内存使用优化
4. 并发工具调用

**Day 19-20**:
1. 完整的端到端测试
2. 真实项目验证
3. 文档完善
4. 发布 v1.0

---

## 📊 验收标准

### MVP 验收 (Week 1 结束)

**测试任务**: "实现一个简单的 TODO 应用"

**Agent 应该能**:
```
1. 列出项目文件 ✓
2. 创建任务列表 ✓
3. 创建 todo.py ✓
4. 编写测试 test_todo.py ✓
5. 运行测试验证 ✓
6. 标记任务完成 ✓
```

**指标**:
- 人工干预次数: < 3 次
- 成功率: > 80%
- 完成时间: < 30 分钟

---

### 完整版验收 (Week 3 结束)

**测试任务**: "实现一个 REST API (用户 CRUD)"

**Agent 应该能**:
```
1. 规划项目结构
2. 创建多个文件 (models, routes, tests)
3. 实现完整功能
4. 运行测试
5. 自动修复错误
6. Git 提交代码
```

**指标**:
- 人工干预次数: < 5 次
- 成功率: > 70%
- 完成时间: < 1 小时

---

## 🚀 资源需求

### 开发资源

- **人力**: 1 名全职开发者
- **时间**: 4 周
- **环境**: Python 3.13, macOS

### 测试资源

- **单元测试**: 每个工具配套
- **集成测试**: 端到端场景
- **真实项目**: 2-3 个验证项目

### API 成本

- **开发期间**: ~$50 (主要是任务分解用小模型)
- **测试期间**: ~$100

---

## 📝 交付物

### Week 1 (MVP)
- ✓ list_files 工具
- ✓ directory_tree 工具
- ✓ run_tests 工具
- ✓ TaskList 工具
- ✓ 单元测试
- ✓ MVP 文档

### Week 3 (完整版)
- ✓ Git 工具集
- ✓ 错误恢复机制
- ✓ 代码分析工具
- ✓ 批量操作工具
- ✓ 完整测试套件
- ✓ 使用文档

### Week 4 (优化)
- ✓ 性能优化报告
- ✓ 端到端测试报告
- ✓ 完整 API 文档
- ✓ v1.0 Release

---

## ⚠️ 风险与应对

### 风险 1: 任务分解质量不高

**应对**:
- 使用更好的提示词
- 添加示例学习
- 允许人工调整任务

### 风险 2: 工具集成复杂

**应对**:
- 优先简单可用
- 逐步增强功能
- 充分测试

### 风险 3: 性能问题

**应对**:
- 限制返回数据量
- 添加超时机制
- 异步执行优化

---

## 📈 进度追踪

```bash
# 每日检查
make test              # 运行测试
make lint              # 代码检查
git log --oneline      # 提交记录

# 每周里程碑
Week 1: MVP Demo
Week 2: Git + 错误恢复
Week 3: 完整功能
Week 4: 优化发布
```

---

## 🎉 预期成果

**4 周后**:

```
Codex-mini v1.0
├── 能力: 30% → 70%
├── 工具: 6 → 15+
├── 场景: 简单任务 → 中等项目
└── 定位: 编程助手 → 独立开发者
```

**示例**:

```bash
用户: "实现一个用户认证 API"

Agent:
1. 创建任务列表 (5个任务)
2. 列出项目文件
3. 创建 models/user.py
4. 创建 routes/auth.py
5. 创建 tests/test_auth.py
6. 运行测试
7. (测试失败) 自动分析错误
8. 修复代码
9. 再次运行测试 ✓
10. 提交到 Git

完成！只需要你在关键决策点确认。
```

---

开始执行吧！💪

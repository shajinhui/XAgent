# Codex-mini 缺失能力清单

## 🎯 关键缺失 (阻碍完成项目)

### 1. 文件浏览工具 ⭐⭐⭐⭐⭐

**当前状态**: ✅ 已有第一版 `list_files` 和 `file_search`
**剩余影响**: 还缺目录树、批量浏览和更智能的项目结构摘要

**需要**:
```python
# tools/filesystem/tree.py
def directory_tree(path: str = ".", max_depth: int = 3) -> str:
    """生成目录树"""
```

**工作量**: 0.5-1 天
**优先级**: P1

---

### 2. 任务规划能力 ⭐⭐⭐⭐⭐

**当前状态**: ⚠️ 已有第一版 `update_plan` / `create_task_list`
**剩余影响**: 还不是完整 Plan 模式，缺少模式切换、计划审批和更强的执行约束

**需要**:
```python
# Plan 模式
用户: /plan "实现用户认证"

Agent 生成:
□ 1. 创建 User 模型
□ 2. 实现注册接口
□ 3. 实现登录接口
□ 4. 添加 JWT 验证
□ 5. 编写测试用例

然后逐步执行每个任务
```

**实现方式**:
- 简单版: `TaskList` 工具手动管理
- 完整版: `Plan` 模式自动分解

**工作量**: 2-3 天
**优先级**: P0

---

### 3. 测试工具 ⭐⭐⭐⭐

**当前状态**: ⚠️ 已有第一版 `run_tests`，能运行并摘要 pytest/unittest 结果
**剩余影响**: 已接入沙箱/审批链路，但框架识别和失败解析仍较粗

**需要**:
```python
# tools/testing/run_tests.py
def run_tests(pattern: str = "") -> TestResult:
    """
    运行测试并解析结果

    Returns:
        TestResult(
            passed=5,
            failed=2,
            errors=[...],
            duration=1.2
        )
    """
```

**工作量**: 1 天
**优先级**: P0

---

### 4. Git 工具集 ⭐⭐⭐⭐

**当前状态**: ❌ 只能用 `run_command git ...`
**影响**: 无法高效管理代码

**需要**:
```python
# tools/git/status.py
def git_status() -> dict:
    """查看状态"""

# tools/git/diff.py
def git_diff(file: str = "") -> str:
    """查看修改"""

# tools/git/commit.py
def git_commit(message: str, files: list[str] = None) -> str:
    """提交代码"""

# tools/git/branch.py
def git_branch(name: str = "", create: bool = False) -> str:
    """分支操作"""
```

**工作量**: 1 天
**优先级**: P1

---

### 5. 错误恢复机制 ⭐⭐⭐⭐

**当前状态**: ❌ 遇到错误就停止
**影响**: 需要人工介入

**需要**:
```python
# 自动重试机制
try:
    result = tool.execute()
except ToolError as e:
    # 1. 分析错误
    # 2. 生成修复方案
    # 3. 自动重试
    retry_with_fix()
```

**工作量**: 2 天
**优先级**: P1

---

## 🔧 次要缺失 (影响效率)

### 6. 代码分析工具 ⭐⭐⭐

```python
def analyze_imports(file: str) -> list[str]:
    """分析导入依赖"""

def find_references(symbol: str) -> list[str]:
    """查找符号引用"""

def get_definitions(file: str) -> list[str]:
    """获取定义列表"""
```

**工作量**: 2 天
**优先级**: P2

---

### 7. 依赖管理 ⭐⭐⭐

```python
def install_package(name: str) -> str:
    """安装 Python 包"""

def list_dependencies() -> list[str]:
    """列出依赖"""

def check_outdated() -> list[str]:
    """检查过期包"""
```

**工作量**: 0.5 天
**优先级**: P2

---

### 8. 批量操作 ⭐⭐⭐

```python
def batch_edit(files: list[str], operation: str) -> str:
    """批量修改文件"""

def refactor_rename(old: str, new: str) -> str:
    """重命名符号"""
```

**工作量**: 1 天
**优先级**: P2

---

### 9. 数据库工具 ⭐⭐

```python
def db_query(sql: str) -> list[dict]:
    """执行 SQL 查询"""

def db_migrate() -> str:
    """运行迁移"""
```

**工作量**: 1 天
**优先级**: P3

---

### 10. 文档生成 ⭐⭐

```python
def generate_docstring(function: str) -> str:
    """生成文档字符串"""

def generate_readme(project_path: str) -> str:
    """生成 README"""
```

**工作量**: 1 天
**优先级**: P3

---

## 📊 优先级总结

### 已完成第一版

```
1. list_files / file_search  ⭐⭐⭐⭐⭐
2. update_plan / create_task_list  ⭐⭐⭐⭐⭐
3. run_tests  ⭐⭐⭐⭐
```

当前能力已从基础工具阶段推进到“可浏览、可测试、可追踪轻量任务”的阶段。

### 立即需要 (P0) - 1 周

```
1. Git 工具集         [1 天] ⭐⭐⭐⭐
2. 错误恢复           [2 天] ⭐⭐⭐⭐
3. Plan 模式产品化     [3 天] ⭐⭐⭐⭐⭐

总计: 6 天
```

完成后能力提升: **约 60% → 75%**

### 近期需要 (P1) - 1 周

```
6. 代码分析工具       [2 天] ⭐⭐⭐
7. 依赖管理           [0.5 天] ⭐⭐⭐
8. 批量操作           [1 天] ⭐⭐⭐

总计: 3.5 天
```

完成后能力提升: **75% → 85%**

### 可选 (P2-P3) - 按需

```
9. 数据库工具         [1 天]
10. 文档生成          [1 天]
其他专用工具...
```

---

## 🎯 最小可行方案 (MVP)

**目标**: 能独立完成简单项目

**只需添加 3 个工具**:

```python
1. list_files()      # 浏览项目
2. run_tests()       # 验证代码
3. plan_task()       # 分解任务
```

**实现时间**: 3-4 天
**能力提升**: 30% → 60%

**效果**:
```
之前: "写一个用户注册功能"
     → 需要你逐步指导每个文件

之后: "写一个用户注册功能"
     → Agent 自己:
       1. 列出项目文件
       2. 分解为 5 个子任务
       3. 逐个实现
       4. 运行测试验证
     → 只需要你在关键决策点确认
```

---

## 🚀 完整路线图

### 阶段 1: 基础完善 (当前 → MVP)

**时间**: 1 周
**目标**: 能完成简单项目

```
✓ 上下文压缩 (已完成)
□ list_files
□ run_tests
□ 简单任务规划
```

### 阶段 2: 能力扩展 (MVP → 中级)

**时间**: 2 周
**目标**: 能完成中等项目

```
□ Git 工具集
□ 错误恢复
□ 代码分析
□ 批量操作
```

### 阶段 3: 高级特性 (中级 → 高级)

**时间**: 1 月
**目标**: 接近生产级

```
□ Plan 模式完整实现
□ Multi-agent 协作
□ 智能调试
□ 性能优化
```

---

## 💡 实现建议

### 快速起步

**第一步**: 先实现 `list_files`

```python
# tools/filesystem/list_files.py
from pathlib import Path
from tools.core.protocol import Tool

def execute(directory: str = ".", pattern: str = "**/*") -> str:
    path = Path(directory)
    files = [str(f) for f in path.glob(pattern) if f.is_file()]
    return "\n".join(files[:100])  # 限制返回数量

TOOL = Tool(
    name="list_files",
    description="列出目录下的文件",
    input_schema={
        "type": "object",
        "properties": {
            "directory": {"type": "string"},
            "pattern": {"type": "string"}
        }
    },
    execute=execute
)
```

**第二步**: 添加简单的任务规划

```python
# tools/planning/task_list.py
def create_task_list(goal: str) -> str:
    """让模型生成任务列表"""
    # 1. 让模型分解任务
    # 2. 保存到 .codex-mini/tasks.json
    # 3. 逐个执行
```

**第三步**: 实现测试工具

```python
# tools/testing/run_tests.py
def run(ctx, payload: dict) -> str:
    """通过当前 command executor 和 sandbox policy 运行测试。"""
    command = build_test_command(payload)
    result = ctx.command_executor.run(
        command,
        filesystem_policy=ctx.filesystem_policy,
        network_policy=ctx.network_policy,
        cwd=ctx.current_dir,
        sandbox_enabled=True,
    )
    return summarize_test_output(result.stdout + result.stderr)
```

---

## 📝 总结

**当前缺失的核心能力**:

1. ⚠️ 文件浏览 - 已有第一版，缺目录树和结构摘要
2. ⚠️ 任务规划 - 已有第一版，缺完整 Plan 模式
3. ⚠️ 测试验证 - 已有第一版，解析仍需增强
4. ❌ Git 集成 - 版本管理能力弱
5. ❌ 错误恢复 - 遇错就停

**最快提升方案**:

添加前 3 个工具 (1 周) → 能力从 30% 提升到 60%

**完整升级**:

全部实现 (1-2 月) → 能力达到 85%，接近生产级

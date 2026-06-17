# Day 4-5 实施计划：TaskList 工具

**目标**: 实现轻量级任务追踪工具（类似 Codex 的 update_plan）

**时间**: 2 天 (10 小时)

**能力提升**: 60% → 70% (达到 MVP)

---

## 📋 任务分解

### Day 4 (6小时)
1. **核心 TaskList 类** (2h) - 任务存储、加载、格式化
2. **工具函数封装** (1h) - update_plan 工具接口
3. **自动分解功能** (2h) - 用小模型分解目标
4. **基础测试** (1h) - 单元测试

### Day 5 (4小时)
5. **工具注册集成** (1h) - 集成到工具目录
6. **完整测试** (2h) - 端到端测试
7. **文档编写** (1h) - 使用文档

---

## 🎯 核心功能

### TaskList 类
```python
class TaskList:
    def update(self, tasks: list[dict]) -> str:
        """更新任务列表，返回格式化显示"""

    def _format(self) -> str:
        """格式化: ✓/⋯/○ + 进度统计"""

    def get_current_task(self) -> dict:
        """获取当前任务"""
```

### update_plan 工具
```python
def run(explanation: str, plan: list[dict]) -> str:
    """
    更新任务计划

    plan: [
        {"step": "创建模型", "status": "pending"},
        {"step": "写测试", "status": "in_progress"}
    ]
    """
```

### create_task_list 工具
```python
async def create_run(goal: str) -> str:
    """自动分解目标为任务列表"""
    tasks = await decompose_goal(goal)  # 用小模型
    return update_plan(plan=tasks)
```

---

## 📊 验收标准

### 功能
- ✅ 创建/更新任务列表
- ✅ 持久化存储 (JSON)
- ✅ 格式化显示 (✓⋯○)
- ✅ 进度统计
- ✅ 自动分解目标

### 测试
- ✅ 10+ 单元测试
- ✅ 3+ 集成测试
- ✅ 覆盖率 > 80%

### 效果
- ✅ Agent 能自动分解任务
- ✅ Agent 能追踪进度
- ✅ 能完成简单项目

---

## 📦 交付物

### 代码 (~600 行)
- `tools/planning/task_list.py` (300 行)
- `tools/planning/decomposer.py` (150 行)
- `tools/planning/__init__.py` (10 行)
- `tests/test_task_list.py` (100 行)
- `tests/test_task_integration.py` (50 行)

### 文档
- `docs/progress/DAY4-5_COMPLETE.md`
- `docs/TASK_LIST_USAGE.md`

---

## 🚀 执行步骤

```bash
# 1. 创建文件
mkdir -p tools/planning
touch tools/planning/{__init__,task_list,decomposer}.py

# 2. Day 4: 实现核心功能
#    - TaskList 类
#    - update_plan 工具
#    - 自动分解
#    - 基础测试

# 3. Day 5: 集成测试
#    - 注册工具
#    - 端到端测试
#    - 文档

# 4. 验证
PYTHONPATH=. python3 -m unittest discover -s tests -p "test_task*.py"
```

---

## 📈 预期成果

**工具数量**: 10 → 12
**能力评估**: 60% → 70%
**里程碑**: ✅ MVP 达成

**实际效果**:
```
用户: "实现 TODO 应用"

Agent:
1. create_task_list("实现 TODO 应用")
2. update_plan(任务1 = in_progress)
3. write_file(...)
4. update_plan(任务1 = completed, 任务2 = in_progress)
5. ...

✅ 自动分解 → 逐步执行 → 追踪进度
```

---

**准备开始 Day 4 任务 1 吗？** 🚀

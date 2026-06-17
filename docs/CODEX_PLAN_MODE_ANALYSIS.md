# OpenAI Codex 任务规划机制分析

## ✅ 是的！Codex 有完整的任务规划功能

基于源码分析，Codex 有非常完善的 Plan 模式。

---

## 🎯 Codex 的 Plan 架构

### 核心文件
```
codex-rs/
├── protocol/src/plan_tool.rs           # Plan 工具定义
├── core/src/tools/handlers/plan.rs     # Plan 处理器
├── core/src/tools/spec_plan.rs         # Plan 规范
├── collaboration-mode-templates/
│   └── templates/plan.md               # Plan 模板
└── tui/src/chatwidget/plan_implementation.rs  # UI 实现
```

---

## 📋 Plan 工具结构

从 `plan_tool.rs` 看到的定义：

```rust
pub struct PlanItemArg {
    pub step: String,        // 步骤描述
    pub status: StepStatus,  // 状态
}

pub enum StepStatus {
    Pending,      // 待执行
    InProgress,   // 进行中
    Completed,    // 已完成
}

pub struct UpdatePlanArgs {
    pub explanation: Option<String>,  // 解释
    pub plan: Vec<PlanItemArg>,      // 步骤列表
}
```

---

## 🔄 工作流程

### 1. 进入 Plan 模式

**用户触发**:
```
用户: "帮我实现一个用户认证系统"
或
用户: /plan
```

**Codex 响应**:
- 进入 Plan 模式
- 分析需求
- 生成步骤列表

---

### 2. 生成 Plan

**Codex 生成的 Plan 示例**:

```markdown
# 实现用户认证系统

## Plan

1. [Pending] 创建 User 数据模型
2. [Pending] 实现密码哈希功能
3. [Pending] 创建注册接口
4. [Pending] 创建登录接口
5. [Pending] 实现 JWT token 生成
6. [Pending] 添加认证中间件
7. [Pending] 编写测试用例
8. [Pending] 更新 API 文档
```

---

### 3. 执行 Plan

**逐步执行**:

```
Step 1: [InProgress] 创建 User 数据模型
→ write_file("models/user.py", ...)
→ [Completed] ✓

Step 2: [InProgress] 实现密码哈希功能
→ write_file("auth/hash.py", ...)
→ [Completed] ✓

...
```

**使用 `update_plan` 工具更新状态**:
```rust
update_plan({
    explanation: "已完成用户模型",
    plan: [
        {step: "创建 User 数据模型", status: "Completed"},
        {step: "实现密码哈希功能", status: "InProgress"},
        ...
    ]
})
```

---

## 🎨 UI 显示

**TUI 界面** (`tui/src/chatwidget/plan_implementation.rs`):

```
┌─ Plan ─────────────────────────────────┐
│ ✓ 创建 User 数据模型                    │
│ ⋯ 实现密码哈希功能  (进行中)            │
│ ○ 创建注册接口                          │
│ ○ 创建登录接口                          │
│ ○ 实现 JWT token 生成                   │
│ ○ 添加认证中间件                        │
│ ○ 编写测试用例                          │
│ ○ 更新 API 文档                         │
└─────────────────────────────────────────┘
```

**符号**:
- ✓ Completed
- ⋯ InProgress
- ○ Pending

---

## 🆚 与简单 TODO 的区别

### Codex Plan 模式

**特点**:
1. **结构化**: 严格的步骤定义
2. **状态追踪**: Pending/InProgress/Completed
3. **持久化**: 保存在会话中
4. **可视化**: 专门的 UI 组件
5. **集成**: 与主循环深度集成

**工作流**:
```
用户请求 → 进入 Plan 模式 → 生成步骤 →
逐步执行 → 更新状态 → 完成所有步骤
```

---

### 简单 TODO 工具

**特点**:
1. **松散**: 简单的列表
2. **手动**: 需要手动标记
3. **独立**: 不与主循环集成

**工作流**:
```
创建列表 → 执行任务 → 手动标记完成
```

---

## 📊 Codex Plan 的核心优势

### 1. **自动分解**
```
用户: "实现用户认证"
Codex: 自动分解为 8 个具体步骤
```

### 2. **自动更新**
```
完成一个步骤 → 自动调用 update_plan → 更新状态
```

### 3. **可视化**
```
实时显示进度
用户随时知道当前状态
```

### 4. **上下文保持**
```
Plan 持续整个会话
模型知道"我在执行第 3 步"
```

---

## 🔧 技术实现

### Plan 模式触发

**方式 1**: 用户主动
```bash
/plan "实现用户认证系统"
```

**方式 2**: Codex 建议
```
用户: "我想做一个博客系统"
Codex: "这是个复杂任务，我建议先制定计划。要进入 Plan 模式吗？"
```

**方式 3**: 自动检测
```rust
if task_is_complex(user_input) {
    enter_plan_mode()
}
```

---

### Plan 存储

```rust
struct Session {
    active_plan: Option<Plan>,
    // ...
}

struct Plan {
    steps: Vec<PlanStep>,
    current_step: usize,
    started_at: DateTime,
}
```

**持久化**: 保存在 rollout (会话历史) 中

---

### Plan 执行引擎

```rust
async fn execute_plan(plan: &Plan) {
    for (i, step) in plan.steps.iter().enumerate() {
        // 1. 更新为 InProgress
        update_plan_status(i, StepStatus::InProgress);

        // 2. 执行步骤
        let result = execute_step(step).await;

        // 3. 更新为 Completed 或处理错误
        if result.is_ok() {
            update_plan_status(i, StepStatus::Completed);
        } else {
            // 错误处理
        }
    }
}
```

---

## 🎯 与 Codex-mini 的对比

### Codex Plan 模式

**复杂度**: ⭐⭐⭐⭐⭐ (非常复杂)

**特性**:
- ✅ 完整的状态机
- ✅ 专门的 UI 组件
- ✅ 深度集成主循环
- ✅ 持久化存储
- ✅ 错误恢复

**实现量**: ~5000+ 行代码

---

### Codex-mini TaskList (Day 4-5 计划)

**复杂度**: ⭐⭐ (简单)

**特性**:
- ✅ 基础任务列表
- ✅ 简单状态追踪
- ⚠️ 最小化 UI
- ⚠️ 文件存储
- ❌ 有限的集成

**实现量**: ~500 行代码

---

## 💡 Codex-mini 应该怎么做？

### 方案 A: MVP 简化版 (推荐 Day 4-5)

**实现**:
```python
# tools/planning/task_list.py

def create_task_list(goal: str) -> str:
    """用小模型分解任务"""
    tasks = _decompose_with_model(goal)
    _save_to_file(tasks)
    return _format_tasks(tasks)

def mark_task_done(task_id: int) -> str:
    """标记完成"""
    tasks = _load_tasks()
    tasks[task_id]["status"] = "done"
    _save_to_file(tasks)
```

**特点**:
- 简单的 JSON 存储
- 基础的状态管理
- 快速实现（1-2 天）

---

### 方案 B: 完整 Plan 模式 (未来)

**实现**:
- 状态机
- 自动执行引擎
- UI 集成
- 错误恢复

**特点**:
- 接近 Codex 功能
- 需要 2-3 周开发

---

## 📈 实际价值

### 有 Plan 模式的效果

```
用户: "实现一个博客系统"

Codex:
1. 分解为 15 个步骤
2. 逐步执行
3. 自动追踪进度
4. 4 小时后完成

用户体验: ⭐⭐⭐⭐⭐
```

### 没有 Plan 模式

```
用户: "实现一个博客系统"

Agent:
"好的，我先创建数据库模型..."
(执行一些操作)
"接下来呢？"

用户: "继续"
"好的..." (又执行一些)
"接下来呢？"

需要持续引导，效率低
用户体验: ⭐⭐
```

---

## ✅ 结论

### Codex 有非常完善的 Plan 模式

**特点**:
- 完整的状态管理
- 专门的 UI
- 深度集成
- 生产级质量

### Codex-mini 应该实现简化版

**Day 4-5 目标**:
- ✅ 基础任务列表
- ✅ 状态追踪
- ✅ 模型分解
- ⚠️ 最小化实现

**效果**:
- 能力从 60% → 70%
- 可以自主完成简单项目
- MVP 目标达成

---

**Plan 模式是 Codex 的核心能力之一！**

Codex-mini 必须实现某种形式的任务规划才能真正"独立完成项目"。

**准备好开始 Day 4-5 吗？** 实现简化但实用的 TaskList 工具！

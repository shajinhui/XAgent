# update_plan 工具详解

## 🎯 什么是 update_plan？

**update_plan 是 Codex 提供的一个轻量级任务追踪工具。**

类似于 Agent 的"内部备忘录"或"待办清单"。

---

## 📋 工具定义

```rust
// 数据结构
struct PlanStep {
    step: String,        // "创建用户模型"
    status: StepStatus,  // Pending/InProgress/Completed
}

// 工具调用
update_plan({
    explanation: "可选的说明",
    plan: [
        {step: "创建模型", status: "pending"},
        {step: "写测试", status: "pending"}
    ]
})
```

---

## 🔄 完整工作流程示例

### 任务：给 API 添加认证功能

**步骤 1: 创建计划**
```json
update_plan({
  "plan": [
    {"step": "创建 User 模型", "status": "pending"},
    {"step": "实现密码哈希", "status": "pending"},
    {"step": "添加 JWT", "status": "pending"},
    {"step": "创建中间件", "status": "pending"},
    {"step": "编写测试", "status": "pending"}
  ]
})
```

**步骤 2: 执行第一个任务**
```json
update_plan({
  "plan": [
    {"step": "创建 User 模型", "status": "in_progress"},  // 开始
    {"step": "实现密码哈希", "status": "pending"},
    ...
  ]
})

→ write_file("models/user.py", "...")
```

**步骤 3: 完成并继续**
```json
update_plan({
  "plan": [
    {"step": "创建 User 模型", "status": "completed"},    // 完成
    {"step": "实现密码哈希", "status": "in_progress"},   // 开始下一个
    ...
  ]
})

→ write_file("auth/hash.py", "...")
```

---

## 📊 UI 显示效果

```
┌─ Current Plan ─────────────────┐
│ ✓ 创建 User 模型                │
│ ✓ 实现密码哈希                  │
│ ⋯ 添加 JWT (进行中)             │
│ ○ 创建中间件                    │
│ ○ 编写测试                      │
└────────────────────────────────┘
```

**符号**: ✓ 完成 / ⋯ 进行中 / ○ 待执行

---

## 💡 关键特点

### 1. **用户无感知**

```
用户看到:
"好的，我会添加认证功能"
(Agent 开始工作)

Agent 实际:
update_plan(创建计划)
write_file(...)
update_plan(标记完成)
write_file(...)
...
```

### 2. **自动调用**
- Agent 自己决定何时用
- 不需要用户执行 `/plan`
- 后台运行

### 3. **保持普通模式**
- 会话模式不变
- 系统提示不变
- 只是多了任务追踪

### 4. **灵活使用**
- 可以 3 步简单计划
- 可以 20 步复杂计划
- 可以中途调整

---

## 🆚 与 Plan Mode 的区别

| 特性 | update_plan 工具 | Plan Mode |
|------|-----------------|----------|
| **触发** | Agent 自动 | 用户 `/plan` |
| **感知** | 用户无感知 | 需要审批 |
| **模式** | 保持 Default | 切换到 Plan |
| **灵活** | 高 | 严格 |
| **适用** | 简单任务 | 大型项目 |

---

## 🎯 实际价值

### 1. 组织思路
```
"实现认证" → 分解为 6 个具体步骤
不会遗漏
```

### 2. 追踪进度
```
"已完成 3/6"
"当前: 添加中间件"
```

### 3. 错误恢复
```
执行到第 3 步出错:
✓ 步骤 1,2
✗ 步骤 3 (重试)
○ 步骤 4,5
```

### 4. 保持上下文
```
Agent 知道:
"我在执行第 3 步"
"前面完成了 2 步"
"还剩 3 步"
```

---

## 🔧 Codex-mini 实现方案

```python
# tools/planning/task_list.py

def update_plan(explanation: str = "", plan: list[dict] = None):
    """更新任务计划"""
    # 保存到 .codex-mini/tasks.json
    tasks = plan or []
    save_tasks(tasks)

    # 格式化显示
    lines = []
    for task in tasks:
        icon = {"pending": "○", "in_progress": "⋯", "completed": "✓"}
        lines.append(f"{icon[task['status']]} {task['step']}")

    return "\n".join(lines)
```

---

## 📝 总结

**update_plan 是什么？**
- 轻量级任务追踪工具
- Agent 自动调用
- 用户无感知
- 追踪进度
- 组织工作流程

**类似于**:
- Agent 的待办清单
- 内部备忘录

**不是**:
- 不是完整 Plan Mode
- 不需要用户开启

---

**Codex-mini Day 4-5**: 实现简化版 update_plan

**效果**: 能力 60% → 70%，达到 MVP！ 🚀

# Codex Plan 模式触发机制分析

## 🔍 关键发现

基于源码分析，Codex 的 Plan 模式有两种使用方式：

---

## 方式 1: Plan Mode（协作模式）⭐

### 这是一个独立的"模式"

```rust
// 协作模式枚举
enum ModeKind {
    Default,   // 普通模式
    Plan,      // Plan 模式
}

// 会话状态
struct TurnContext {
    collaboration_mode: ModeKind,  // 当前模式
}
```

### 触发方式

**用户主动进入**:
```
用户: /plan "实现用户认证系统"
```

或者

**Codex 提议进入**:
```
用户: "我想做一个复杂的电商系统"

Codex: "这是个复杂任务，我建议先制定计划。
       使用 /plan 命令进入 Plan 模式？"

用户: /plan
```

### Plan 模式特点

**整个会话切换到 Plan 模式**:
- ✅ 专门的系统提示词
- ✅ 不同的工具集
- ✅ 特殊的 UI 显示
- ✅ 持续到用户退出

**工作流程**:
```
1. 用户: /plan "目标"
2. 进入 Plan 模式
3. Codex 生成计划
4. 用户审批计划
5. Codex 逐步执行
6. 完成后退出 Plan 模式
```

---

## 方式 2: update_plan 工具（TODO 列表）

### 这是一个普通工具

```rust
// update_plan 工具
// 注意：这个工具在 Plan 模式中是禁用的！

if collaboration_mode == ModeKind::Plan {
    return Error("update_plan 不能在 Plan 模式使用");
}
```

### 使用方式

**在普通模式下使用**:
```
用户: "帮我实现一个功能"

Codex: (没有进入 Plan 模式)
       update_plan([
           {step: "创建模型", status: "InProgress"},
           {step: "写测试", status: "Pending"}
       ])
```

### 特点

**轻量级任务列表**:
- ⚠️ 在普通模式下使用
- ⚠️ 简单的 TODO 追踪
- ⚠️ 不影响会话模式
- ⚠️ 更灵活但功能较弱

---

## 📊 两种方式对比

| 特性 | Plan Mode | update_plan 工具 |
|------|----------|-----------------|
| **触发** | 用户主动 `/plan` | 自动调用 |
| **会话模式** | 切换整个模式 | 保持普通模式 |
| **系统提示** | 专门的 Plan 提示 | 普通提示 |
| **UI** | 特殊 Plan UI | 简单列表显示 |
| **用户审批** | 需要审批计划 | 无需审批 |
| **适用场景** | 大型复杂项目 | 简单任务追踪 |
| **灵活性** | 结构化、严格 | 灵活、轻量 |

---

## 🎯 实际使用场景

### 场景 1: 大型项目 → Plan Mode

```
用户: "从零开发一个电商系统，包括用户、商品、订单、支付"

Codex: "这需要大量工作，建议使用 Plan 模式。执行 /plan 吗？"

用户: /plan

→ 进入 Plan Mode
→ 生成 30 个步骤
→ 用户审批
→ 逐步执行
→ 显示专门的进度 UI
```

---

### 场景 2: 简单任务 → update_plan

```
用户: "给这个函数添加错误处理"

Codex: (内部)
       update_plan([
           {step: "添加 try-catch", status: "InProgress"},
           {step: "添加日志", status: "Pending"}
       ])

       (执行任务)

       update_plan([
           {step: "添加 try-catch", status: "Completed"},
           {step: "添加日志", status: "InProgress"}
       ])

→ 保持普通模式
→ 简单的任务追踪
→ 不显示特殊 UI
```

---

## 💡 关键区别

### Plan Mode = 严格的项目管理

**类似于**:
- 正式的项目规划会议
- 需要用户批准
- 严格按步骤执行

**用户体验**:
```
"我要进入 Plan 模式了，请审批我的计划"
```

---

### update_plan = 内部任务追踪

**类似于**:
- Agent 自己的备忘录
- 不打断用户
- 灵活调整

**用户体验**:
```
用户可能都不知道 Agent 在用 update_plan
只是看到 Agent 在有条不紊地工作
```

---

## 🤔 回答原问题

### ❓ Plan 模式是用户需要主动开启的吗？

**答案**: 是的，但有两种情况：

### 1. Plan Mode（严格的计划模式）

**必须主动开启**:
- ✅ 用户执行 `/plan`
- ✅ 或者 Codex 建议，用户同意

**不会自动进入**: 因为会改变整个会话的行为

---

### 2. update_plan 工具（轻量追踪）

**自动使用**:
- ✅ Codex 自己决定何时用
- ✅ 用户无感知
- ✅ 不影响会话模式

**更常用**: 日常简单任务用这个

---

## 🎯 对 Codex-mini 的启示

### 建议方案: 混合策略

**阶段 1 (Day 4-5): 实现 update_plan 式工具**

```python
# tools/planning/task_list.py

def create_task_list(goal: str):
    """Agent 自动调用，生成任务列表"""
    # 用小模型分解
    # 保存到文件
    # 返回列表

def mark_done(task_id: int):
    """Agent 执行完一步后调用"""
```

**特点**:
- ✅ 简单实现
- ✅ 自动使用
- ✅ 不改变会话模式
- ✅ 达到 MVP 目标

---

**阶段 2 (未来): 实现 Plan Mode**

```python
# 用户主动触发
/plan "实现电商系统"

→ 进入 Plan 模式
→ 生成详细计划
→ 用户审批
→ 严格执行
```

**特点**:
- ⚠️ 复杂实现
- ⚠️ 需要 UI 支持
- ⚠️ 需要状态管理

---

## 📝 总结

### Codex 有两套系统

**Plan Mode**:
- 用户主动开启 (`/plan`)
- 严格的项目管理
- 适合大型项目
- 需要用户审批

**update_plan 工具**:
- Agent 自动使用
- 轻量任务追踪
- 适合日常任务
- 用户无感知

### Codex-mini Day 4-5

**实现 update_plan 式的工具**:
- 简单快速
- 自动使用
- 满足 MVP

**不实现完整 Plan Mode**:
- 太复杂
- 留待未来

---

**Day 4-5 任务明确**: 实现自动的、轻量的 TaskList 工具（类似 update_plan），而不是完整的 Plan Mode。

这样更实用，也更容易实现！🚀

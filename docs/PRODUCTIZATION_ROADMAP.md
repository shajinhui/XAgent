# XCode 产品化路线

结合 Codex 和 Claude Code 的分析，阶段四不应该只做成“加几个高级功能”，而应升级为：

## XCode 产品化路线：从本地 Agent Runtime 进化成可恢复、可审查、可扩展的 Coding Agent 工作台

### 参考基准

- **OpenAI Codex 官方方向**：CLI、本地运行、IDE、Desktop App、Codex Web、resume、MCP、skills、hooks、subagents、sandbox、GitHub/Slack/Linear 集成。
- **Claude Code analysis 启发**：append-only transcript、resume 恢复流水线、context compact、memory 分层、skills/MCP 扩展、sandbox + permission 一体化、multi-agent sidechain。

### 总体架构

```text
XCode
├── Clients
│   ├── CLI
│   ├── Desktop Electron
│   └── Future IDE Extension
├── Runtime Service
│   ├── Agent Loop
│   ├── Event Protocol
│   ├── Tool Scheduler
│   └── Model Router
├── Session Layer
│   ├── Transcript JSONL
│   ├── SQLite Session Index
│   ├── Resume / Recovery
│   └── Sidechain Sessions
├── Context Layer
│   ├── Token Budget
│   ├── Auto Compact
│   ├── AGENTS.md Context
│   └── Memory Injection
├── Tool Layer
│   ├── Built-in Tools
│   ├── Git Tools
│   ├── Patch Tools
│   ├── LSP Tools
│   ├── MCP Tools
│   └── Skills
├── Safety Layer
│   ├── Permission Mode
│   ├── Path Policy
│   ├── Command Policy
│   ├── macOS Seatbelt / Future Docker Sandbox
│   └── Audit Log
└── Product Layer
    ├── Diff Review
    ├── Task Modes
    ├── Hooks
    ├── Automations
    └── Subagents
```

## 第一阶段：Session / Transcript / Resume

**这是最应该先做的。** 没有它，后面的 memory、context、multi-agent 都会漂。

### 目标

- 每次对话都有 `session_id`
- 所有关键事件写入 append-only JSONL
- SQLite 只做索引，不做唯一真相
- 支持 `/resume`
- 支持 Desktop 断线重连后恢复

### 建议结构

```text
.codex-mini/
  sessions/
    index.sqlite
    transcripts/
      <session_id>.jsonl
      <session_id>/
        subagents/
          agent-<id>.jsonl
```

### 事件类型

```text
user_message
assistant_message
assistant_delta
tool_call_started
tool_call_result
permission_request
permission_decision
summary
mode_changed
project_instructions
git_state
session_title
session_tag
```

### 交付物

- `session/store.py`
- `session/transcript.py`
- `session/recovery.py`
- WebSocket `resume_session`
- CLI `codex-mini resume`
- Desktop session picker

### 验收标准

- 后端重启后能恢复最近会话
- 旧工具结果仍能作为上下文被模型理解
- 权限记录、模式、项目目录能恢复
- 单元测试覆盖 transcript append / load / resume

## 第二阶段：Context Manager / Auto Compact

你原来写的“上下文窗口管理”方向对，但要产品化。

### 目标

- 不直接把所有 `messages` 丢给模型
- 由 `ContextManager` 组装最终 prompt
- 超阈值自动 compact
- 保留目标、约束、最近工具结果、最近 diff、用户偏好、项目规则

### 核心模块

```text
context/
  token_counter.py
  builder.py
  compactor.py
  summaries.py
```

### 策略

| 内容类型                        | 处理方式       |
| --------------------------- | ---------- |
| 系统提示                        | 永远保留       |
| AGENTS.md / project profile | 压缩后保留      |
| 用户原始目标                      | 永远保留       |
| 最近 N 轮                      | 完整保留       |
| 旧工具结果                       | 摘要保留       |
| 大 stdout                    | 截断 + 文件引用  |
| diff                        | 只保留摘要和文件列表 |

### compact 触发条件

- 超过 **70% context**：轻量压缩
- 超过 **85% context**：强制压缩
- compact 失败 **3 次**：停止自动压缩，提示用户

### 交付物

- `ContextManager.build_messages()`
- `TokenBudget`
- `ConversationSummary`
- 小模型摘要调用
- 测试长会话不爆 context

## 第三阶段：AGENTS.md 项目指令 / Codex-style Project Docs

**这个应该排在 Chroma 前面。**

### 目标

- 对齐 Codex：项目级上下文主要来自 `AGENTS.md`
- 从 `project_root` 到 `current_dir` 加载可见的 `AGENTS.md`，并把内容注入模型上下文
- workspace/env 继续提供已有的 `project_root`、`selected_root`、`current_dir`、permission/trust 边界
- workspace 打开、目录切换、session resume 后刷新项目指令
- 让模型按任务需要继续使用 `list_files`、`file_search`、`read_file`、`run_tests` 自己探索

### 上下文内容

```text
AGENTS.md content from project_root -> current_dir
AGENTS.md source paths
existing environment/workspace context:
  project_root
  selected_root
  current_dir
  permission/trust boundary
```

### 不做

- 不生成 `.codex-mini/project_profile.json`
- 不生成 `.codex-mini/project_summary.md`
- 不在启动时递归扫描整个仓库
- 不预先猜测完整语言栈、测试命令和构建命令
- 不做 `workspace/project_hints.py`、root marker hints、git summary 预计算或 `.gitignore` scanner

### 交付物

- 收口 `workspace/instructions.py` 的 `AGENTS.md` 分层加载和 budget/truncation 行为
- 确保 `server/runtime/websocket_context.py` 在 workspace 打开、目录切换和 session resume 后刷新系统提示
- 模型上下文里只注入 `AGENTS.md` 内容、来源路径和已有 workspace/env 边界
- 测试：不写持久 profile、不扫描 root markers、`AGENTS.md` 变更会按当前 workspace/cwd 生效

## 第四阶段：Diff / Patch / Git Review

**这是你和成熟产品差距最大的体验点之一。**

Stage 4 的详细收敛方案和执行分期见 `docs/STAGE4_CHANGE_REVIEW_RUNTIME.md`。

现在 `write_file` / `edit_file` 是“批准后直接写”。下一步应该变成：

```text
模型提出修改 -> 生成 patch -> 展示 diff -> 用户 approve -> apply patch -> 自动测试 -> 总结变更
```

### 核心模块

```text
patch/
  diff_builder.py
  apply_patch.py
  rollback.py
  review.py
git/
  status.py
  branch.py
  commit.py
  pr_summary.py
```

当前第一片已落地 `patch/models.py`、`patch/diff_builder.py` 和 `patch/store.py`，覆盖 proposal core、diff 构造和 pending patch JSON 存储；`write_file` 与 `edit_file` 都已通过 `dry_run=true` 生成 patch proposal；`tools/patching` 已提供 `apply_patch` / `reject_patch` / `rollback_patch` 工具骨架。WebSocket patch lifecycle events 已接入 transcript 和桌面 activity/system-message 展示；桌面端已有 live `PatchReviewCard.vue` 负责 review，并新增 `PatchResultCard.vue` 展示独立测试历史；`session_resumed` 会恢复仍待处理的 pending review；卡片支持 apply selected，部分应用后剩余文件继续保留在同一个 pending patch 中。`apply_patch` 已支持显式 `test_command` 后置测试，trusted `.codex-mini/config.toml` 也可通过 `[tests] command = "..."` / `timeout = 60` 提供默认 post-apply 测试命令，AGENTS.md 里的显式测试命令也会在通过安全校验后自动成为默认测试入口。Stage 4.7 已让 patch proposal 记录 partial apply 的 `original_changes` / `applied_changes`，并支持按 `patch_id` durable rollback：完整 apply 后可回滚为 `rolled_back`，partial apply 后可回滚并恢复完整 pending review；现在即使 partial apply 的 patch 在 rollback 中途失败，已成功恢复的文件也会重新回到 pending review，`applied_changes` / `applied_paths` 会收敛到尚未回滚的子集，后续 retry rollback 仍可继续恢复。Git 仓库内 apply/rollback 还会先跑 `git apply --check` / `--reverse --check` preflight，且 patch runtime 会拒绝 symlink 路径、binary / 非 UTF-8 文件，以及 `.git`、`.codex-mini`、`.venv`、`__pycache__` 这类 protected path。`move_path` / rename + protected path 组合、`cross-root move_path`、多文件 simultaneous cross-root move、multiple writable additional roots 组合、rename + apply/rollback failure diagnostics、多文件 `partial apply + cross-root move_path`、跨多 roots 的失败诊断、`additional root` read/write 边界，以及 `additional root + symlink/binary` 组合现在也已有回归测试覆盖；同时修复了 macOS 下 external additional root 会被父级 symlink 误判为非法 patch 路径的问题。现在 apply / rollback 如果在写入循环中途失败，也会把 `written_paths` / `rolled_back_paths`、`failed_path`、`remaining_paths` 和 partial-write 标记持久化下来，并同步展示到失败 review 卡片、patch lifecycle 时间线和独立测试历史卡片。对 rename 冲突场景，runtime 现在会显式拒绝覆盖预先存在的 move target 或 restore path。至此 Stage 4 已可按 `v0.4.0 Change Review Runtime` 收口。

### 工具升级

```text
write_file_preview
edit_file_preview
apply_patch
reject_patch
git_status
git_diff
git_diff_file
git_changed_files
```

### Desktop UI 应新增

- 文件变更列表
- inline diff
- approve / reject
- apply all / apply selected
- command output 面板
- git status 面板

### 验收标准

- 默认写入前可看到 diff
- 可撤销最近一次 agent 修改
- agent 能解释改了哪些文件
- 测试失败时能定位到对应 patch

## 第五阶段：Permission Mode / Sandbox Policy Lite

当前权限系统已经足够 alpha 使用：`request_approval`、`auto_approve`、`full_access` 和 `custom` 已能覆盖日常开发，macOS Seatbelt、session allowlist、trust-gated project config 和 transcript 审计也已有第一版。因此 Stage 5 不继续扩成完整策略平台，先做现有能力的体验收口。

### 保留现有模式

| 模式                 | 语义                                        |
| ------------------ | ----------------------------------------- |
| `request_approval` | 默认模式；写入、测试和风险命令先询问                        |
| `auto_approve`     | 非危险工作区操作自动继续，危险命令仍拒绝                      |
| `full_access`      | 用户显式选择；关闭 Seatbelt 并开启网络                  |
| `custom`           | trusted `.codex-mini/config.toml` 中的白名单策略 |

### 本阶段只做

- 权限弹窗展示原因、cwd、沙箱状态、网络状态和建议的 session prefix rule。
- `full_access` 在 composer 和权限弹窗中显示明确风险提示。
- macOS Seatbelt 不可用、非 macOS 环境、嵌套沙箱失败时给出可理解的 fallback 说明。
- 补少量回归测试，锁住现有 permission metadata 和 sandbox 错误文案。
- 同步 `AGENTS.md`、`docs/PROJECT_ARCHITECTURE_STATUS.md` 和权限架构文档。

### 当前落地状态

Stage 5 Lite 第一片已经落地：权限弹窗会展示确认原因、cwd、permission profile、网络/沙箱状态和建议的 session prefix rule；composer 与权限弹窗会明确提示 `full_access` 会关闭 Seatbelt 并开启网络；macOS Seatbelt 在非 macOS、命令缺失和嵌套沙箱场景下会返回可操作的说明；相关 metadata、权限模式和 sandbox 错误路径已有回归测试。后续只补真实桌面 smoke test 和更完整的 macOS 端到端验证，不把本阶段扩成策略平台。

### 暂不做

- persistent allowlist / 策略编辑器。
- domain allowlist / denylist 网络策略。
- 新权限模式命名迁移。
- Git commit / push / PR 自动化。
- 完整 sandbox doctor 面板。

## 第六阶段：Memory 分层

**不要一开始就重 Chroma。** 先做透明、可审查的 memory。

### 建议结构

```text
.codex-mini/
  memory/
    user.md
    project.md
    task.md
    preferences.json
```

### 分层定义

| 层级             | 内容                      |
| -------------- | ----------------------- |
| User Memory    | 用户偏好，例如喜欢 pnpm、单引号、中文解释 |
| Project Memory | 项目规则、架构约定、常用命令          |
| Session Memory | 当前会话摘要                  |
| Task Memory    | 当前任务目标、已尝试方案、阻塞点        |
| Vector Memory  | 后期再加，用于语义搜索代码片段         |

### 工具

```text
remember_preference
forget_memory
search_memory
summarize_session
```

### 原则

- 用户可查看
- 用户可删除
- 默认不记录敏感内容
- 每条 memory 标记来源和时间
- 注入前经过 ContextManager 控制 token

## 第七阶段：Skills / Hooks / MCP

**这是从“项目工具”变成“平台”的关键。**

### Skills

```text
.codex-mini/skills/
  python-refactor/
    SKILL.md
    scripts/
  frontend-design/
    SKILL.md
```

`SKILL.md` 可包含：

```yaml
name:
description:
when_to_use:
allowed_tools:
preferred_model:
instructions:
```

### Hooks

```text
before_tool_call
after_tool_call
before_apply_patch
after_apply_patch
on_test_failure
on_session_resume
```

### MCP

```text
mcp/
  config.py
  client.py
  registry_adapter.py
```

### 整合方向

```text
ToolRegistry
├── Built-in tools
├── Skill tools
├── MCP tools
├── IDE tools
└── Git tools
```

### 验收标准

- 能配置一个 MCP server
- MCP tools 和内置 tools 一起暴露给模型
- MCP tool 也走权限策略
- skills 可以被项目级启用

## 第八阶段：LSP / IDE 能力

**LSP 不要太早做，但做了以后很有价值。**

### 先做 Python

```text
lsp/
  diagnostics.py
  symbols.py
  references.py
  hover.py
```

### 优先能力

- 获取语法错误
- 获取诊断
- 找 symbol
- 找引用
- 修改后自动重新诊断
- 把 diagnostics 注入模型上下文

### 工具

```text
get_diagnostics
find_symbol
find_references
```

### Desktop / IDE 结合

- 问题列表
- 点击跳转文件
- 修改后显示错误减少/增加

## 第九阶段：Subagents / Multi-Model / Automation

**这个最后做，别一开始就炫技。**

### Subagents

```text
agent/
  coordinator.py
  worker.py
  sidechain.py
```

### 场景

- 一个 agent 读后端
- 一个 agent 读前端
- 主 agent 汇总方案
- 写入权限仍由主 session 控制

### Multi-model

```text
model_router.py
```

### 策略

| 场景         | 模型选择   |
| ---------- | ------ |
| 简单问答       | 便宜快速模型 |
| 代码解释       | 中等模型   |
| 复杂重构       | 强模型    |
| 总结 compact | 小模型    |
| 安全判断       | 稳定模型   |

### Automation

```text
codex-mini exec "run tests and summarize"
codex-mini watch "每天检查 CI"
```

## 最终优先级

建议按以下顺序实施：

1. **Session / Transcript / Resume**
2. **ContextManager / Auto Compact**
3. **AGENTS.md 注入 / 项目指令分层**
4. **Patch Preview / Diff Review / Apply Patch**
5. **Permission Mode / Sandbox Policy**
6. **Memory 分层**
7. **Skills / Hooks**
8. **MCP**
9. **LSP**
10. **Subagents / Multi-model / Automation**

## 最小可发布版本

如果要定义一个阶段四第一个可发布版本，建议命名：

```text
v0.4.0 Durable Agent Runtime
```

包含以下特性：

- append-only transcript
- session resume
- context compact
- project profile
- diff preview
- permission mode
- Desktop session state 展示

这版做完，XCode 就会从 **“能聊天、能调工具”** 变成 **“可以连续干活、断了能接、改动能审”** 的产品。

## 一句话路线

> **先学 Codex 的产品入口和治理体系，再学 Claude Code 的 transcript/context/memory/runtime 深度。**  
> 不要先追多模型、LSP、Chroma。真正的核心是：  
> **会话可恢复、上下文可控、修改可审查、权限可治理、工具可扩展。**

## 参考

- [OpenAI Codex GitHub](https://github.com/openai/codex)
- [Codex CLI Features](https://developers.openai.com/codex/cli/features/)
- [Codex IDE Extension](https://developers.openai.com/codex/ide/)
- [claude-code-analysis README](https://github.com/liuup/claude-code-analysis)
- [Session Storage / Resume 分析](https://github.com/liuup/claude-code-analysis/blob/main/analysis/04i-session-storage-resume.md)
- [Context Management 分析](https://github.com/liuup/claude-code-analysis/blob/main/analysis/04f-context-management.md)

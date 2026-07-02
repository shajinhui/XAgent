# Stage 4：Change Review Runtime 执行计划

Stage 4 应该收敛成“修改可审查”这一条主线，不扩散到 MCP、LSP、多 Agent。

更准确的版本目标是：

~~~text
v0.4.0 Change Review Runtime
~~~

核心链路：

~~~text
模型提出修改
  -> 生成结构化 patch / diff
  -> 桌面端展示 review
  -> 用户 approve / reject
  -> runtime apply
  -> 自动测试 / 回滚 / 总结
~~~

## 当前结论

- docs/PRODUCTIZATION_ROADMAP.md 里 Stage 4 写成 Diff / Patch / Git Review，方向正确。
- Stage 4 不应继续沿用 project profile 旧表述；项目上下文继续坚持 AGENTS.md-first。
- 这个阶段的目标不是“加更多功能”，而是把 XCode 从“会改代码”升级成“改代码前后都可审查、可恢复、可解释”。

## Codex 参考点

参考上游 Codex，Stage 4 最值得借鉴的是：

- apply_patch 是一等工具：先解析为结构化 FileChange，再做安全评估和审批，而不是直接写文件。
- patch 有生命周期事件：approval request、patch begin、patch updated、patch end，前端可以实时展示将要改什么、是否已应用。
- TurnDiffTracker 按 turn 追踪净 diff；本项目已有 session/turn_diff.py 雏形，但当前仍偏事后 changed files + undo。
- review 模式支持 uncommitted / base branch / commit / custom；第一版不完整复刻，但数据结构要预留 review_target。

## 执行分期

### Stage 4.1：补齐 patch 后端模型

新增独立 patch/ 域，不塞进 server/app.py 或 tools/filesystem/。

交付：

- patch/models.py：PatchProposal、PatchFileChange、PatchStatus
- patch/diff_builder.py：从 before / after 生成 unified diff
- patch/apply.py：真正 apply patch，先做 dry-run / check
- patch/store.py：把 pending patch 存到 .codex-mini/patches/

验收：

- 能创建 patch proposal。
- 能生成 add / update / delete diff。
- 能持久化 pending patch。
- 不直接写源码文件。

当前状态：

- 已完成 proposal models、diff builder、store。
- v0.4.0 当前把 apply / rollback runtime 逻辑稳定收敛在 `tools/patching/patch_review.py`；后续如要拆成 `patch/apply.py`，属于 Stage 5 的内部重构，不再阻塞 Stage 4 收口。

### Stage 4.2：把写入工具改成“默认预览”

当前 write_file 原本是批准后直接写；edit_file 有 dry_run，但不是完整 review 流。

目标工具形态：

- write_file_preview
- edit_file_preview
- apply_patch
- reject_patch

第一版允许保留旧 write_file / edit_file，但系统提示应要求模型优先走 patch preview；等 UI 稳定后再把直接写入降级为兼容路径。

验收：

- write_file dry_run=true 不写源码。
- dry-run 返回 diff。
- dry-run 保存 pending proposal 并返回 patch_id。
- apply / reject 通过 patch_id 操作 stored proposal。

当前状态：

- write_file dry_run=true 已接入 patch.diff_builder。
- write_file dry_run=true 已保存 pending proposal。
- edit_file dry_run=true 现在也已保存 pending proposal，并返回 patch_id / diff stats。
- apply_patch / reject_patch 工具骨架已完成。
- v0.4.0 继续保留 `write_file` / `edit_file` 作为兼容工具名，`dry_run=true` 就是标准 preview 入口；独立 preview alias 不再作为 Stage 4 阻塞项。

### Stage 4.3：新增 WebSocket patch 事件

建议事件名贴近现有协议风格：

- patch_proposed
- patch_approval_request
- patch_applied
- patch_rejected
- patch_apply_failed
- turn_diff

要求：

- patch lifecycle event 同步写入 transcript。
- resume 后 pending review 不丢。
- 前端即使还没有完整 review UI，也能先在 activity/system message 中看到 patch 状态。

验收：

- write_file dry_run=true 后推送 patch_proposed。
- apply_patch 成功后推送 patch_applied。
- reject_patch 成功后推送 patch_rejected。
- apply_patch 失败后推送 patch_apply_failed。
- transcript 中有对应事件，可供 session summary / resume 使用。

当前状态：

- 已完成后端 lifecycle event helper：patch_proposed、patch_approval_request、patch_applied、patch_rejected、patch_apply_failed。
- patch lifecycle event 会写入 transcript，并同步推送 WebSocket。
- 桌面端已能识别 patch lifecycle event，并在现有 activity/system message 中展示 patch 状态。
- session summary 恢复已能把 patch lifecycle event 投影回 activity 轨迹。
- session resume 已能恢复仍待处理的 pending patch review 卡片。
- v0.4.0 继续保留 `final_answer.changed_files` 作为 turn 级 diff 摘要出口；独立 `turn_diff` 事件不再作为 Stage 4 阻塞项。
- Diff Review UI 主链路已在 Stage 4.4 收口。

### Stage 4.4：桌面端 Diff Review 面板

当前桌面端只显示“已编辑几个文件”和撤销按钮，Stage 4 要升级为 review 面板。

第一版 UI：

- 文件变更列表
- inline diff
- approve / reject
- apply all / apply selected
- 应用后显示测试结果
- 保留现有撤销入口

建议先做：

- PatchReviewCard.vue
- 不做复杂 GitHub PR 级体验。
- 不做多文件复杂批量选择的极致交互，先保证主链路可用。

当前状态：

- 已完成最小 PatchReviewCard.vue。
- live patch_proposed / patch_apply_failed 事件会在桌面端形成 pending patch review 卡片。
- 卡片已能展示文件列表、增删统计和 unified diff。
- 卡片提供应用 / 拒绝 / 收起入口；应用 / 拒绝已改为直接发送 apply_patch_review / reject_patch_review control packet，不再绕模型。
- 卡片支持勾选文件并应用所选；部分应用后同一个 pending patch 会保留剩余文件继续 review。
- session_resumed 会带回 pending_patch_review，刷新或恢复历史会话后待审查卡片不丢。
- 应用后测试结果现在会在 pending review 卡片内展示最近一次结果，并在独立 `PatchResultCard.vue` 中展示完整测试历史。

### Stage 4.5：Git 只先做 read-only review 能力

第一版只加只读 Git 能力：

- git_status
- git_diff
- git_diff_file
- git_changed_files

当前状态：

- 已新增 `tools/git/` 域。
- `git_status` 输出当前 workspace 范围内的 short status。
- `git_diff` 输出当前 workspace 范围内的 unstaged / staged diff。
- `git_diff_file` 输出单文件 diff，文件路径必须先通过 workspace read policy。
- `git_changed_files` 返回结构化 changed files metadata。
- Git 工具默认限制在 selected_root 对应的 Git pathspec 范围内；当 selected_root 是仓库子目录时，不展示仓库其它目录的 diff。

暂不做：

- 自动 commit
- push
- PR

commit 可以放 Stage 4.6 或 Stage 5，因为它属于更强外部状态变更。

### Stage 4.6：测试与失败归因

apply patch 后可以自动跑可配置测试命令，但第一版保持简单。

原则：

- 默认不猜测试命令。
- 使用用户、AGENTS.md 或可信项目配置给出的测试命令。
- 测试失败时把失败输出关联到 patch_id 和 changed files。
- final answer 总结改了什么、测了什么、失败在哪。

当前状态：

- `apply_patch` 已支持显式 `test_command` / `test_timeout`。
- trusted `.codex-mini/config.toml` 已支持 `[tests] command = "..."` 和 `timeout = 60`，并会作为 `apply_patch` 的默认 post-apply 测试命令。
- 默认不自动探测、不猜测测试命令；没有显式 `test_command` 且没有 trusted config `[tests]` 时只应用 patch。
- 测试命令通过现有 command executor 运行，继承 filesystem policy、network policy、current_dir 和 sandbox profile。
- 测试命令会先经过现有 ExecPolicy 的危险命令 / protected path 拒绝逻辑；apply 审批只覆盖显式或 trusted config 测试命令的执行确认，不绕过安全拒绝。
- 测试结果会写入 tool metadata 和 patch proposal metadata，关联 `patch_id`、本次应用的 changed paths、命令、退出码和截断后的 stdout / stderr / output。
- 测试失败不会把已成功应用的 patch 标成 apply failed；状态仍是 applied / proposed（partial apply），失败作为诊断结果随 patch 记录。
- 桌面端 `PatchReviewCard.vue` 已支持可选 `test_command` / `test_timeout` 输入，并展示最近一次 patch 测试结果；patch lifecycle activity / system message 也会带出测试状态、命令和失败输出摘要。

配置示例：

~~~toml
[tests]
command = "python -m unittest discover -s tests"
timeout = 120
~~~

待收口：

- 已收口：现在默认测试命令优先级为「显式传入 > trusted config > AGENTS.md」，并且桌面端已有独立 patch test history 卡片。

### Stage 4.7：回滚和安全硬化

当前 undo 基于上一次 diff_tracker，不够 durable。Stage 4 后期要升级为 patch 级回滚。

目标：

- patch_id 级回滚。
- partial apply 失败时保留可诊断状态。
- protected path / symlink / binary file / .git / .env 边界测试。
- git apply --check 可用时先 preflight。

当前状态：

- `rollback_patch` 工具第一片已接入，支持回滚 `PatchStatus.APPLIED` 的完整 patch，以及 `partial apply` 后仍处于 `PatchStatus.PROPOSED` 的 patch。
- partial apply 现在会把 `original_changes` / `applied_changes` 写入 patch metadata，因此多次 `apply selected` 后最终完整 apply 的 patch 也能按 `patch_id` 回滚。
- 部分应用后的 rollback 会把 patch 恢复成完整 `proposed` review，桌面端会重新出现待审查卡片；完整应用后的 rollback 会把 patch 标记为 `rolled_back`。
- 新增 `patch_rolled_back` lifecycle event；历史投影和会话恢复会把它当作已解决状态，不再误恢复成 pending review。
- `rollback_patch` 同样重新走 workspace write policy，因此对 `.env` 等受保护路径仍会拒绝。
- `apply_patch` / `rollback_patch` 现在会在 Git 仓库中优先跑 `git apply --check` / `git apply --reverse --check` preflight，工作树已漂移时会在真正写文件前失败。
- patch runtime 现在显式拒绝 `symlink` 路径和 binary / 非 UTF-8 文件，失败 metadata 会带上 `failure_stage`，便于区分是 validate、preflight 还是 write 阶段失败。
- apply / rollback 在写入循环中途失败时，现在会把 `written_paths` / `rolled_back_paths`、`failed_path`、`remaining_paths` 和 partial-write 标记持久化到 patch metadata；桌面端失败卡片和 patch lifecycle 时间线会直接展示这些诊断，便于判断哪些文件已经被改动、哪个文件卡住、哪些文件还未触及。
- `.git` 相关 patch apply / rollback 拒绝回归测试也已补上，明确保证 Git metadata 不会被 review runtime 当作普通可改文件。
- `.codex-mini`、`.venv`、`__pycache__` 的 patch apply / rollback 拒绝回归测试也已补上，当前默认 protected path 已覆盖到主要内部运行目录。
- `move_path` / rename + protected path 组合回归测试已补上，避免“重命名目标或源路径是受保护目录”时漏过 review runtime。
- `additional root` 的 apply / rollback 边界已补上：writable additional root 可以成功 apply + rollback，read-only additional root 会被拒绝；同时修复了路径校验在 macOS 上沿着父级 symlink 继续上探、误伤 external additional root 的问题。
- `cross-root move_path` 现在也已有回归测试覆盖：项目根内文件可以安全移动到 writable additional root，并能按 `patch_id` 正常 rollback。
- `cross-root` 多文件同时 `move_path` 现在也已有回归测试覆盖：同一个 patch 内的多个项目内文件可以一起移动到 writable additional root，并能整体 apply / rollback。
- `multiple writable additional roots` 组合现在也已有回归测试覆盖：同一个 patch 可以把不同文件分别移动到多个 external writable roots，并支持完整 apply / rollback，以及 partial apply 后再 rollback reopen。
- `additional root + symlink/binary` 组合也已补上回归测试，确保外部可写根不会绕过文本文件与 symlink 限制。
- 多文件 `partial apply + cross-root move_path` / rename 链路现在也已有回归测试覆盖：既验证了按 `selected_paths` 选中 rename 目标做部分应用，也验证了后续完整 apply 与 rollback 的恢复链路。
- `rename + failure diagnostics` 组合现在也已有回归测试覆盖：apply / rollback 在 `move_path` 场景写到一半失败时，会继续记录目标路径侧的 `written_paths` / `rolled_back_paths`、`failed_path` 和 `remaining_paths`，并验证恢复链路没有被 rename 场景破坏。
- `partial apply reopen + rollback failure` 交织场景现在也已补上：当 partial apply 的 patch 在 rollback 中途失败时，已经成功回滚的文件会重新回到 pending review，`applied_changes` / `applied_paths` 会收敛到仍然处于已应用状态的子集，后续 retry rollback 也能继续完成恢复。
- 跨多个 writable additional roots 的失败诊断链路现在也已补上：`written_paths` / `rolled_back_paths`、`failed_path` 和 `remaining_paths` 在多 external roots 组合下仍能保持稳定。
- `move_path` 现在会显式拒绝覆盖一个预先存在的目标文件；rollback 也会显式拒绝覆盖被外部重新创建的 restore path，避免 rename 冲突时静默覆盖未知内容。

## 暂时不做

- MCP 工具治理
- LSP 诊断
- 多 Agent / subagents
- 自动 PR
- 向量数据库记忆
- project profile 持久化

## 当前实现进度

| 分期 | 状态 | 说明 |
| --- | --- | --- |
| Stage 4.1 | 已完成 | proposal model、diff builder、store 已完成；`patch/apply.py` 独立拆分转为 Stage 5 内部重构项 |
| Stage 4.2 | 已完成 | `write_file` / `edit_file` 的 `dry_run=true` 都已生成 pending proposal；v0.4.0 继续使用现有工具名作为 preview 入口 |
| Stage 4.3 | 已完成 | WebSocket lifecycle events、transcript、desktop activity 展示和 session resume 都已接入；`final_answer.changed_files` 继续承担 turn 级 diff 摘要 |
| Stage 4.4 | 已完成主链路 | 最小 live PatchReviewCard、direct control packet、pending review 恢复、apply selected 已接入；测试结果展示并入 Stage 4.6 |
| Stage 4.5 | 已完成第一片 | git_status、git_diff、git_diff_file、git_changed_files 已接入；commit/push/PR 继续不做 |
| Stage 4.6 | 已完成 | apply_patch 支持显式 test_command、trusted config `[tests]` 和 AGENTS.md 默认测试命令；桌面端已接入测试命令输入、最近一次结果和独立测试历史卡片 |
| Stage 4.7 | 已完成 | `rollback_patch`、partial apply rollback reopen、`patch_rolled_back` event、`git apply --check` preflight、symlink/binary 拒绝、partially-written diagnostics、`move_path` / rename + protected path 回归测试、`cross-root move_path`、多文件 simultaneous cross-root move、multiple writable additional roots 组合、`additional root` read/write 边界、`additional root + symlink/binary` 组合测试、rename + apply/rollback failure diagnostics、partial-apply rollback-failure state reconciliation、跨多 roots 失败诊断和 rename 冲突拒绝都已接入 |

## 执行顺序

## 收口结论

Stage 4 现在可以按 `v0.4.0 Change Review Runtime` 收口：

- 模型可以生成结构化 patch proposal
- 桌面端可以做 review / apply / reject / apply selected
- apply 后可以跑显式 / trusted config / AGENTS.md 默认测试命令
- patch 可以按 `patch_id` durable rollback
- 失败时可以保留可诊断、可恢复、可解释的状态

后续若还要继续扩展，优先进入 Stage 5：

1. `patch/apply.py` 等内部模块拆分重构
2. 更高阶的 Git review / commit / PR 工作流
3. 更复杂的 diff rendering / review UX 优化

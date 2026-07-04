# AGENTS.md

This file is the shared context for future agent conversations in this repo.

## Project Snapshot

- Project: XCode
- Goal: a Python Codex-like local agent runtime with a desktop client and optional IDE extension
- Current stage: stage 2 alpha, desktop runtime bridge and durable session baseline in progress
- Main focus: modular tools, safety policy, macOS native sandbox command execution, local event protocol, durable transcripts, and desktop-client runtime UX

## Current Status

- `agent_loop.py` already uses `ToolRegistry` and LangGraph orchestration.
- Tools are split into individual modules under `tools/`.
- `run_command` goes through `security/` and `sandbox/macos_executor.py`.
- `processes/` provides the first Runtime-managed background process slice: `start_process` launches an independent process group through the existing command policy and sandbox boundary, optional `expected_port` checks require the listener to belong to that process group, `process_status` exposes owned status/log tails and read-only listener diagnostics, and `stop_process` accepts only Runtime-issued process ids, sends SIGTERM first, and uses SIGKILL only after timeout.
- macOS Seatbelt runs approved commands in the real project workspace, derives read/write roots from `FileSystemPolicy`, and keeps network denied by default unless the user explicitly selects full access mode.
- `server/app.py` is a local event transport / runtime bridge prototype, not the final product UI.
- `server/` is now split into protocol, runtime, processors, and views modules so `app.py` can focus on WebSocket transport and turn orchestration.
- WebSocket events now carry a schema version and session state.
- Assistant output uses LiteLLM streaming in the WebSocket path instead of simulated word chunks.
- Permission requests support approve/deny acknowledgements and approved tool retry.
- Session state now tracks active turns, cancellation requests, and suspension; suspended sessions block new turns until the client sends `resume_session`, and persisted suspension state is restored from transcript.
- WebSocket waiting points support `cancel_turn` and return `session_busy` when the client tries to start another turn while permission or clarification is pending.
- In the default request-approval mode, mutating tools require explicit approval before they write or run commands.
- Durable session storage now lives under `.codex-mini/sessions/`, with a SQLite index and append-only JSONL transcripts.
- WebSocket sessions are initially in memory only; the runtime persists them on the first non-empty `user_input`, so opening the app, switching workspaces, or clicking new chat must not create empty session files.
- `session/` provides session records, transcript writing, session listing, and model-context recovery.
- `memory/` provides a transparent file-backed Session/Task Memory first slice under `.codex-mini/memory/`: session summaries, task-state extraction, list/search/forget controls, explicit preference recording, and automatic summarization when leaving an active persisted session. Durable memory is not automatically injected into model prompts yet, and the experimental preference learner is not connected to the write path.
- `observability/` provides schema-v2 structured local runtime interaction logs under `.codex-mini/logs/runtime-YYYY-MM-DD.jsonl`. Selected transcript events are mirrored with user/agent/model/tool actors; each record exposes searchable `level`, `summary`, and `payload_bytes`. The first model request in a turn records a sanitized full message snapshot, later model loops record only the changed tail, and assistant transcript persistence no longer duplicates the already-recorded `model_response`. Runtime logs are diagnostic copies only: they do not participate in recovery, omit API credentials, `reasoning_content`, and full Skill bodies, and can be disabled with `CODEX_MINI_RUNTIME_LOG_ENABLED=false`.
- `workspace/` provides the first backend workspace context and validation layer.
- WebSocket clients can open a validated workspace with `open_workspace`; switching workspaces creates a fresh session, binds the session store to `workspace.project_root`, and keeps tool execution bounded by `workspace.selected_root`.
- WebSocket clients can send `change_directory` to update `workspace.current_dir` without creating a new session, and `add_dir` to explicitly add read/write additional roots.
- Resuming a stored session requires the current v2 workspace snapshot from session metadata plus the latest `workspace_policy_changed` event, then strictly revalidates `selected_root`, `project_root`, `current_dir`, and `additional_roots` before rebuilding the tool runner.
- Project instructions are loaded from `AGENTS.md` files on the path from `project_root` to `current_dir`, share a bounded total character budget, and refresh when the active workspace boundary changes; when `current_dir` is in an external additional root, runtime does not load external project docs.
- Skills now have a local-file MVP under the top-level `skills/` domain: repo skills are discovered from `.agents/skills/**/SKILL.md` along the `project_root -> current_dir` path chain, user skills are discovered from `~/.agents/skills/**/SKILL.md`, and the system prompt only receives bounded `name / description / path` catalog metadata until a skill is explicitly selected or the model calls the read-only skill tools.
- WebSocket clients can send `list_skills`, and `user_input` can include `selected_skills`; `$skill-name` text mentions select a skill only when the name is unique in the current catalog.
- WebSocket clients can manage local file skills through explicit user control events: `get_skill`, `create_skill`, `import_skill`, `install_skill`, `install_registry_skill`, `reinstall_skill`, `update_skill`, and `delete_skill`. `list_installable_skills` returns the curated installable registry from `openai/skills` `skills/.curated`, annotates entries with local `installed` / `installed_version` / `update_available` state, and `install_registry_skill` installs one of those entries through the same GitHub archive installer path as `install_skill`. Desktop registry rows marked `update_available` can directly trigger the existing `reinstall_skill` packet using `installed_path`. GitHub registry/API and archive requests use optional `GITHUB_TOKEN` / `GH_TOKEN` authorization headers when present, but tokens are not persisted in install records or transcripts. `SKILL.md` may expose optional `version` and `metadata.icon` fields for catalog/update checks and desktop display. `dependencies.tools` is checked against the current tool registry and returned as display-only `dependency_status`; the runtime does not auto-install MCP/plugins or enable missing tools. `create_skill` can optionally use `package_template: "standard"` to scaffold standard resource directories with progressive-disclosure guidance. Local imports may read an explicit user-provided local source path, validate the package, and copy it into repo/user `.agents/skills` without adding the source directory to normal workspace permissions. Local imports and GitHub installs write `.skill-install.json` source metadata for installer/update flows; GitHub install/reinstall downloads an archive, extracts only the requested package path, validates it, rejects symlinks/path traversal, and never executes remote scripts. Clients can also list, read, save, and delete standard skill resource files through `list_skill_resources`, `get_skill_resource`, `save_skill_resource`, and `delete_skill_resource`. Skill writes remain limited to repo/user `.agents/skills` roots and model-facing skill tools stay read-only.
- Full `SKILL.md` and bundled skill resources are read through `read_skill` / `read_skill_resource`; user skill files do not become normal workspace roots, and skill content is omitted from transcript/recovered context after the active turn.
- Project-local policy config is gated by user-side trust: default `session_only` workspaces do not load `.codex-mini/config.toml`; trusted projects can load the current whitelist-only permission, exec policy, and tests config, while sensitive keys, broad allow rules, and dangerous test commands are rejected.
- WebSocket clients can send `trust_workspace` / `untrust_workspace` to explicitly update the user-side trust store for the current project; the runtime reloads workspace policy and emits `workspace_policy_changed`.
- WebSocket clients can send `set_permission_mode` to switch the global runtime permission mode between `request_approval`, `auto_approve`, `full_access`, and `custom`; the backend stores it on the WebSocket context and reapplies it on workspace switch, new session, and session resume, while the desktop client persists the preference in localStorage.
- Stage 5 `Permission Mode / Sandbox Policy Lite` has its first implementation slice: the permission dialog shows the reason, cwd, permission profile, network/sandbox state, and suggested session prefix; `full_access` has explicit risk treatment in the composer and approval UI; and non-macOS, missing Seatbelt, and nested-sandbox failures return actionable explanations. Persistent allowlists, a policy editor, domain network rules, and Git commit/PR automation remain out of scope for this slice.
- `run_command` supports an optional `cwd`, but it is only a command working directory inside the current filesystem policy, not a hidden permission expansion.
- `run_tests` runs project tests through the same command executor, filesystem policy, network policy, and sandbox mode as `run_command`, and requires approval because tests execute project code.
- `update_plan` and `create_task_list` persist runtime task state under `.codex-mini/tasks.json`; they require approval even though they are not source-code edits.
- WebSocket clients can send `plan_request` to generate a pending plan document without starting an implementation turn, then `plan_confirm` to execute the original user request through the normal `user_input` path, or `plan_cancel` to discard the pending plan.
- The current Plan Mode slice has backend state, transcript events, desktop store plumbing, a persistent composer Plan Mode toggle, an assistant-visible plan document for user review, a lightweight confirm/cancel action card, and confirmed-plan document context injected into model execution and session recovery; plan editing, read-only exploratory planning turns, and stricter step-level execution constraints are not productized yet.
- Stage 4 patch review has been closed as `v0.4.0 Change Review Runtime`: `patch/` provides immutable patch proposal models, unified diff construction, add/update/delete line statistics, transparent JSON persistence, status transitions, and focused unit tests. `write_file` and `edit_file` now both support `dry_run=true` previews backed by patch proposals, and `apply_patch` / `reject_patch` / `rollback_patch` can apply, reject, or roll back stored proposals through the normal tool approval path. WebSocket patch lifecycle events are wired for proposed / approval request / applied / rejected / failed / rolled_back states and are persisted to transcript. The desktop client has a live `PatchReviewCard.vue` for pending patch review with file list, unified diff, direct apply/reject control packets, apply selected, session resume recovery, optional post-apply test inputs, and apply-failure diagnostics; it also has `PatchResultCard.vue` for independent patch test history display after apply. Stage 4.5 read-only Git review tools are available as `git_status`, `git_diff`, `git_diff_file`, and `git_changed_files`. Stage 4.6 supports explicit `test_command` / `test_timeout`, trusted `.codex-mini/config.toml` `[tests] command` / `timeout`, and AGENTS.md-declared safe default test commands; post-apply tests run through the existing command executor and sandbox boundary, and success/failure/blocked diagnostics are associated with `patch_id` plus the applied changed paths. Stage 4.7 records durable rollback metadata plus partially-written diagnostics (`written_paths` / `rolled_back_paths`, `failed_path`, `remaining_paths`) when apply or rollback fails mid-loop, has dedicated rollback/apply regression coverage for symlink/binary plus `.git` / `.codex-mini` / `.venv` / `__pycache__` protected paths, covers `move_path` / rename + protected path combinations, covers `cross-root move_path` including multi-step partial apply + rollback, simultaneous multi-file moves, multi-writable-additional-root combinations, multi-root failure diagnostics, and partial-apply rollback-failure state reconciliation, covers rename + apply/rollback failure-diagnostics combinations, rejects rename target / restore-path overwrite conflicts, and supports writable-vs-read-only `additional root` patch boundaries plus `additional root + symlink/binary` combinations with the macOS parent-symlink validation bug fixed.
- The default model-facing write path is now Patch Preview: `write_file` and `edit_file` default to `dry_run=true`; explicit `dry_run=false` remains only as a compatibility escape hatch, and the system prompt requires preview/apply unless the user explicitly requests bypassing review.
- The overall runtime architecture is now modularized, and the first `WorkspaceContext v2` slice is implemented: `selected_root`, `project_root`, `current_dir`, session-only trust, additional root model, and workspace snapshots are the canonical workspace shape. The old `root` / `allowed_roots` payload fields are not emitted and are not accepted during session resume.
- Session-scoped command allow rules are written through `permission_decision` transcript events with a workspace snapshot, and resume only restores those rules when the saved permission workspace matches the restored workspace safety boundary; global permission mode is not part of that boundary.
- `security/permissions.py` now contains the first unified filesystem/permission primitives: `FileSystemPolicy`, `PermissionProfile`, `ApprovalPolicy`, and `NetworkPolicy`; `security/exec_policy.py` contains prefix-based command rules and session allow support.
- `docs/WORKSPACE_PERMISSION_ARCHITECTURE.md` is the target design for the remaining permission work: project docs layering, project-trust config loading, project-local policy persistence, and sandbox hardening.
- WebSocket clients can create a new session, list existing sessions, resume a stored session, and request a conversation title generated from the first user question.
- Conversation titles should be generated by the configured model from the first user message, then cleaned and truncated; frontend previews may temporarily show a first-message placeholder while the request is pending.
- DeepSeek model requests use the official thinking parameters: `off` sends `thinking.disabled`, enabled thinking sends `thinking.enabled`, and DeepSeek-compatible effort values map `low/medium` to `high` and `xhigh/max` to `max`.
- Model options are not purely hard-coded: when `MODEL_PROVIDER=deepseek`, the runtime should prefer `${API_BASE:-https://api.deepseek.com}/models` with the generic `API_KEY`; if that fails, fall back only to `MODEL_OPTIONS`. Do not add a provider prefix to model ids returned by `/models`.
- The desktop client is an Electron/Vue shell for chat, tool timeline, approvals, command output, Markdown rendering, and session navigation.
- The desktop client has native directory picker entries for opening a workspace, changing current directory, and adding an explicit additional root.
- The desktop title bar shows current workspace trust and provides minimal trust/untrust controls; the chat composer owns the permission mode switcher, while the main sidebar `插件` entry owns the Skills catalog, selection, and lifecycle management panel.
- The latest full local validation passes 407 unit tests; desktop TypeScript/Vue typecheck and production build also pass.

## Important Files

- `agent_loop.py`: CLI agent loop
- `prompts/`: 系统提示词模块
  - `prompts/base.py`: 模板加载器（编译时加载 Markdown 模板）
  - `prompts/builder.py`: 提示词组装器
  - `prompts/templates/`: Markdown 提示词模板（核心提示词、个性化、工具指引）
- `server/app.py`: FastAPI WebSocket transport and request dispatch
- `server/protocol/events.py`: event envelope and client packet parsing
- `server/protocol/serialization.py`: object-to-dict conversion for SDK response objects
- `server/runtime/model_config.py`: model name, API key/base, and reasoning-effort request config
- `server/runtime/model_stream.py`: LiteLLM streaming delta and tool-call assembly helpers
- `server/runtime/session_state.py`: in-memory WebSocket session state and delayed persistence
- `server/runtime/turn_runner.py`: streaming model turn loop, tool calls, permission wait, and tool-result events
- `server/runtime/websocket_context.py`: mutable WebSocket runtime context for workspace/session/store/registry state
- `server/runtime/transcript_events.py`: transcript event helpers and denied-tool result shaping
- `server/runtime/session_allowlist.py`: recover session-scoped command allow rules from permission transcript events
- `server/processors/request_dispatcher.py`: WebSocket control-packet dispatcher
- `server/processors/skill_processor.py`: WebSocket skill catalog and management request processor
- `server/processors/title_processor.py`: low-cost conversation title generation
- `server/views/session_summary.py`: stored-session summary and display-message projection
- `context/`: model-visible context fragments for environment, permissions, model, and user input
- `context_manager/`: model history container and context update/truncation helpers
- `memory/`: transparent file-backed memory models, storage, summarization, indexing, and keyword search
- `observability/runtime_log.py`: append-only per-workspace JSONL interaction logger and model-message sanitizer
- `patch/`: Stage 4 patch proposal models, diff construction, and pending patch JSON store
- `skills/`: local file-based skills metadata, package spec/validator, install metadata, curated installable registry, loader, catalog renderer, selection logic, manager cache, restricted resource resolver, and explicit user skill management
- `workspace/models.py`: workspace context types
- `workspace/validator.py`: safe workspace root validation
- `workspace/manager.py`: workspace opening and session-store binding
- `workspace/instructions.py`: project `AGENTS.md` layered loading from project root to current dir
- `workspace/trust.py`: user-side trusted project store
- `workspace/project_config.py`: trust-gated project-local permission and exec policy config loader
- `session/models.py`: durable session and transcript event types
- `session/store.py`: SQLite session index and transcript access
- `session/transcript.py`: append-only JSONL transcript writer/reader
- `session/recovery.py`: rebuild model messages from transcript events
- `session/turn_context.py`: per-turn runtime context passed into tool execution
- `tools/core/`: tool protocol, pure registry, router, runner, catalog, and shared types
- `tools/filesystem/`: read/write/edit file tools
- `tools/search/grep.py`: code search tool
- `tools/git/`: read-only Git review tools (`git_status`, `git_diff`, `git_diff_file`, `git_changed_files`)
- `tools/shell/run_command.py`: sandboxed command tool
- `tools/shell/process_control.py`: managed process start/status/stop tool adapters
- `processes/manager.py`: workspace-scoped process registry, log access, port ownership checks, and safe process-group shutdown
- `tools/network/web_fetch.py`: public HTTP/HTTPS web fetch tool
- `tools/interaction/ask_user.py`: model-initiated clarification tool
- `tools/patching/`: mutating patch review tools for applying, rejecting, or rolling back stored patch proposals
- `tools/skills/`: read-only wrappers for `read_skill` and `read_skill_resource`
- `security/policy.py`: path and command policy
- `security/exec_policy.py`: command prefix allow/ask/deny rules and suggestions
- `security/permissions.py`: filesystem policy, permission profile, and approval policy primitives
- `security/circuit_breaker.py`: rejection counter
- `sandbox/macos_executor.py`: macOS Seatbelt command executor
- `docs/PROJECT_ARCHITECTURE_STATUS.md`: detailed architecture/status log
- `docs/RUNTIME_LOGGING.md`: runtime log location, event schema, safety boundary, and usage
- `docs/STAGE4_CHANGE_REVIEW_RUNTIME.md`: Stage 4 Change Review Runtime execution plan
- `docs/SKILLS_ARCHITECTURE.md`: current Skills module architecture, loading flow, runtime injection flow, management flow, and safety boundaries
- `docs/SKILLS_QUICKSTART.md`: minimal run/use guide for creating, selecting, and using skills
- `docs/SKILL_AUTHORING_GUIDE.md`: current project guide for writing local file skills
- `docs/examples/skills/standard-package/`: validator-covered example standard skill package
- `docs/WORKSPACE_PERMISSION_ARCHITECTURE.md`: target workspace and permission architecture, plus current implementation gap
- `docs/PRODUCTIZATION_ROADMAP.md`: stage 4 productization roadmap

## Behavior Notes

- `read_file` and `grep` are read-only helpers.
- `read_skill` and `read_skill_resource` are read-only skill-context helpers; they must only read currently cataloged skills or resources inside the selected skill directory, and they must not expand `FileSystemPolicy`.
- `write_file` and `edit_file` default to `dry_run=true` and create patch proposals without touching source. Explicit `dry_run=false` still mutates the real repo as a compatibility path and must remain protected.
- `patch/` provides the review-domain backend primitives; `tools/patching/` currently applies, rejects, or rolls back stored proposals, including selected file subsets, and `apply_patch` can run an explicitly supplied, trusted-config, or AGENTS.md-declared safe post-apply `test_command` through the normal command executor / sandbox path while storing diagnostics on the patch metadata. Apply/rollback preflight uses `git apply --check` when available, rejects symlink and binary / non-UTF-8 targets, and persists partially-written diagnostics (`written_paths` / `rolled_back_paths`, `failed_path`, `remaining_paths`) if a write loop fails mid-way. `server/runtime/turn_runner.py` emits transcript-backed WebSocket patch lifecycle events, and `server/views/session_summary.py` restores pending patch review on session resume. Desktop has a live `PatchReviewCard.vue` for pending diff review and `PatchResultCard.vue` for independent patch test history.
- `tools/git/` exposes read-only review helpers for status, scoped diff, single-file diff, and changed-file listing; it does not commit, push, checkout, or create PRs.
- `run_command` is the riskiest path and must keep going through policy; it uses macOS Seatbelt except in explicit `full_access` mode.
- `run_command` must return `ToolResult(ok=False)` for every non-zero exit code, including timeout 124; timeout stdout/stderr must be decoded to text before entering tool results or transcripts.
- `run_command` must reject shell-managed backgrounding (`nohup` or standalone `&`). Long-running services use `start_process`; raw `kill` / `pkill` / `killall` commands stay denied, and only `stop_process` may terminate a Runtime-owned process id.
- `run_tests` also executes code and must stay on the sandboxed command-executor path instead of using raw subprocess calls.
- `run_command` may auto-allow narrow read-only exploration commands, including safe `find ... | sort` pipelines and read-only Git queries such as `git status`, `git log`, and `git diff`; mutating Git commands, mutating `find` options, parent/external paths, redirection, and shell chaining still require approval.
- Permission modes are explicit global runtime choices: `request_approval` asks before mutations, `auto_approve` auto-approves non-dangerous workspace operations, `full_access` disables Seatbelt and enables network access, and `custom` uses trusted `.codex-mini/config.toml`.
- `web_fetch` can fetch normal public HTTP/HTTPS pages without approval, but must reject localhost, private IPs, non-public resolved addresses, and non-Web protocols.
- Protected paths such as `.env`, `.git`, `.codex-mini`, `.venv`, and `__pycache__` should not be written; `.env` should not be read by file tools.
- `.codex-mini/sessions/` contains local runtime state and should be treated as generated data, not product source.
- `.codex-mini/logs/` contains raw local interaction diagnostics and must not be committed. It may contain user messages, ordinary tool results, source snippets, and command output even though structured credentials, hidden reasoning, and full Skill content are omitted.
- Runtime log schema v2 keeps the first `model_request` snapshot in each turn complete and writes later requests as `message_snapshot.mode=delta`; consumers reconstruct a delta by retaining the first `base_message_count` messages from the previous request and replacing its tail with the logged `messages` array.
- Do not persist or restore `reasoning_content` into recovered model context. Active in-memory turns may keep assistant `reasoning_content` only when that assistant message has `tool_calls`, because DeepSeek requires it for later tool-call context stitching.
- Do not persist full `SKILL.md` bodies or skill resource contents as durable memory; transcript should keep `skill_used` / `skill_warning` events and omit full skill tool content from recovery.
- Keep the desktop client thin: Python runtime owns tool execution, policy, transcript persistence, and recovery.
- Keep `server/app.py` thin: protocol shaping, model request config, session-state helpers, request dispatch, turn execution, title generation, transcript helpers, and session views should stay in their dedicated `server/*` modules.
- Do not add compatibility glue for old protocols, old schemas, or transitional payloads unless the user explicitly approves a migration plan. The project should support the current contract clearly instead of accumulating fallback branches.
- Follow minimum responsibility boundaries: each module should have one clear reason to change, and new logic should live in the domain that owns it rather than being mixed into transport, UI, persistence, or policy code.
- Keep architecture boundaries explicit. Avoid "just make it work" coupling where request dispatch, model runtime, session persistence, workspace policy, and desktop presentation know each other's internals.
- Treat workspace roots as user-selected safety boundaries. Command cwd support must stay inside the active filesystem policy, and extra allowed directories must not be added silently.
- Treat session resume as a permission-boundary restore path: missing or invalid v2 workspace fields return `workspace_error`; do not add legacy fallback, warning-based repair, or silent migration for old session data.
- Stage 3 should stay AGENTS.md-first, matching Codex's project-doc pattern: use layered `AGENTS.md` plus existing workspace/env context, and do not add project profile persistence, root marker hints, git summary precomputation, or full-repo scans at workspace open.
- When discussing current workspace/permission behavior, distinguish the implemented v2 slices from the full target design in `docs/WORKSPACE_PERMISSION_ARCHITECTURE.md`.
- When adding or updating code comments, docstrings, or inline implementation notes, write them in Chinese. Add concise Chinese comments for complex control flow, safety boundaries, and non-obvious design intent, but avoid redundant comments for self-explanatory code.

## Current Plan

1. Keep the runtime contract stable.
   - baseline event schema, tool metadata, permission flow, session suspension, resume behavior, streaming, session listing, and durable recovery are in place
2. Continue the desktop client shell.
   - use TypeScript + Node + Electron/Vue to render chat, tool timeline, diffs, approvals, Markdown, session history, and command output
3. Reuse the same runtime protocol for an IDE extension later.
   - keep the client thin and let the Python runtime own tool execution
4. Make the backend more product-ready.
   - extend the current workspace/filesystem/exec policy into persistent project policy editing, tool scheduling, checkpoint/restore, and cross-session allowlist persistence
5. Add integration coverage.
   - desktop client smoke tests, runtime event tests, durable session recovery tests, WebSocket integration tests, and macOS sandbox regression tests

## Useful Commands

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall agent_loop.py tools security sandbox server session workspace skills patch memory observability processes tests
make run
make run-server
make clean-sessions
```

## Working Rule

If this file and `docs/PROJECT_ARCHITECTURE_STATUS.md` diverge, update both together.

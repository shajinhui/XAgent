# AGENTS.md

This file is the shared context for future agent conversations in this repo.

## Project Snapshot

- Project: Codex-mini
- Goal: a Python Codex-like local agent runtime with a desktop client and optional IDE extension
- Current stage: stage 2 alpha, desktop runtime bridge and durable session baseline in progress
- Main focus: modular tools, safety policy, macOS native sandbox command execution, local event protocol, durable transcripts, and desktop-client runtime UX

## Current Status

- `agent_loop.py` already uses `ToolRegistry` and LangGraph orchestration.
- Tools are split into individual modules under `tools/`.
- `run_command` goes through `security/` and `sandbox/macos_executor.py`.
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
- `workspace/` provides the first backend workspace context and validation layer.
- WebSocket clients can open a validated workspace with `open_workspace`; switching workspaces creates a fresh session, binds the session store to `workspace.project_root`, and keeps tool execution bounded by `workspace.selected_root`.
- WebSocket clients can send `change_directory` to update `workspace.current_dir` without creating a new session, and `add_dir` to explicitly add read/write additional roots.
- Resuming a stored session requires the current v2 workspace snapshot from session metadata plus the latest `workspace_policy_changed` event, then strictly revalidates `selected_root`, `project_root`, `current_dir`, and `additional_roots` before rebuilding the tool runner.
- Project instructions are loaded from `AGENTS.md` files on the path from `project_root` to `current_dir`; when `current_dir` is in an external additional root, runtime does not load external project docs.
- Project-local policy config is gated by user-side trust: default `session_only` workspaces do not load `.codex-mini/config.toml`; trusted projects can load the current whitelist-only permission and exec policy config, while sensitive keys and broad allow rules are rejected.
- WebSocket clients can send `trust_workspace` / `untrust_workspace` to explicitly update the user-side trust store for the current project; the runtime reloads workspace policy and emits `workspace_policy_changed`.
- WebSocket clients can send `set_permission_mode` to switch the global runtime permission mode between `request_approval`, `auto_approve`, `full_access`, and `custom`; the backend stores it on the WebSocket context and reapplies it on workspace switch, new session, and session resume, while the desktop client persists the preference in localStorage.
- `run_command` supports an optional `cwd`, but it is only a command working directory inside the current filesystem policy, not a hidden permission expansion.
- `run_tests` runs project tests through the same command executor, filesystem policy, network policy, and sandbox mode as `run_command`, and requires approval because tests execute project code.
- `update_plan` and `create_task_list` persist runtime task state under `.codex-mini/tasks.json`; they require approval even though they are not source-code edits.
- WebSocket clients can send `plan_request` to generate a pending task plan without starting a model turn, then `plan_confirm` to execute the original user request through the normal `user_input` path, or `plan_cancel` to discard the pending plan.
- The current Plan Mode slice has backend state, transcript events, desktop store plumbing, a composer plan action, and a lightweight desktop review card for confirm/cancel; plan editing and execution-mode constraints are not productized yet.
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
- The desktop title bar shows current workspace trust and provides minimal trust/untrust controls; the chat composer owns the permission mode switcher.
- Basic unit tests exist in `tests/`.

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
- `server/processors/title_processor.py`: low-cost conversation title generation
- `server/views/session_summary.py`: stored-session summary and display-message projection
- `context/`: model-visible context fragments for environment, permissions, model, and user input
- `context_manager/`: model history container and context update/truncation helpers
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
- `tools/shell/run_command.py`: sandboxed command tool
- `tools/network/web_fetch.py`: public HTTP/HTTPS web fetch tool
- `tools/interaction/ask_user.py`: model-initiated clarification tool
- `security/policy.py`: path and command policy
- `security/exec_policy.py`: command prefix allow/ask/deny rules and suggestions
- `security/permissions.py`: filesystem policy, permission profile, and approval policy primitives
- `security/circuit_breaker.py`: rejection counter
- `sandbox/macos_executor.py`: macOS Seatbelt command executor
- `docs/PROJECT_ARCHITECTURE_STATUS.md`: detailed architecture/status log
- `docs/WORKSPACE_PERMISSION_ARCHITECTURE.md`: target workspace and permission architecture, plus current implementation gap
- `docs/PRODUCTIZATION_ROADMAP.md`: stage 4 productization roadmap

## Behavior Notes

- `read_file` and `grep` are read-only helpers.
- `write_file` and `edit_file` mutate the real repo and should stay protected; `edit_file` supports `dry_run=true` to preview a unified diff without writing.
- `run_command` is the riskiest path and must keep going through policy; it uses macOS Seatbelt except in explicit `full_access` mode.
- `run_tests` also executes code and must stay on the sandboxed command-executor path instead of using raw subprocess calls.
- `run_command` may auto-allow narrow read-only exploration commands, including safe `find ... | sort` pipelines and read-only Git queries such as `git status`, `git log`, and `git diff`; mutating Git commands, mutating `find` options, parent/external paths, redirection, and shell chaining still require approval.
- Permission modes are explicit global runtime choices: `request_approval` asks before mutations, `auto_approve` auto-approves non-dangerous workspace operations, `full_access` disables Seatbelt and enables network access, and `custom` uses trusted `.codex-mini/config.toml`.
- `web_fetch` can fetch normal public HTTP/HTTPS pages without approval, but must reject localhost, private IPs, non-public resolved addresses, and non-Web protocols.
- Protected paths such as `.env`, `.git`, `.codex-mini`, `.venv`, and `__pycache__` should not be written; `.env` should not be read by file tools.
- `.codex-mini/sessions/` contains local runtime state and should be treated as generated data, not product source.
- Do not persist or restore `reasoning_content` into recovered model context. Active in-memory turns may keep assistant `reasoning_content` only when that assistant message has `tool_calls`, because DeepSeek requires it for later tool-call context stitching.
- Keep the desktop client thin: Python runtime owns tool execution, policy, transcript persistence, and recovery.
- Keep `server/app.py` thin: protocol shaping, model request config, session-state helpers, request dispatch, turn execution, title generation, transcript helpers, and session views should stay in their dedicated `server/*` modules.
- Do not add compatibility glue for old protocols, old schemas, or transitional payloads unless the user explicitly approves a migration plan. The project should support the current contract clearly instead of accumulating fallback branches.
- Follow minimum responsibility boundaries: each module should have one clear reason to change, and new logic should live in the domain that owns it rather than being mixed into transport, UI, persistence, or policy code.
- Keep architecture boundaries explicit. Avoid "just make it work" coupling where request dispatch, model runtime, session persistence, workspace policy, and desktop presentation know each other's internals.
- Treat workspace roots as user-selected safety boundaries. Command cwd support must stay inside the active filesystem policy, and extra allowed directories must not be added silently.
- Treat session resume as a permission-boundary restore path: missing or invalid v2 workspace fields return `workspace_error`; do not add legacy fallback, warning-based repair, or silent migration for old session data.
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
.venv/bin/python -m compileall agent_loop.py tools security sandbox server session workspace tests
make run
make run-server
make clean-sessions
```

## Working Rule

If this file and `docs/PROJECT_ARCHITECTURE_STATUS.md` diverge, update both together.

# AGENTS.md

This file is the shared context for future agent conversations in this repo.

## Project Snapshot

- Project: Codex-mini
- Goal: a Python Codex-like local agent runtime with a desktop client and optional IDE extension
- Current stage: stage 2 alpha, runtime-contract baseline complete
- Main focus: modular tools, safety policy, Docker command execution, local event protocol, desktop-client-ready runtime

## Current Status

- `agent_loop.py` already uses `ToolRegistry` and LangGraph orchestration.
- Tools are split into individual modules under `tools/`.
- `run_command` goes through `security/` and `sandbox/docker_executor.py`.
- Docker now mounts the real project workspace, so allowed command-side file changes land in the repo.
- `server/app.py` is a local event transport / runtime bridge prototype, not the final product UI.
- WebSocket events now carry a schema version and session state.
- Assistant output uses LiteLLM streaming in the WebSocket path instead of simulated word chunks.
- Permission requests support approve/deny acknowledgements and approved tool retry.
- Suspended sessions now block new turns until the client sends `resume_session`.
- Mutating tools require explicit approval before they write or run commands.
- Basic unit tests exist in `tests/`.

## Important Files

- `agent_loop.py`: CLI agent loop
- `server/app.py`: FastAPI WebSocket service
- `tools/registry.py`: tool registration and execution
- `tools/types.py`: shared tool runtime types
- `tools/read_file.py`
- `tools/write_file.py`
- `tools/edit_file.py`
- `tools/grep.py`
- `tools/run_command.py`
- `tools/web_fetch.py`
- `security/policy.py`: path and command policy
- `security/circuit_breaker.py`: rejection counter
- `sandbox/docker_executor.py`: Docker command executor
- `docs/PROJECT_ARCHITECTURE_STATUS.md`: detailed architecture/status log

## Behavior Notes

- `read_file` and `grep` are read-only helpers.
- `write_file` and `edit_file` mutate the real repo and should stay protected.
- `run_command` is the riskiest path and must keep going through policy + Docker.
- `web_fetch` stays opt-in.
- Protected paths such as `.env`, `.git`, `.venv`, and `__pycache__` should not be written.

## Current Plan

1. Keep the runtime contract stable.
   - baseline event schema, tool metadata, permission flow, session suspension, resume behavior, and streaming are in place
2. Build the desktop client shell first.
   - use TypeScript + Node + Electron to render chat, tool timeline, diffs, approvals, and command output
3. Reuse the same runtime protocol for an IDE extension later.
   - keep the client thin and let the Python runtime own tool execution
4. Make the backend more product-ready.
   - configurable policy, tool scheduling, `edit_file` dry-run, checkpoint/restore, cancellation/backpressure
5. Add integration coverage.
   - desktop client smoke tests, runtime event tests, and Docker sandbox regression tests

## Useful Commands

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall agent_loop.py tools security sandbox server tests
make run
make run-server
```

## Working Rule

If this file and `docs/PROJECT_ARCHITECTURE_STATUS.md` diverge, update both together.

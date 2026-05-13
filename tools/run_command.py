from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from tools.types import ToolExecutionContext, ToolMeta, ToolPermissionError


META = ToolMeta(
    name="run_command",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


class RunCommandArgs(BaseModel):
    command: str = Field(..., description="要执行的 shell 命令")
    timeout: int = Field(20, ge=1, le=120, description="超时时间（秒）")
    cwd: str | None = Field(
        None,
        description="可选命令工作目录，必须位于当前 workspace 内部；默认使用 workspace 根目录",
    )


def schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在 macOS 原生沙箱中执行 shell 命令",
            "parameters": RunCommandArgs.model_json_schema(),
        },
    }


def run(ctx: ToolExecutionContext, payload: dict) -> str:
    args = RunCommandArgs(**payload)
    raw_cwd = args.cwd.strip() if args.cwd else None

    try:
        command_cwd = ctx.policy.resolve_command_cwd(raw_cwd)
    except (PermissionError, ValueError) as exc:
        raise ToolPermissionError(
            f"命令工作目录无效: {exc}",
            metadata={
                "error_type": "permission_denied",
                "permission_action": "deny",
                "category": "command_cwd",
                "command": args.command,
                "cwd": raw_cwd or ".",
            },
        ) from exc

    approved = bool(payload.get("_approved", False))
    decision = ctx.policy.check_command(args.command, approved=approved)
    if decision.requires_approval:
        raise ToolPermissionError(
            f"命令需要用户确认: {decision.reason}",
            metadata={
                "error_type": "permission_required",
                "permission_action": "ask",
                "category": decision.category,
                "command": args.command,
                "cwd": _display_cwd(ctx, command_cwd),
            },
        )

    if not decision.allowed:
        suspended = ctx.circuit_breaker.record_rejection(ctx.session_id, decision.category)
        count = ctx.circuit_breaker.count(ctx.session_id, decision.category)
        message = f"命令被拒绝: {decision.reason} (连续拒绝 {count}/3)"
        metadata = {
            "error_type": "permission_denied",
            "permission_action": "deny",
            "category": decision.category,
            "rejection_count": count,
            "session_suspended": suspended,
            "command": args.command,
            "cwd": _display_cwd(ctx, command_cwd),
        }
        if suspended:
            message += "\n会话已自动挂起，请用户确认后恢复。"
        raise ToolPermissionError(message, metadata=metadata)

    if not approved:
        raise ToolPermissionError(
            f"命令需要用户确认: {args.command}",
            metadata={
                "error_type": "permission_required",
                "permission_action": "ask",
                "category": "command_approval",
                "command": args.command,
                "cwd": _display_cwd(ctx, command_cwd),
            },
        )

    result = ctx.command_executor.run(args.command, timeout_seconds=args.timeout, cwd=command_cwd)
    if result.ok:
        ctx.circuit_breaker.record_success(ctx.session_id, "dangerous_shell")

    return (
        f"cwd: {_display_cwd(ctx, command_cwd)}\n"
        f"exit_code: {result.exit_code}\n"
        f"stdout:\n{result.stdout.strip() or '(empty)'}\n"
        f"stderr:\n{result.stderr.strip() or '(empty)'}"
    )


def _display_cwd(ctx: ToolExecutionContext, cwd: Path) -> str:
    try:
        relative = cwd.relative_to(ctx.project_root)
    except ValueError:
        return cwd.as_posix()
    if relative.as_posix() == ".":
        return "."
    return relative.as_posix()

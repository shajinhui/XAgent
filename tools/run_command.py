from __future__ import annotations

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


def schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在 Docker 沙箱中执行 shell 命令",
            "parameters": RunCommandArgs.model_json_schema(),
        },
    }


def run(ctx: ToolExecutionContext, payload: dict) -> str:
    args = RunCommandArgs(**payload)

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
            },
        )

    result = ctx.docker_executor.run(args.command, timeout_seconds=args.timeout)
    if result.ok:
        ctx.circuit_breaker.record_success(ctx.session_id, "dangerous_shell")

    return (
        f"exit_code: {result.exit_code}\n"
        f"stdout:\n{result.stdout.strip() or '(empty)'}\n"
        f"stderr:\n{result.stderr.strip() or '(empty)'}"
    )

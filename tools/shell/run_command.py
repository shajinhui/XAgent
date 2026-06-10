"""通过安全策略和 macOS Seatbelt 执行命令的高风险工具。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from security import ApprovalPolicy
from security.exec_policy import CommandDecision
from tools.core.types import ToolExecutionContext, ToolMeta, ToolPermissionError


META = ToolMeta(
    name="run_command",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


class RunCommandArgs(BaseModel):
    """run_command 工具入参。"""

    command: str = Field(..., description="要执行的 shell 命令")
    timeout: int = Field(20, ge=1, le=120, description="超时时间（秒）")
    cwd: str | None = Field(
        None,
        description="可选命令工作目录，必须位于当前 filesystem policy 的可写目录；默认使用 current_dir",
    )


def schema() -> dict:
    """返回供模型调用的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在 macOS 原生沙箱中执行 shell 命令",
            "parameters": RunCommandArgs.model_json_schema(),
        },
    }


def run(ctx: ToolExecutionContext, payload: dict) -> str:
    """执行命令前完成 cwd 校验、命令策略判断和用户审批检查。"""

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
        # 非白名单命令先返回 ask，让 WebSocket 层弹出审批，而不是直接执行。
        raise ToolPermissionError(
            f"命令需要用户确认: {decision.reason}",
            metadata=_command_metadata(ctx, args.command, command_cwd, decision, "permission_required", "ask"),
        )

    if not decision.allowed:
        # 明确 deny 的命令会累计拒绝次数，达到阈值后挂起会话。
        suspended = ctx.circuit_breaker.record_rejection(ctx.session_id, decision.category)
        count = ctx.circuit_breaker.count(ctx.session_id, decision.category)
        message = f"命令被拒绝: {decision.reason} (连续拒绝 {count}/3)"
        metadata = _command_metadata(ctx, args.command, command_cwd, decision, "permission_denied", "deny")
        metadata.update(
            {
                "rejection_count": count,
                "session_suspended": suspended,
            }
        )
        if suspended:
            message += "\n会话已自动挂起，请用户确认后恢复。"
        raise ToolPermissionError(message, metadata=metadata)

    if not approved and decision.approval_required:
        # 即便是白名单命令，run_command 本身仍是 mutating/高风险入口，需要用户确认。
        if ctx.approval_policy == ApprovalPolicy.NEVER:
            raise ToolPermissionError(
                f"命令被拒绝: 当前 approval policy 禁止请求用户批准: {args.command}",
                metadata=_command_metadata(
                    ctx,
                    args.command,
                    command_cwd,
                    decision,
                    "permission_denied",
                    "deny",
                    category="approval_unavailable",
                ),
            )
        raise ToolPermissionError(
            f"命令需要用户确认: {args.command}",
            metadata=_command_metadata(
                ctx,
                args.command,
                command_cwd,
                decision,
                "permission_required",
                "ask",
                category="command_approval",
            ),
        )

    result = ctx.command_executor.run(
        args.command,
        filesystem_policy=ctx.filesystem_policy,
        network_policy=ctx.network_policy,
        timeout_seconds=args.timeout,
        cwd=command_cwd,
    )
    if result.ok:
        ctx.circuit_breaker.record_success(ctx.session_id, "dangerous_shell")

    return (
        f"cwd: {_display_cwd(ctx, command_cwd)}\n"
        f"exit_code: {result.exit_code}\n"
        f"stdout:\n{result.stdout.strip() or '(empty)'}\n"
        f"stderr:\n{result.stderr.strip() or '(empty)'}"
    )


def _display_cwd(ctx: ToolExecutionContext, cwd: Path) -> str:
    """把 cwd 转成前端更容易阅读的相对路径。"""

    try:
        relative = cwd.relative_to(ctx.selected_root)
    except ValueError:
        return cwd.as_posix()
    if relative.as_posix() == ".":
        return "."
    return relative.as_posix()


def _command_metadata(
    ctx: ToolExecutionContext,
    command: str,
    cwd: Path,
    decision: CommandDecision,
    error_type: str,
    permission_action: str,
    *,
    category: str | None = None,
) -> dict:
    metadata = {
        "error_type": error_type,
        "permission_action": permission_action,
        "category": category or decision.category,
        "command": command,
        "cwd": _display_cwd(ctx, cwd),
        "selected_root": ctx.selected_root.as_posix(),
        "current_dir": ctx.current_dir.as_posix(),
        "permission_profile": ctx.permission_profile.value,
        "approval_policy": ctx.approval_policy.value,
        "network_policy": ctx.network_policy.value,
    }
    if decision.suggested_prefix_rule:
        metadata["suggested_prefix_rule"] = list(decision.suggested_prefix_rule)
    if decision.matched_prefix_rule:
        metadata["matched_prefix_rule"] = list(decision.matched_prefix_rule)
    return metadata

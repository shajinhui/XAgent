"""受管理后台进程的启动、状态查询和安全停止工具。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from processes import ManagedProcessError
from security import ApprovalPolicy
from security.exec_policy import CommandDecision
from security.permissions import PermissionProfile
from tools.core.types import ToolExecutionContext, ToolMeta, ToolPermissionError, ToolResult
from tools.shell.command_syntax import requests_shell_backgrounding


START_META = ToolMeta(
    name="start_process",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)
STATUS_META = ToolMeta(
    name="process_status",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=True,
)
STOP_META = ToolMeta(
    name="stop_process",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


class StartProcessArgs(BaseModel):
    """启动受管理进程参数。"""

    command: str = Field(..., min_length=1, description="前台运行命令，不要包含 nohup 或 &")
    cwd: str | None = Field(None, description="可选工作目录，必须位于当前可写 filesystem policy 内")
    expected_port: int | None = Field(None, ge=1, le=65535, description="可选预期 TCP 监听端口")
    startup_timeout: int = Field(5, ge=1, le=30, description="等待预期端口就绪的秒数")


class ProcessStatusArgs(BaseModel):
    """查询受管理进程或端口状态参数。"""

    process_id: str | None = Field(None, description="可选受管理 process_id")
    port: int | None = Field(None, ge=1, le=65535, description="可选 TCP 端口")
    tail_lines: int = Field(80, ge=1, le=500, description="返回进程日志末尾行数")


class StopProcessArgs(BaseModel):
    """停止受管理进程参数。"""

    process_id: str = Field(..., min_length=1, description="start_process 返回的 process_id")
    timeout: int = Field(5, ge=1, le=30, description="SIGTERM 后等待秒数")


def start_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": START_META.name,
            "description": "由 Runtime 启动并托管后台进程，可严格验证预期端口归属",
            "parameters": StartProcessArgs.model_json_schema(),
        },
    }


def status_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": STATUS_META.name,
            "description": "查询 Runtime 管理的进程、日志尾部或任意 TCP 端口 listener",
            "parameters": ProcessStatusArgs.model_json_schema(),
        },
    }


def stop_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": STOP_META.name,
            "description": "只停止 Runtime 自己启动并登记的 process_id",
            "parameters": StopProcessArgs.model_json_schema(),
        },
    }


def start_run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    """通过现有命令策略与 sandbox 边界启动受管理进程。"""

    args = StartProcessArgs(**payload)
    raw_cwd = args.cwd.strip() if args.cwd else None
    try:
        command_cwd = ctx.policy.resolve_command_cwd(raw_cwd)
    except (PermissionError, ValueError) as exc:
        raise ToolPermissionError(
            f"命令工作目录无效: {exc}",
            metadata={"error_type": "permission_denied", "permission_action": "deny"},
        ) from exc

    if requests_shell_backgrounding(args.command):
        return ToolResult(
            ok=False,
            content="start_process 已负责后台化；command 中不能再包含 nohup 或独立的 &",
            metadata={"error_type": "nested_backgrounding", "command": args.command},
        )

    approved = bool(payload.get("_approved", False))
    decision = ctx.policy.check_command(args.command, approved=approved)
    _authorize_start(ctx, args, command_cwd, decision, approved)

    try:
        result = ctx.process_manager.start(
            args.command,
            cwd=command_cwd,
            executor=ctx.command_executor,
            filesystem_policy=ctx.filesystem_policy,
            network_policy=ctx.network_policy,
            sandbox_enabled=ctx.permission_profile != PermissionProfile.DANGER_NO_SANDBOX,
            expected_port=args.expected_port,
            startup_timeout=args.startup_timeout,
        )
    except ManagedProcessError as exc:
        return ToolResult(
            ok=False,
            content=str(exc),
            metadata={"command": args.command, "cwd": command_cwd.as_posix(), **exc.metadata},
        )

    return ToolResult(
        ok=True,
        content=json.dumps(result, ensure_ascii=False, indent=2),
        metadata=result,
    )


def status_run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    """查询进程状态、日志和端口归属，不接管外部进程。"""

    args = ProcessStatusArgs(**payload)
    try:
        result: dict[str, object] = {}
        if args.process_id:
            record = ctx.process_manager.get(args.process_id)
            result["process"] = record.as_dict()
            result["log_tail"] = ctx.process_manager.read_log(
                args.process_id,
                tail_lines=args.tail_lines,
            )
        elif args.port is None:
            result["processes"] = ctx.process_manager.list()
        if args.port is not None:
            result["port"] = args.port
            result["listeners"] = ctx.process_manager.inspect_port(args.port)
    except ManagedProcessError as exc:
        return ToolResult(ok=False, content=str(exc), metadata=exc.metadata)
    return ToolResult(ok=True, content=json.dumps(result, ensure_ascii=False, indent=2), metadata=result)


def stop_run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    """只按受管理 process_id 停止本 Runtime 持有的进程组。"""

    args = StopProcessArgs(**payload)
    try:
        result = ctx.process_manager.stop(args.process_id, timeout=args.timeout)
    except ManagedProcessError as exc:
        return ToolResult(ok=False, content=str(exc), metadata=exc.metadata)
    return ToolResult(ok=True, content=json.dumps(result, ensure_ascii=False, indent=2), metadata=result)


def _authorize_start(
    ctx: ToolExecutionContext,
    args: StartProcessArgs,
    cwd: Path,
    decision: CommandDecision,
    approved: bool,
) -> None:
    metadata = {
        "command": args.command,
        "cwd": cwd.as_posix(),
        "expected_port": args.expected_port,
        "reason": decision.reason,
        "category": decision.category,
        "permission_profile": ctx.permission_profile.value,
        "approval_policy": ctx.approval_policy.value,
        "network_policy": ctx.network_policy.value,
        "sandbox_enabled": ctx.permission_profile != PermissionProfile.DANGER_NO_SANDBOX,
    }
    if decision.suggested_prefix_rule:
        metadata["suggested_prefix_rule"] = list(decision.suggested_prefix_rule)
    if not decision.allowed:
        action = "ask" if decision.requires_approval else "deny"
        raise ToolPermissionError(
            f"受管理进程命令未获授权: {decision.reason}",
            metadata={**metadata, "error_type": "permission_required", "permission_action": action},
        )
    if approved or ctx.approval_policy == ApprovalPolicy.AUTO:
        return
    if ctx.approval_policy == ApprovalPolicy.NEVER:
        raise ToolPermissionError(
            "当前 approval policy 禁止启动后台进程",
            metadata={**metadata, "error_type": "permission_denied", "permission_action": "deny"},
        )
    raise ToolPermissionError(
        "启动受管理后台进程需要用户确认",
        metadata={**metadata, "error_type": "permission_required", "permission_action": "ask"},
    )

"""只读 Git review 工具。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tools.core.types import ToolExecutionContext, ToolMeta, ToolResult


STATUS_META = ToolMeta(
    name="git_status",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=True,
)


DIFF_META = ToolMeta(
    name="git_diff",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=True,
)


DIFF_FILE_META = ToolMeta(
    name="git_diff_file",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=True,
)


CHANGED_FILES_META = ToolMeta(
    name="git_changed_files",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=True,
)


class GitStatusArgs(BaseModel):
    """git_status 工具入参。"""

    include_untracked: bool = Field(True, description="是否展示未跟踪文件")


class GitDiffArgs(BaseModel):
    """git_diff 工具入参。"""

    cached: bool = Field(False, description="是否查看 staged diff")
    context_lines: int = Field(3, ge=0, le=20, description="diff 上下文行数")
    max_chars: int = Field(60000, ge=1000, le=200000, description="最多返回字符数")


class GitDiffFileArgs(GitDiffArgs):
    """git_diff_file 工具入参。"""

    path: str = Field(..., description="要查看 diff 的文件路径")


class GitChangedFilesArgs(BaseModel):
    """git_changed_files 工具入参。"""

    include_untracked: bool = Field(True, description="是否包含未跟踪文件")
    max_files: int = Field(200, ge=1, le=1000, description="最多返回文件数")


def status_schema() -> dict:
    """返回 git_status 的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": STATUS_META.name,
            "description": "查看当前 workspace 范围内的 Git 状态，只读",
            "parameters": GitStatusArgs.model_json_schema(),
        },
    }


def diff_schema() -> dict:
    """返回 git_diff 的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": DIFF_META.name,
            "description": "查看当前 workspace 范围内的 Git diff，只读",
            "parameters": GitDiffArgs.model_json_schema(),
        },
    }


def diff_file_schema() -> dict:
    """返回 git_diff_file 的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": DIFF_FILE_META.name,
            "description": "查看单个文件的 Git diff，只读",
            "parameters": GitDiffFileArgs.model_json_schema(),
        },
    }


def changed_files_schema() -> dict:
    """返回 git_changed_files 的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": CHANGED_FILES_META.name,
            "description": "列出当前 workspace 范围内 Git 已变更文件，只读",
            "parameters": GitChangedFilesArgs.model_json_schema(),
        },
    }


def status_run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    """读取当前 workspace 范围内的 Git status。"""

    args = GitStatusArgs(**payload)
    repo = _git_context(ctx)
    command = [
        "status",
        "--short",
        "--branch",
        "--untracked-files=all" if args.include_untracked else "--untracked-files=no",
        "--",
        repo.scope.as_posix(),
    ]
    result = _run_git(repo.root, command)
    return _tool_result(repo, "git status", result.stdout, result.stderr, result.returncode)


def diff_run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    """读取当前 workspace 范围内的 Git diff。"""

    args = GitDiffArgs(**payload)
    repo = _git_context(ctx)
    command = ["diff", "--no-ext-diff", f"--unified={args.context_lines}"]
    if args.cached:
        command.append("--cached")
    command.extend(["--", repo.scope.as_posix()])
    result = _run_git(repo.root, command)
    return _tool_result(
        repo,
        "git diff",
        _truncate(result.stdout, args.max_chars),
        result.stderr,
        result.returncode,
    )


def diff_file_run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    """读取单个文件的 Git diff，文件路径必须在 workspace 读取边界内。"""

    args = GitDiffFileArgs(**payload)
    try:
        file_path = ctx.policy.resolve_read_path(args.path)
    except Exception as exc:
        raise PermissionError(str(exc)) from exc
    repo = _git_context(ctx)
    if not _is_within(file_path, repo.root):
        raise PermissionError(f"文件不在当前 Git 仓库内: {file_path}")

    command = ["diff", "--no-ext-diff", f"--unified={args.context_lines}"]
    if args.cached:
        command.append("--cached")
    command.extend(["--", file_path.as_posix()])
    result = _run_git(repo.root, command)
    return _tool_result(
        repo,
        "git diff file",
        _truncate(result.stdout, args.max_chars),
        result.stderr,
        result.returncode,
        metadata={"path": file_path.as_posix()},
    )


def changed_files_run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    """列出当前 workspace 范围内的 Git changed files。"""

    args = GitChangedFilesArgs(**payload)
    repo = _git_context(ctx)
    command = [
        "status",
        "--porcelain=v1",
        "--untracked-files=all" if args.include_untracked else "--untracked-files=no",
        "--",
        repo.scope.as_posix(),
    ]
    result = _run_git(repo.root, command)
    all_files = _parse_changed_files(result.stdout)
    files = all_files[: args.max_files]
    content = json.dumps(
        {
            "git_root": repo.root.as_posix(),
            "scope": repo.scope.as_posix(),
            "files": files,
            "truncated": len(all_files) > args.max_files,
        },
        ensure_ascii=False,
        indent=2,
    )
    return ToolResult(
        ok=result.returncode == 0,
        content=content,
        metadata={
            "git_root": repo.root.as_posix(),
            "scope": repo.scope.as_posix(),
            "changed_files": files,
            "exit_code": result.returncode,
            "stderr": result.stderr.strip(),
        },
    )


class _GitContext(BaseModel):
    """单次 Git review 命令的仓库与展示范围。"""

    root: Path
    scope: Path

    model_config = {"arbitrary_types_allowed": True}


def _git_context(ctx: ToolExecutionContext) -> _GitContext:
    """定位 Git 根，并把命令限制在当前 workspace 可见范围内。"""

    git_root = _discover_git_root(ctx)
    scope = _review_scope(ctx, git_root)
    return _GitContext(root=git_root, scope=scope)


def _discover_git_root(ctx: ToolExecutionContext) -> Path:
    """优先使用当前目录；若当前目录是额外目录，则回退到 workspace 项目边界。"""

    candidates = [ctx.current_dir, ctx.selected_root, ctx.project_root]
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        discovery = _run_raw_git(
            ["git", "-C", resolved.as_posix(), "rev-parse", "--show-toplevel"],
            cwd=resolved,
        )
        if discovery.returncode == 0:
            return Path(discovery.stdout.strip()).expanduser().resolve()

    raise RuntimeError("当前目录不在 Git 仓库中")


def _review_scope(ctx: ToolExecutionContext, git_root: Path) -> Path:
    """选择 Git pathspec 范围，避免把用户未选择的仓库部分一起暴露。"""

    selected_root = ctx.selected_root.resolve()
    current_dir = ctx.current_dir.resolve()
    if _is_within(selected_root, git_root):
        return selected_root
    if _is_within(current_dir, git_root):
        return current_dir
    raise PermissionError("当前 Git 仓库不在 workspace 读取边界内")


def _run_git(git_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """在指定 Git 根执行只读 git 子命令。"""

    return _run_raw_git(
        ["git", "--no-pager", "-C", git_root.as_posix(), *args],
        cwd=git_root,
    )


def _run_raw_git(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """执行 git 命令；禁用交互和 pager，避免 UI 卡住。"""

    env = os.environ.copy()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "LC_ALL": "C",
        }
    )
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


def _tool_result(
    repo: _GitContext,
    label: str,
    stdout: str,
    stderr: str,
    returncode: int,
    *,
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    """把 Git 命令结果整理成统一工具输出。"""

    content = (
        f"{label}\n"
        f"git_root: {repo.root.as_posix()}\n"
        f"scope: {repo.scope.as_posix()}\n"
        f"exit_code: {returncode}\n"
        f"stdout:\n{stdout.strip() or '(empty)'}\n"
        f"stderr:\n{stderr.strip() or '(empty)'}"
    )
    result_metadata = {
        "git_root": repo.root.as_posix(),
        "scope": repo.scope.as_posix(),
        "exit_code": returncode,
    }
    if metadata:
        result_metadata.update(metadata)
    return ToolResult(ok=returncode == 0, content=content, metadata=result_metadata)


def _parse_changed_files(stdout: str) -> list[dict[str, str]]:
    """解析 git status --porcelain=v1 的变更行。"""

    files: list[dict[str, str]] = []
    for line in stdout.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        raw_path = line[3:].strip()
        display_path = raw_path.split(" -> ")[-1].strip('"')
        files.append(
            {
                "path": display_path,
                "index_status": xy[0],
                "worktree_status": xy[1],
                "status": _status_label(xy),
            }
        )
    return files


def _status_label(xy: str) -> str:
    """把 porcelain 状态码翻译成简短英文标签，方便前端/模型消费。"""

    if xy == "??":
        return "untracked"
    if "A" in xy:
        return "added"
    if "D" in xy:
        return "deleted"
    if "R" in xy:
        return "renamed"
    if "C" in xy:
        return "copied"
    if "M" in xy:
        return "modified"
    return "changed"


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"\n... truncated {len(value) - max_chars} chars"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

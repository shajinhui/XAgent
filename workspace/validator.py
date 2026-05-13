from __future__ import annotations

from pathlib import Path

from workspace.models import WorkspaceValidationError


SYSTEM_ROOT_PREFIXES = (
    Path("/System"),
    Path("/Library"),
    Path("/Network"),
    Path("/private/etc"),
    Path("/private/var/db"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
)


def validate_workspace_path(raw_path: str | Path) -> Path:
    candidate = Path(raw_path).expanduser().resolve()

    if not candidate.exists():
        raise WorkspaceValidationError(f"工作区不存在: {candidate}")
    if not candidate.is_dir():
        raise WorkspaceValidationError(f"工作区必须是目录: {candidate}")
    if candidate == Path("/"):
        raise WorkspaceValidationError("不能把系统根目录作为工作区")
    if candidate == Path.home().resolve():
        raise WorkspaceValidationError("不能把用户 home 根目录作为工作区")
    if ".git" in candidate.parts:
        raise WorkspaceValidationError("不能把 .git 内部目录作为工作区")

    for prefix in SYSTEM_ROOT_PREFIXES:
        if candidate == prefix or prefix in candidate.parents:
            raise WorkspaceValidationError(f"系统目录不能作为工作区: {candidate}")

    return candidate

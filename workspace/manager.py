"""workspace 打开与运行态绑定。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from session import SessionStore
from workspace.models import (
    AdditionalRoot,
    TrustLevel,
    WorkspaceContext,
    WorkspaceTrust,
    WorkspaceValidationError,
)
from workspace.project_config import load_project_policy
from workspace.trust import WorkspaceTrustStore
from workspace.validator import validate_workspace_path


class WorkspaceManager:
    """负责校验 workspace，并为其创建 SessionStore。"""

    def __init__(self, default_root: Path, trust_store: WorkspaceTrustStore | None = None) -> None:
        self.default_root = validate_workspace_path(default_root)
        self.trust_store = trust_store or WorkspaceTrustStore()

    def open(
        self,
        raw_path: str | Path | None = None,
        *,
        trust_override: WorkspaceTrust | None = None,
    ) -> WorkspaceContext:
        """打开指定 workspace；未指定时打开默认 workspace。"""

        selected_root = validate_workspace_path(raw_path or self.default_root)
        git_root = _find_git_root(selected_root)
        project_root = git_root or selected_root
        trust = trust_override or self.trust_store.trust_for(project_root)
        project_policy = load_project_policy(project_root, trust)
        return WorkspaceContext(
            selected_root=selected_root,
            project_root=project_root,
            current_dir=selected_root,
            display_name=selected_root.name or selected_root.as_posix(),
            git_root=git_root,
            trust=trust,
            additional_roots=[],
            session_store=SessionStore(project_root=project_root),
            project_policy=project_policy,
        )

    def restore_from_snapshot(
        self,
        snapshot: dict[str, Any],
    ) -> WorkspaceContext:
        """从当前 schema 的 workspace 快照恢复运行态，并严格重新验证路径。"""

        selected_root = _required_path(snapshot, "selected_root")
        expected_project_root = _required_path(snapshot, "project_root")
        trust = _trust_for_resume(
            snapshot,
            expected_project_root,
            self.trust_store.trust_for(expected_project_root),
        )
        workspace = self.open(selected_root, trust_override=trust)

        if workspace.project_root != expected_project_root:
            raise WorkspaceValidationError(
                "会话所属 project_root 与当前工作区不一致: "
                f"{expected_project_root.as_posix()} != {workspace.project_root.as_posix()}"
            )

        workspace.additional_roots = _validated_additional_roots(
            snapshot.get("additional_roots"),
            workspace.selected_root,
        )

        workspace.change_current_dir(_required_path(snapshot, "current_dir"))
        return workspace

    def trust_project(self, project_root: Path) -> WorkspaceTrust:
        """验证项目本地配置后，再把当前 project_root 持久标记为 trusted。"""

        validated_project_root = validate_workspace_path(project_root)
        candidate_trust = WorkspaceTrust.trusted(validated_project_root)
        # trust 会启用项目本地配置；必须先验证配置安全，再写入用户侧 trust store。
        load_project_policy(validated_project_root, candidate_trust)
        return self.trust_store.mark_trusted(validated_project_root)

    def untrust_project(self, project_root: Path) -> WorkspaceTrust:
        """把当前 project_root 持久标记为 untrusted，并关闭项目本地配置。"""

        return self.trust_store.mark_untrusted(validate_workspace_path(project_root))


def _find_git_root(path: Path) -> Path | None:
    """向上查找最近的 Git 根目录。"""

    current = path
    while True:
        if (current / ".git").exists() and _is_safe_project_root(current):
            return current
        if current.parent == current:
            return None
        current = current.parent


def _is_safe_project_root(path: Path) -> bool:
    """Git 根也必须满足 workspace 边界，避免把 Home 误当项目。"""

    if path.resolve() == Path.home().resolve():
        return False
    try:
        validate_workspace_path(path)
    except WorkspaceValidationError:
        return False
    return True


def _required_path(snapshot: dict[str, Any], key: str) -> Path:
    value = snapshot.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceValidationError(f"workspace snapshot 缺少字段: {key}")
    return Path(value).expanduser().resolve()


def _trust_for_resume(
    snapshot: dict[str, Any],
    expected_project_root: Path,
    current_trust: WorkspaceTrust,
) -> WorkspaceTrust:
    """恢复会话时按快照 trust 重新校验，避免静默权限升级。"""

    snapshot_trust = WorkspaceTrust.from_dict(snapshot.get("trust"), expected_project_root)
    if snapshot_trust.level == TrustLevel.TRUSTED:
        if current_trust.level != TrustLevel.TRUSTED:
            raise WorkspaceValidationError("会话需要 trusted workspace，但当前项目未被信任")
        return current_trust

    if snapshot_trust.level == TrustLevel.UNTRUSTED:
        return WorkspaceTrust(
            level=TrustLevel.UNTRUSTED,
            trust_key=expected_project_root.resolve().as_posix(),
            source=snapshot_trust.source,
            project_config_enabled=False,
        )

    return WorkspaceTrust.session_only(expected_project_root)


def _validated_additional_roots(
    raw_roots: Any,
    selected_root: Path,
) -> list[AdditionalRoot]:
    """严格恢复 additional roots；任何无效项都让会话恢复失败。"""

    if not isinstance(raw_roots, list):
        raise WorkspaceValidationError("workspace snapshot 字段 additional_roots 必须是列表")

    roots: list[AdditionalRoot] = []
    for raw_root in raw_roots:
        if not isinstance(raw_root, dict):
            raise WorkspaceValidationError("会话 additional root 格式无效")

        raw_path = raw_root.get("path")
        access = raw_root.get("access")
        if not isinstance(raw_path, str) or access not in ("read", "write"):
            raise WorkspaceValidationError("会话 additional root 缺少 path/access")

        path = Path(raw_path).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise WorkspaceValidationError(f"会话 additional root 不可用: {path.as_posix()}")
        if _is_within(path, selected_root):
            raise WorkspaceValidationError(
                f"会话 additional root 已在 selected_root 内: {path.as_posix()}"
            )

        source = raw_root.get("source", "user")
        if source not in ("user", "session", "config"):
            raise WorkspaceValidationError("会话 additional root source 无效")

        root = AdditionalRoot(path, access, source)
        roots = [existing for existing in roots if existing.path != root.path]
        roots.append(root)
    return roots


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents

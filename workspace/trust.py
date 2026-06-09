"""用户侧 workspace 信任存储。

项目本地配置不能自己决定是否可信；信任状态必须来自用户侧存储或当前会话。
第一版只提供文件存储和查询能力，后续再接前端显式 trust/untrust 入口。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from workspace.models import TrustLevel, WorkspaceTrust, WorkspaceValidationError


TRUST_STORE_ENV = "CODEX_MINI_TRUST_STORE"


def default_trust_store_path() -> Path:
    """返回用户侧 trust store 路径；测试可用环境变量覆盖。"""

    configured = os.getenv(TRUST_STORE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex-mini" / "trusted-projects.json"


class WorkspaceTrustStore:
    """读取和维护用户显式信任过的 workspace trust key。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_trust_store_path()).expanduser()

    def trust_for(self, project_root: Path) -> WorkspaceTrust:
        """按 canonical project root 返回当前信任状态。"""

        trust_key = project_root.resolve().as_posix()
        record = self._records().get(trust_key)
        if not isinstance(record, dict):
            return WorkspaceTrust.session_only(project_root)

        level = record.get("level")
        if level == TrustLevel.TRUSTED.value:
            return WorkspaceTrust.trusted(project_root)
        if level == TrustLevel.UNTRUSTED.value:
            return WorkspaceTrust.untrusted(project_root)
        return WorkspaceTrust.session_only(project_root)

    def mark_trusted(self, project_root: Path) -> WorkspaceTrust:
        """持久标记项目可信；当前尚未接 UI，只供后续控制事件和测试使用。"""

        return self._write_level(project_root, TrustLevel.TRUSTED)

    def mark_untrusted(self, project_root: Path) -> WorkspaceTrust:
        """持久标记项目不可信，明确禁止加载 project-local config。"""

        return self._write_level(project_root, TrustLevel.UNTRUSTED)

    def _write_level(self, project_root: Path, level: TrustLevel) -> WorkspaceTrust:
        trust_key = project_root.resolve().as_posix()
        records = self._records()
        records[trust_key] = {
            "level": level.value,
            "updated_at": time.time(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(records, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return self.trust_for(project_root)

    def _records(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WorkspaceValidationError(f"workspace trust store 不是合法 JSON: {self.path}") from exc
        if not isinstance(raw, dict):
            raise WorkspaceValidationError(f"workspace trust store 格式无效: {self.path}")
        return raw

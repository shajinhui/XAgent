"""Pending patch proposal 的本地存储。"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from patch.models import PatchProposal, PatchStatus

_PATCH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class PatchStore:
    """把 patch proposal 以透明 JSON 文件保存到本地 runtime 目录。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def save(self, proposal: PatchProposal) -> Path:
        """原子写入一个 patch proposal。"""

        self.root.mkdir(parents=True, exist_ok=True)
        target = self._proposal_path(proposal.patch_id)
        tmp_path = self.root / f".{proposal.patch_id}.{uuid.uuid4().hex}.tmp"
        # pending patch 需要能跨刷新恢复，因此这里保存完整 diff/source snapshot。
        tmp_path.write_text(
            json.dumps(proposal.as_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(target)
        return target

    def load(self, patch_id: str) -> PatchProposal | None:
        """按 patch_id 读取 proposal；不存在时返回 None。"""

        path = self._proposal_path(patch_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"invalid patch proposal file: {path}")
        return PatchProposal.from_dict(raw)

    def delete(self, patch_id: str) -> bool:
        """删除 proposal 文件，返回是否真的删除。"""

        path = self._proposal_path(patch_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list(
        self,
        *,
        session_id: str | None = None,
        status: PatchStatus | str | None = None,
    ) -> list[PatchProposal]:
        """按创建时间列出 proposal，可选按 session/status 过滤。"""

        if not self.root.exists():
            return []
        expected_status = PatchStatus(status) if status is not None else None
        proposals: list[PatchProposal] = []
        for path in sorted(self.root.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            proposal = PatchProposal.from_dict(raw)
            if session_id is not None and proposal.session_id != session_id:
                continue
            if expected_status is not None and proposal.status != expected_status:
                continue
            proposals.append(proposal)
        proposals.sort(key=lambda item: item.created_at)
        return proposals

    def _proposal_path(self, patch_id: str) -> Path:
        clean_id = patch_id.strip()
        if not _PATCH_ID_RE.fullmatch(clean_id):
            raise ValueError("invalid patch_id")
        return self.root / f"{clean_id}.json"

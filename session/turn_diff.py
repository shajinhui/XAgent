"""Turn 级别的文件变更跟踪与撤销。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict


class TurnDiffTracker:
    """跟踪单个 turn 内的文件变更，支持撤销。"""

    def __init__(self) -> None:
        self.baselines: Dict[str, str] = {}  # {file_path: content_before}
        self.existing_files: set[str] = set()

    def save_baseline(self, file_path: str) -> None:
        """在修改文件前保存 baseline。"""
        file_path = self._normalize_path(file_path)
        if file_path in self.baselines:
            return  # 已保存，不重复

        path = Path(file_path)
        if path.exists():
            self.existing_files.add(file_path)
            try:
                self.baselines[file_path] = path.read_text(encoding="utf-8")
            except Exception:
                self.baselines[file_path] = ""
        else:
            self.existing_files.discard(file_path)
            self.baselines[file_path] = ""  # 新文件

    def undo_file(self, file_path: str) -> bool:
        """撤销对指定文件的修改。"""
        file_path = self._normalize_path(file_path)
        if file_path not in self.baselines:
            return False

        baseline = self.baselines[file_path]
        path = Path(file_path)
        existed_before_turn = file_path in self.existing_files

        try:
            if existed_before_turn:
                # 修改已有文件时，即使原内容为空，也应该恢复为空文件，而不是删除。
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(baseline, encoding="utf-8")
            else:
                # 本轮新建文件撤销时直接删除。
                if path.exists():
                    path.unlink()
            self.baselines.pop(file_path, None)
            self.existing_files.discard(file_path)
            return True
        except Exception:
            return False

    def get_changed_files(self) -> Dict[str, Dict[str, str]]:
        """获取所有变更的文件。"""
        changes = {}
        for file_path, baseline in self.baselines.items():
            path = Path(file_path)
            try:
                current = path.read_text(encoding="utf-8") if path.exists() else ""
            except Exception:
                continue

            if current != baseline:
                changes[file_path] = {
                    "before": baseline,
                    "after": current,
                }

        return changes

    def clear(self) -> None:
        """清空跟踪状态（turn 结束时调用）。"""
        self.baselines.clear()
        self.existing_files.clear()

    def _normalize_path(self, file_path: str) -> str:
        """统一 diff tracker 内部的路径 key，避免相对路径和绝对路径混用。"""

        return Path(file_path).resolve().as_posix()

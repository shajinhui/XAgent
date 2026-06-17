"""Memory 存储层：markdown/jsonl 文件管理。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from memory.models import MemoryEntry, MemoryType

_MAX_MEMORY_ID_LENGTH = 128


class MemoryStore:
    """管理 session/task memory 的文件存储。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.sessions_dir = self.data_dir / "sessions"
        self.tasks_dir = self.data_dir / "tasks"
        self.users_dir = self.data_dir / "users"
        self.projects_dir = self.data_dir / "projects"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def save_session_memory(self, session_id: str, content: str, metadata: Dict[str, Any] | None = None) -> MemoryEntry:
        """保存会话摘要。"""
        return self._save_memory(MemoryType.SESSION, session_id, content, metadata)

    def save_task_memory(self, task_id: str, content: str, metadata: Dict[str, Any] | None = None) -> MemoryEntry:
        """保存任务状态。"""
        return self._save_memory(MemoryType.TASK, task_id, content, metadata)

    def load_session_memory(self, session_id: str) -> MemoryEntry | None:
        """加载会话摘要。"""
        return self._load_memory(MemoryType.SESSION, session_id)

    def load_task_memory(self, task_id: str) -> MemoryEntry | None:
        """加载任务状态。"""
        return self._load_memory(MemoryType.TASK, task_id)

    def save_user_memory(self, content: str, metadata: Dict[str, Any] | None = None) -> MemoryEntry:
        """保存用户偏好（全局）。"""
        return self._save_memory(MemoryType.USER, "preferences", content, metadata)

    def load_user_memory(self) -> MemoryEntry | None:
        """加载用户偏好。"""
        return self._load_memory(MemoryType.USER, "preferences")

    def save_project_memory(self, project_id: str, content: str, metadata: Dict[str, Any] | None = None) -> MemoryEntry:
        """保存项目规则。"""
        return self._save_memory(MemoryType.PROJECT, project_id, content, metadata)

    def load_project_memory(self, project_id: str) -> MemoryEntry | None:
        """加载项目规则。"""
        return self._load_memory(MemoryType.PROJECT, project_id)

    def list_session_memories(self) -> List[MemoryEntry]:
        """列出所有会话摘要。"""
        return self._list_memories(MemoryType.SESSION)

    def list_task_memories(self) -> List[MemoryEntry]:
        """列出所有任务状态。"""
        return self._list_memories(MemoryType.TASK)

    def delete_memory(self, memory_type: MemoryType, memory_id: str) -> bool:
        """删除 memory。"""
        path = self._get_path(memory_type, memory_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def _save_memory(
        self,
        memory_type: MemoryType,
        memory_id: str,
        content: str,
        metadata: Dict[str, Any] | None,
    ) -> MemoryEntry:
        now = time.time()
        existing = self._load_memory(memory_type, memory_id)
        created_at = existing.created_at if existing else now

        entry = MemoryEntry(
            memory_id=memory_id,
            memory_type=memory_type,
            content=content,
            created_at=created_at,
            updated_at=now,
            metadata=metadata or {},
        )

        path = self._get_path(memory_type, memory_id)
        tmp_path = path.with_name(f".{path.name}.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "memory_id": entry.memory_id,
                    "memory_type": entry.memory_type.value,
                    "content": entry.content,
                    "created_at": entry.created_at,
                    "updated_at": entry.updated_at,
                    "metadata": entry.metadata,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
            f.write("\n")
        os.replace(tmp_path, path)
        return entry

    def _load_memory(self, memory_type: MemoryType, memory_id: str) -> MemoryEntry | None:
        path = self._get_path(memory_type, memory_id)
        if not path.exists():
            return None

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return MemoryEntry(
            memory_id=data["memory_id"],
            memory_type=MemoryType(data["memory_type"]),
            content=data["content"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            metadata=data.get("metadata", {}),
        )

    def _list_memories(self, memory_type: MemoryType) -> List[MemoryEntry]:
        target_dir = {
            MemoryType.SESSION: self.sessions_dir,
            MemoryType.TASK: self.tasks_dir,
            MemoryType.USER: self.users_dir,
            MemoryType.PROJECT: self.projects_dir,
        }[memory_type]
        entries = []
        for path in target_dir.glob("*.json"):
            try:
                entry = self._load_memory(memory_type, path.stem)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            if entry:
                entries.append(entry)
        return sorted(entries, key=lambda e: e.updated_at, reverse=True)

    def _get_path(self, memory_type: MemoryType, memory_id: str) -> Path:
        target_dir = {
            MemoryType.SESSION: self.sessions_dir,
            MemoryType.TASK: self.tasks_dir,
            MemoryType.USER: self.users_dir,
            MemoryType.PROJECT: self.projects_dir,
        }[memory_type]
        safe_id = _validate_memory_id(memory_id)
        path = (target_dir / f"{safe_id}.json").resolve()
        try:
            path.relative_to(target_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"invalid memory id: {memory_id}") from exc
        return path


def _validate_memory_id(memory_id: str) -> str:
    """校验 memory id，避免客户端输入逃逸到 memory 目录外。"""

    value = str(memory_id or "").strip()
    if not value:
        raise ValueError("memory id must not be empty")
    if len(value) > _MAX_MEMORY_ID_LENGTH:
        raise ValueError("memory id is too long")
    if value in {".", ".."}:
        raise ValueError(f"invalid memory id: {memory_id}")
    if any(char in value for char in ("/", "\\", "\x00")):
        raise ValueError(f"invalid memory id: {memory_id}")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"invalid memory id: {memory_id}")
    return value

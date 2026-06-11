"""Memory 存储层：markdown/jsonl 文件管理。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List

from memory.models import MemoryEntry, MemoryType


class MemoryStore:
    """管理 session/task memory 的文件存储。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.sessions_dir = self.data_dir / "sessions"
        self.tasks_dir = self.data_dir / "tasks"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def save_session_memory(self, session_id: str, content: str, metadata: Dict | None = None) -> MemoryEntry:
        """保存会话摘要。"""
        return self._save_memory(MemoryType.SESSION, session_id, content, metadata)

    def save_task_memory(self, task_id: str, content: str, metadata: Dict | None = None) -> MemoryEntry:
        """保存任务状态。"""
        return self._save_memory(MemoryType.TASK, task_id, content, metadata)

    def load_session_memory(self, session_id: str) -> MemoryEntry | None:
        """加载会话摘要。"""
        return self._load_memory(MemoryType.SESSION, session_id)

    def load_task_memory(self, task_id: str) -> MemoryEntry | None:
        """加载任务状态。"""
        return self._load_memory(MemoryType.TASK, task_id)

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

    def _save_memory(self, memory_type: MemoryType, memory_id: str, content: str, metadata: Dict | None) -> MemoryEntry:
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
        with path.open("w", encoding="utf-8") as f:
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
        target_dir = self.sessions_dir if memory_type == MemoryType.SESSION else self.tasks_dir
        entries = []
        for path in target_dir.glob("*.json"):
            entry = self._load_memory(memory_type, path.stem)
            if entry:
                entries.append(entry)
        return sorted(entries, key=lambda e: e.updated_at, reverse=True)

    def _get_path(self, memory_type: MemoryType, memory_id: str) -> Path:
        target_dir = self.sessions_dir if memory_type == MemoryType.SESSION else self.tasks_dir
        return target_dir / f"{memory_id}.json"

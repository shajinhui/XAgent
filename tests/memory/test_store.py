"""memory 层单元测试。"""

import tempfile
from pathlib import Path

from memory.models import MemoryType
from memory.store import MemoryStore


def test_save_and_load_session_memory():
    """测试保存和加载会话摘要。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(Path(tmpdir))

        entry = store.save_session_memory(
            "session-123",
            "用户询问如何优化性能",
            {"turn_count": 5}
        )

        assert entry.memory_id == "session-123"
        assert entry.memory_type == MemoryType.SESSION
        assert "优化性能" in entry.content
        assert entry.metadata["turn_count"] == 5

        loaded = store.load_session_memory("session-123")
        assert loaded is not None
        assert loaded.content == entry.content


def test_save_and_load_task_memory():
    """测试保存和加载任务状态。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(Path(tmpdir))

        entry = store.save_task_memory(
            "task-456",
            "目标：优化 CPU 占用\n已尝试：批量写入",
            {"status": "in_progress"}
        )

        assert entry.memory_id == "task-456"
        assert entry.memory_type == MemoryType.TASK

        loaded = store.load_task_memory("task-456")
        assert loaded is not None
        assert "批量写入" in loaded.content


def test_list_memories():
    """测试列出 memory。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(Path(tmpdir))

        store.save_session_memory("session-1", "会话1")
        store.save_session_memory("session-2", "会话2")
        store.save_task_memory("task-1", "任务1")

        sessions = store.list_session_memories()
        assert len(sessions) == 2

        tasks = store.list_task_memories()
        assert len(tasks) == 1


def test_delete_memory():
    """测试删除 memory。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(Path(tmpdir))

        store.save_session_memory("session-999", "测试")
        assert store.load_session_memory("session-999") is not None

        deleted = store.delete_memory(MemoryType.SESSION, "session-999")
        assert deleted is True
        assert store.load_session_memory("session-999") is None


def test_update_memory():
    """测试更新 memory 保留创建时间。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(Path(tmpdir))

        first = store.save_session_memory("session-upd", "初始内容")
        second = store.save_session_memory("session-upd", "更新内容")

        assert first.created_at == second.created_at
        assert second.updated_at > first.updated_at
        assert second.content == "更新内容"

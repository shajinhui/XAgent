"""memory 存储层单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memory.models import MemoryType
from memory.store import MemoryStore


class MemoryStoreTest(unittest.TestCase):
    def test_save_and_load_session_memory(self) -> None:
        """保存和加载会话摘要。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))

            entry = store.save_session_memory(
                "session-123",
                "用户询问如何优化性能",
                {"turn_count": 5},
            )

            self.assertEqual(entry.memory_id, "session-123")
            self.assertEqual(entry.memory_type, MemoryType.SESSION)
            self.assertIn("优化性能", entry.content)
            self.assertEqual(entry.metadata["turn_count"], 5)

            loaded = store.load_session_memory("session-123")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.content, entry.content)

    def test_save_and_load_task_memory(self) -> None:
        """保存和加载任务状态。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))

            entry = store.save_task_memory(
                "task-456",
                "目标：优化 CPU 占用\n已尝试：批量写入",
                {"status": "in_progress"},
            )

            self.assertEqual(entry.memory_id, "task-456")
            self.assertEqual(entry.memory_type, MemoryType.TASK)

            loaded = store.load_task_memory("task-456")
            self.assertIsNotNone(loaded)
            self.assertIn("批量写入", loaded.content)

    def test_list_memories(self) -> None:
        """按类型列出 memory。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))

            store.save_session_memory("session-1", "会话1")
            store.save_session_memory("session-2", "会话2")
            store.save_task_memory("task-1", "任务1")

            self.assertEqual(len(store.list_session_memories()), 2)
            self.assertEqual(len(store.list_task_memories()), 1)

    def test_delete_memory(self) -> None:
        """删除指定 memory。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))

            store.save_session_memory("session-999", "测试")
            self.assertIsNotNone(store.load_session_memory("session-999"))

            self.assertTrue(store.delete_memory(MemoryType.SESSION, "session-999"))
            self.assertIsNone(store.load_session_memory("session-999"))

    def test_update_memory_keeps_created_at(self) -> None:
        """更新 memory 时保留创建时间。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))

            first = store.save_session_memory("session-upd", "初始内容")
            second = store.save_session_memory("session-upd", "更新内容")

            self.assertEqual(first.created_at, second.created_at)
            self.assertGreater(second.updated_at, first.updated_at)
            self.assertEqual(second.content, "更新内容")

    def test_rejects_path_traversal_memory_id(self) -> None:
        """拒绝会逃逸 memory 目录的 id。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))

            with self.assertRaises(ValueError):
                store.save_session_memory("../outside", "bad")

            self.assertFalse((Path(tmpdir).parent / "outside.json").exists())


if __name__ == "__main__":
    unittest.main()

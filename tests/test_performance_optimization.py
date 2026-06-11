"""会话写入性能优化的回归测试。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from session.store import SessionStore
from session.transcript import TranscriptWriter


class PerformanceOptimizationTests(unittest.TestCase):
    def test_transcript_batch_writing_keeps_events_complete(self) -> None:
        """批量写入后必须能完整读回所有 transcript event。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = TranscriptWriter(Path(tmpdir) / "test.jsonl")

            start = time.time()
            for index in range(100):
                writer.append("session-1", "test_event", {"index": index})
            writer.flush()
            elapsed = time.time() - start

            events = writer.load()
            self.assertEqual(len(events), 100)
            self.assertLess(elapsed, 0.1)

    def test_session_store_batch_cache_keeps_transcript_complete(self) -> None:
        """SessionStore 延迟索引更新不能丢 transcript 事件。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir))
            record = store.create_session(title="测试会话")

            start = time.time()
            for index in range(50):
                store.append_event(record.session_id, "assistant_token", {"token": f"word_{index}"})
            elapsed = time.time() - start

            store._flush_index_update(record.session_id)
            events = store.load_events(record.session_id)

            self.assertEqual(len(events), 51)
            self.assertLess(elapsed, 0.2)


if __name__ == "__main__":
    unittest.main()

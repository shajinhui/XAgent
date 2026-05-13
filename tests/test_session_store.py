from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from session import SessionStore, TranscriptWriter


class SessionStoreTests(unittest.TestCase):
    def test_create_session_indexes_record_and_writes_start_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(root)

            record = store.create_session(title="First task", metadata={"model": "test"})

            self.assertTrue(record.session_id)
            self.assertEqual(record.title, "First task")
            self.assertTrue(record.transcript_path.exists())

            loaded = store.get_session(record.session_id)
            self.assertEqual(loaded.session_id, record.session_id)
            self.assertEqual(loaded.metadata, {"model": "test"})

            events = store.load_events(record.session_id)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].type, "session_started")
            self.assertEqual(events[0].payload["title"], "First task")

    def test_append_event_updates_index_and_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            record = store.create_session()

            event = store.append_event(
                record.session_id,
                "user_message",
                {"turn_id": "turn-1", "content": "hello"},
            )

            self.assertEqual(event.type, "user_message")
            updated = store.get_session(record.session_id)
            self.assertEqual(updated.last_turn_id, "turn-1")
            self.assertGreaterEqual(updated.updated_at, record.updated_at)

            events = store.load_events(record.session_id)
            self.assertEqual([item.type for item in events], ["session_started", "user_message"])
            self.assertEqual(events[1].payload["content"], "hello")

    def test_list_sessions_orders_by_recent_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            older = store.create_session(session_id="older")
            newer = store.create_session(session_id="newer")

            store.append_event(older.session_id, "user_message", {"turn_id": "turn-old"})
            store.append_event(newer.session_id, "user_message", {"turn_id": "turn-new"})

            sessions = store.list_sessions()

            self.assertEqual([session.session_id for session in sessions], ["newer", "older"])

    def test_load_events_rejects_unknown_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))

            with self.assertRaises(KeyError):
                store.load_events("missing")

    def test_delete_session_removes_index_and_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            record = store.create_session(session_id="delete-me")
            store.append_event(record.session_id, "user_message", {"content": "remove"})

            deleted = store.delete_session(record.session_id)

            self.assertEqual(deleted.session_id, record.session_id)
            self.assertFalse(record.transcript_path.exists())
            self.assertEqual(store.list_sessions(), [])
            with self.assertRaises(KeyError):
                store.get_session(record.session_id)


class TranscriptWriterTests(unittest.TestCase):
    def test_transcript_writer_round_trips_jsonl_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            writer = TranscriptWriter(path)

            writer.append(
                "session-1",
                "assistant_message",
                {"content": "你好", "turn_id": "turn-1"},
                timestamp=123.0,
                event_id="event-1",
            )

            raw = path.read_text(encoding="utf-8").strip()
            self.assertEqual(json.loads(raw)["payload"]["content"], "你好")

            events = writer.load()
            self.assertEqual(events[0].event_id, "event-1")
            self.assertEqual(events[0].timestamp, 123.0)
            self.assertEqual(events[0].payload["turn_id"], "turn-1")

    def test_transcript_writer_reports_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            path.write_text('{"type": "missing-fields"}\n', encoding="utf-8")

            with self.assertRaises(ValueError):
                TranscriptWriter(path).load()

    def test_transcript_writer_preserves_zero_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            writer = TranscriptWriter(path)

            writer.append("session-1", "session_started", timestamp=0.0)

            self.assertEqual(writer.load()[0].timestamp, 0.0)


if __name__ == "__main__":
    unittest.main()

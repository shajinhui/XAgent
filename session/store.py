from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List

from session.models import SessionRecord, TranscriptEvent
from session.transcript import TranscriptWriter


class SessionStore:
    """SQLite index plus append-only transcript files for Codex-mini sessions."""

    def __init__(self, project_root: Path, data_dir: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self.data_dir = (data_dir or self.project_root / ".codex-mini" / "sessions").resolve()
        self.transcript_dir = self.data_dir / "transcripts"
        self.db_path = self.data_dir / "index.sqlite"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def create_session(
        self,
        *,
        session_id: str | None = None,
        title: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> SessionRecord:
        created_at = time.time()
        resolved_session_id = session_id or str(uuid.uuid4())
        transcript_path = self._transcript_path(resolved_session_id)
        record = SessionRecord(
            session_id=resolved_session_id,
            title=title,
            created_at=created_at,
            updated_at=created_at,
            project_root=self.project_root,
            transcript_path=transcript_path,
            metadata=metadata or {},
        )

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    title,
                    created_at,
                    updated_at,
                    project_root,
                    transcript_path,
                    last_turn_id,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.session_id,
                    record.title,
                    record.created_at,
                    record.updated_at,
                    record.project_root.as_posix(),
                    record.transcript_path.as_posix(),
                    record.last_turn_id,
                    json.dumps(record.metadata or {}, ensure_ascii=False, sort_keys=True),
                ),
            )

        self.writer(record.session_id).append(
            record.session_id,
            "session_started",
            {
                "project_root": record.project_root.as_posix(),
                "title": record.title,
                "metadata": record.metadata or {},
            },
            timestamp=created_at,
        )
        return record

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: Dict[str, Any] | None = None,
    ) -> TranscriptEvent:
        record = self.get_session(session_id)
        event = self.writer(session_id).append(session_id, event_type, payload or {})
        last_turn_id = _extract_turn_id(event.payload)

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET updated_at = ?,
                    last_turn_id = COALESCE(?, last_turn_id)
                WHERE session_id = ?
                """,
                (event.timestamp, last_turn_id, record.session_id),
            )
        return event

    def get_session(self, session_id: str) -> SessionRecord:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    session_id,
                    title,
                    created_at,
                    updated_at,
                    project_root,
                    transcript_path,
                    last_turn_id,
                    metadata_json
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()

        if row is None:
            raise KeyError(f"unknown session: {session_id}")
        return _row_to_record(row)

    def list_sessions(self, limit: int | None = None) -> List[SessionRecord]:
        query = """
            SELECT
                session_id,
                title,
                created_at,
                updated_at,
                project_root,
                transcript_path,
                last_turn_id,
                metadata_json
            FROM sessions
            ORDER BY updated_at DESC, created_at DESC, session_id DESC
        """
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def delete_session(self, session_id: str) -> SessionRecord:
        record = self.get_session(session_id)

        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM sessions
                WHERE session_id = ?
                """,
                (record.session_id,),
            )

        transcript_path = record.transcript_path
        try:
            transcript_path.relative_to(self.transcript_dir)
        except ValueError:
            transcript_path = self._transcript_path(record.session_id)
        transcript_path.unlink(missing_ok=True)
        return record

    def load_events(self, session_id: str) -> List[TranscriptEvent]:
        self.get_session(session_id)
        return self.writer(session_id).load()

    def writer(self, session_id: str) -> TranscriptWriter:
        return TranscriptWriter(self._transcript_path(session_id))

    def _transcript_path(self, session_id: str) -> Path:
        safe_session_id = session_id.replace("/", "_").replace("\\", "_")
        return self.transcript_dir / f"{safe_session_id}.jsonl"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    project_root TEXT NOT NULL,
                    transcript_path TEXT NOT NULL,
                    last_turn_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
                ON sessions(updated_at DESC)
                """
            )


def _extract_turn_id(payload: Dict[str, Any]) -> str | None:
    turn_id = payload.get("turn_id")
    if turn_id is None:
        return None
    return str(turn_id)


def _row_to_record(row: sqlite3.Row) -> SessionRecord:
    metadata = json.loads(row["metadata_json"] or "{}")
    return SessionRecord(
        session_id=row["session_id"],
        title=row["title"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        project_root=Path(row["project_root"]),
        transcript_path=Path(row["transcript_path"]),
        last_turn_id=row["last_turn_id"],
        metadata=metadata,
    )

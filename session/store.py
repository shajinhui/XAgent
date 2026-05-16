"""SQLite 会话索引与 append-only transcript 文件的统一访问层。"""

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
    """管理单个 workspace 下的 session 索引和 transcript 文件。"""

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
        """创建会话记录，并写入第一条 session_started transcript event。"""

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
        """追加 transcript event，并同步更新索引中的更新时间和最近 turn。"""

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
        """读取单个 session record；不存在时抛出 KeyError。"""

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

    def list_sessions(
        self,
        limit: int | None = None,
        *,
        with_turns: bool = False,
    ) -> List[SessionRecord]:
        """按更新时间列出 session。

        `with_turns=True` 时只在 SQLite 索引层筛选有真实 turn 的会话，
        避免启动历史列表时扫描大量空 transcript。
        """

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
        """
        if with_turns:
            query += " WHERE last_turn_id IS NOT NULL AND last_turn_id NOT IN ('', 'system', 'title')"
        query += " ORDER BY updated_at DESC, created_at DESC, session_id DESC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def delete_session(self, session_id: str) -> SessionRecord:
        """删除 session 索引记录和对应 transcript 文件。"""

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
        """加载指定 session 的完整 transcript events。"""

        self.get_session(session_id)
        return self.writer(session_id).load()

    def writer(self, session_id: str) -> TranscriptWriter:
        """返回指定 session 对应的 transcript writer。"""

        return TranscriptWriter(self._transcript_path(session_id))

    def _transcript_path(self, session_id: str) -> Path:
        """把 session_id 映射到安全的 transcript 文件路径。"""

        safe_session_id = session_id.replace("/", "_").replace("\\", "_")
        return self.transcript_dir / f"{safe_session_id}.jsonl"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """提供带 commit/rollback/close 的 SQLite 连接上下文。"""

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
        """初始化 SQLite schema 和查询索引。"""

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
    """从事件 payload 中提取可用于索引的 turn_id。"""

    turn_id = payload.get("turn_id")
    if turn_id is None:
        return None
    normalized_turn_id = str(turn_id)
    if normalized_turn_id in {"", "system", "title"}:
        return None
    return normalized_turn_id


def _row_to_record(row: sqlite3.Row) -> SessionRecord:
    """把 SQLite row 转换成 SessionRecord。"""

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

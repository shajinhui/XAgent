"""会话持久化使用的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class SessionRecord:
    """SQLite 索引中的一条会话记录。"""

    session_id: str
    created_at: float
    updated_at: float
    project_root: Path
    transcript_path: Path
    title: str | None = None
    last_turn_id: str | None = None
    metadata: Dict[str, Any] | None = None


@dataclass(frozen=True)
class TranscriptEvent:
    """append-only JSONL transcript 中的一条事件。"""

    event_id: str
    session_id: str
    type: str
    timestamp: float
    payload: Dict[str, Any]

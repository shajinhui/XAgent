"""append-only JSONL transcript 读写器。"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

from session.models import TranscriptEvent


class TranscriptWriter:
    """为单个 session 维护可重放的 JSONL 事件流。"""

    def __init__(self, transcript_path: Path) -> None:
        self.transcript_path = transcript_path
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        session_id: str,
        event_type: str,
        payload: Dict[str, Any] | None = None,
        *,
        timestamp: float | None = None,
        event_id: str | None = None,
    ) -> TranscriptEvent:
        """追加一条 transcript event，并返回结构化事件对象。"""

        event = TranscriptEvent(
            event_id=event_id or str(uuid.uuid4()),
            session_id=session_id,
            type=event_type,
            timestamp=timestamp if timestamp is not None else time.time(),
            payload=payload or {},
        )
        with self.transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_event_to_json(event), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return event

    def load(self) -> List[TranscriptEvent]:
        """读取并校验 transcript 文件中的全部事件。"""

        if not self.transcript_path.exists():
            return []

        events: List[TranscriptEvent] = []
        with self.transcript_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                events.append(_event_from_json(json.loads(stripped), line_number))
        return events

    def extend(self, events: Iterable[TranscriptEvent]) -> None:
        """批量追加事件，主要用于测试或未来迁移。"""

        with self.transcript_path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(_event_to_json(event), ensure_ascii=False, sort_keys=True))
                handle.write("\n")


def _event_to_json(event: TranscriptEvent) -> Dict[str, Any]:
    """把 TranscriptEvent 转为可 JSON 序列化的 dict。"""

    return {
        "event_id": event.event_id,
        "session_id": event.session_id,
        "type": event.type,
        "timestamp": event.timestamp,
        "payload": event.payload,
    }


def _event_from_json(raw: Dict[str, Any], line_number: int) -> TranscriptEvent:
    """从 JSON 行恢复 TranscriptEvent，并在坏行时报出行号。"""

    required = {"event_id", "session_id", "type", "timestamp", "payload"}
    missing = required.difference(raw)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"Transcript line {line_number} missing fields: {missing_text}")

    payload = raw["payload"]
    if not isinstance(payload, dict):
        raise ValueError(f"Transcript line {line_number} payload must be an object")

    return TranscriptEvent(
        event_id=str(raw["event_id"]),
        session_id=str(raw["session_id"]),
        type=str(raw["type"]),
        timestamp=float(raw["timestamp"]),
        payload=payload,
    )

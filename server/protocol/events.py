"""WebSocket 事件协议封包与客户端 packet 解析。"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict


EVENT_SCHEMA_VERSION = "2026-05-05"


def build_event(
    event_type: str,
    session_id: str,
    turn_id: str,
    **payload: Any,
) -> Dict[str, Any]:
    """生成统一事件信封，确保所有事件都带 schema/request/timestamp。"""

    return {
        "type": event_type,
        "session_id": session_id,
        "turn_id": turn_id,
        "request_id": payload.pop("request_id", str(uuid.uuid4())),
        "schema_version": EVENT_SCHEMA_VERSION,
        "timestamp": time.time(),
        **payload,
    }


def parse_client_packet(raw_packet: str) -> Dict[str, Any] | None:
    """解析客户端文本 packet；非法 JSON 或非 object 输入返回 None。"""

    try:
        packet = json.loads(raw_packet)
    except json.JSONDecodeError:
        return None
    if not isinstance(packet, dict):
        return None
    return packet

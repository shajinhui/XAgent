"""Memory 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class MemoryType(str, Enum):
    """Memory 类型。"""

    SESSION = "session"  # 会话摘要
    TASK = "task"  # 任务状态
    USER = "user"  # 用户偏好
    PROJECT = "project"  # 项目规则


@dataclass(frozen=True)
class MemoryEntry:
    """单条 memory 记录。"""

    memory_id: str
    memory_type: MemoryType
    content: str
    created_at: float
    updated_at: float
    metadata: Dict[str, Any]

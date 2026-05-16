"""
单次 turn 的模型请求上下文（中文注释）。

`ModelContext` 是对当前 turn 使用的模型配置（模型名、推理强度及原始
请求配置）的不可变快照，便于记录与在转录或事件中序列化该信息。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ModelContext:
    """本次 turn 使用的模型设置快照（不可变）。"""

    model: str
    reasoning_effort: str
    request_config: Any

    @classmethod
    def from_request_config(cls, request_config: Any) -> "ModelContext":
        return cls(
            model=str(getattr(request_config, "model")),
            reasoning_effort=str(getattr(request_config, "reasoning_effort", "off")),
            request_config=request_config,
        )

    def as_dict(self) -> Dict[str, Any]:
        """将 ModelContext 序列化为字典（优先使用 request_config 提供的序列化）。"""
        if hasattr(self.request_config, "as_dict"):
            return self.request_config.as_dict()
        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
        }

    def render_fragment(self) -> str:
        """渲染为模型可见的简短片段，通常用于构建 system/user 消息的一部分。"""
        return f"Model: {self.model}\nReasoning effort: {self.reasoning_effort}"

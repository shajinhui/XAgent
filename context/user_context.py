"""
用户在一次 turn 中提供的上下文（中文注释）。

包含系统提示与用户输入的不可变数据类，方便在生成模型提示或记录转录时
统一序列化与渲染。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class UserContext:
    """表示一次 turn 中的系统提示与用户输入（不可变）。"""

    system_prompt: str
    user_input: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "system_prompt": self.system_prompt,
            "user_input": self.user_input,
        }

    def render_fragment(self) -> str:
        """以简短文本形式渲染用户输入，供模型提示片段使用。"""
        return self.user_input

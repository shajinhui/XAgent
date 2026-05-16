"""ask_user 的模型可见 schema 和入参定义。"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from tools.core.types import ToolMeta


META = ToolMeta(
    name="ask_user",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=False,
)


class AskUserOption(BaseModel):
    id: str | None = Field(
        None,
        description="稳定选项 id；省略时运行时会按 option_1 生成",
    )
    label: str = Field(..., min_length=1, max_length=120, description="展示给用户的选项")
    description: str | None = Field(
        None,
        max_length=500,
        description="选项说明，帮助用户理解取舍",
    )
    recommended: bool = Field(False, description="是否是推荐选项")


class AskUserArgs(BaseModel):
    question: str = Field(..., min_length=1, max_length=600, description="要询问用户的问题")
    options: List[AskUserOption] = Field(
        default_factory=list,
        max_length=6,
        description="可选答案列表；适合范围、策略、偏好等多选一场景",
    )
    allow_freeform: bool = Field(True, description="是否允许用户输入自定义补充")


def schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "当用户意图、范围或偏好不清楚，并且直接假设会明显影响结果时，"
                "向用户发起一个简短澄清问题。优先提供 2-4 个可选择方案，"
                "并把最保守、最推荐的方案标为 recommended。"
            ),
            "parameters": AskUserArgs.model_json_schema(),
        },
    }

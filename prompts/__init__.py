"""系统提示词模块。

提供模板加载、提示词组装功能，用于构建 AI 代理的系统提示词。
"""
from prompts.base import (
    CORE_PROMPT,
    PERSONALITY_DEFAULT,
    PERSONALITY_FRIENDLY,
    PERSONALITY_PRAGMATIC,
    TOOLS_FILE,
    TOOLS_GIT,
    TOOLS_INTERACTION,
    TOOLS_SHELL,
)
from prompts.builder import PromptBuilder, SystemPromptConfig

__all__ = [
    "CORE_PROMPT",
    "PERSONALITY_DEFAULT",
    "PERSONALITY_FRIENDLY",
    "PERSONALITY_PRAGMATIC",
    "TOOLS_SHELL",
    "TOOLS_FILE",
    "TOOLS_GIT",
    "TOOLS_INTERACTION",
    "PromptBuilder",
    "SystemPromptConfig",
]

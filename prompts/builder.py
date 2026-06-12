"""提示词组装器。

根据配置将多个模板片段组装成完整的系统提示词。
"""
from __future__ import annotations

from dataclasses import dataclass

from prompts import base


@dataclass
class SystemPromptConfig:
    """系统提示词配置。

    Attributes:
        personality: 个性化风格，可选 "default"/"friendly"/"pragmatic"
        include_tools: 是否包含工具使用指引
    """

    personality: str = "default"
    include_tools: bool = True


class PromptBuilder:
    """提示词构建器，负责将核心提示词、个性化模板、工具指引组装为完整提示词。"""

    def build(
        self,
        config: SystemPromptConfig,
        workspace_context: str = "",
        project_instructions: str = "",
    ) -> str:
        """组装完整的系统提示词。

        组装顺序：
        1. 核心提示词（插入个性化模板）
        2. 工具使用指引（可选）
        3. 工作空间上下文
        4. 项目说明（AGENTS.md）

        Args:
            config: 提示词配置
            workspace_context: 工作空间上下文信息
            project_instructions: 项目说明（来自 AGENTS.md）

        Returns:
            完整的系统提示词文本
        """
        # 选择个性化模板
        personality_map = {
            "default": base.PERSONALITY_DEFAULT,
            "friendly": base.PERSONALITY_FRIENDLY,
            "pragmatic": base.PERSONALITY_PRAGMATIC,
        }
        personality_text = personality_map.get(config.personality, base.PERSONALITY_DEFAULT)

        # 将个性化文本插入核心提示词
        core_with_personality = base.CORE_PROMPT.replace("{{ personality }}", personality_text)

        # 组装工具指引（每个工具模板之间用双换行分隔）
        tools_section = ""
        if config.include_tools:
            tools_parts = [
                base.TOOLS_SHELL,
                base.TOOLS_FILE,
                base.TOOLS_GIT,
                base.TOOLS_INTERACTION,
            ]
            tools_section = "\n\n".join(tools_parts)

        # 按顺序组装所有片段
        sections = [
            core_with_personality,
            tools_section,
            workspace_context,
            project_instructions,
        ]

        return "\n\n".join(s for s in sections if s).strip()

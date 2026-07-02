"""测试提示词模块。"""
from __future__ import annotations

import unittest

from prompts import PromptBuilder, SystemPromptConfig
from prompts.base import (
    CORE_PROMPT,
    PERSONALITY_DEFAULT,
    PERSONALITY_FRIENDLY,
    PERSONALITY_PRAGMATIC,
    TOOLS_FILE,
    TOOLS_GIT,
    TOOLS_SHELL,
)


class TestPromptTemplates(unittest.TestCase):
    """测试模板加载。"""

    def test_core_prompt_loaded(self):
        """核心提示词应该成功加载。"""
        self.assertIsInstance(CORE_PROMPT, str)
        self.assertGreater(len(CORE_PROMPT), 100)
        self.assertIn("XCode", CORE_PROMPT)
        self.assertIn("{{ personality }}", CORE_PROMPT)

    def test_personality_templates_loaded(self):
        """所有个性化模板应该成功加载。"""
        self.assertIsInstance(PERSONALITY_DEFAULT, str)
        self.assertIsInstance(PERSONALITY_FRIENDLY, str)
        self.assertIsInstance(PERSONALITY_PRAGMATIC, str)
        self.assertGreater(len(PERSONALITY_DEFAULT), 10)
        self.assertGreater(len(PERSONALITY_FRIENDLY), 10)
        self.assertGreater(len(PERSONALITY_PRAGMATIC), 10)

    def test_tools_templates_loaded(self):
        """所有工具指引模板应该成功加载。"""
        self.assertIsInstance(TOOLS_SHELL, str)
        self.assertIsInstance(TOOLS_FILE, str)
        self.assertIsInstance(TOOLS_GIT, str)
        self.assertIn("rg", TOOLS_SHELL)
        self.assertIn("edit_file", TOOLS_FILE)
        self.assertIn("git", TOOLS_GIT)


class TestPromptBuilder(unittest.TestCase):
    """测试提示词构建器。"""

    def setUp(self):
        """设置测试环境。"""
        self.builder = PromptBuilder()

    def test_build_with_default_personality(self):
        """使用默认个性构建提示词。"""
        config = SystemPromptConfig(personality="default")
        result = self.builder.build(config)

        self.assertIsInstance(result, str)
        self.assertIn("XCode", result)
        self.assertNotIn("{{ personality }}", result)
        self.assertIn(PERSONALITY_DEFAULT, result)

    def test_build_with_friendly_personality(self):
        """使用友好个性构建提示词。"""
        config = SystemPromptConfig(personality="friendly")
        result = self.builder.build(config)

        self.assertIn(PERSONALITY_FRIENDLY, result)
        self.assertNotIn(PERSONALITY_DEFAULT, result)

    def test_build_with_pragmatic_personality(self):
        """使用务实个性构建提示词。"""
        config = SystemPromptConfig(personality="pragmatic")
        result = self.builder.build(config)

        self.assertIn(PERSONALITY_PRAGMATIC, result)
        self.assertNotIn(PERSONALITY_DEFAULT, result)

    def test_build_with_tools(self):
        """包含工具指引时应该出现在提示词中。"""
        config = SystemPromptConfig(include_tools=True)
        result = self.builder.build(config)

        # 检查每个工具指引的关键标记是否存在
        self.assertIn("# Shell 命令工具", result)
        self.assertIn("# 文件操作工具", result)
        self.assertIn("# Git 工具", result)
        self.assertIn("rg", result)
        self.assertIn("edit_file", result)
        self.assertIn("git reset --hard", result)

    def test_build_without_tools(self):
        """不包含工具指引时不应出现在提示词中。"""
        config = SystemPromptConfig(include_tools=False)
        result = self.builder.build(config)

        # 检查工具指引的标题不应出现
        self.assertNotIn("# Shell 命令工具", result)
        self.assertNotIn("# 文件操作工具", result)
        self.assertNotIn("# Git 工具", result)

    def test_build_with_workspace_context(self):
        """应该能够追加工作空间上下文。"""
        config = SystemPromptConfig()
        workspace_context = "工作空间：/path/to/project"
        result = self.builder.build(config, workspace_context=workspace_context)

        self.assertIn(workspace_context, result)

    def test_build_with_project_instructions(self):
        """应该能够追加项目说明。"""
        config = SystemPromptConfig()
        project_instructions = "项目说明：请使用 TypeScript"
        result = self.builder.build(config, project_instructions=project_instructions)

        self.assertIn(project_instructions, result)

    def test_build_order(self):
        """组装顺序应该正确：核心 -> 工具 -> 工作空间 -> 项目说明。"""
        config = SystemPromptConfig(include_tools=True)
        workspace_context = "WORKSPACE_MARKER"
        project_instructions = "PROJECT_MARKER"

        result = self.builder.build(
            config,
            workspace_context=workspace_context,
            project_instructions=project_instructions,
        )

        # 查找各部分的位置
        core_pos = result.find("XCode")
        tools_pos = result.find("rg")  # TOOLS_SHELL 的标志
        workspace_pos = result.find("WORKSPACE_MARKER")
        project_pos = result.find("PROJECT_MARKER")

        # 验证顺序
        self.assertLess(core_pos, tools_pos)
        self.assertLess(tools_pos, workspace_pos)
        self.assertLess(workspace_pos, project_pos)

    def test_unknown_personality_fallback(self):
        """未知个性应该回退到默认个性。"""
        config = SystemPromptConfig(personality="unknown")
        result = self.builder.build(config)

        self.assertIn(PERSONALITY_DEFAULT, result)


if __name__ == "__main__":
    unittest.main()

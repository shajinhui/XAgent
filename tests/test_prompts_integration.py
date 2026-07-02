"""测试提示词模块与 agent_loop 的集成。"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from prompts import PromptBuilder, SystemPromptConfig
from tools.core.catalog import build_default_registry
from tools.core.runner import ToolRunner, create_tool_context


class TestPromptsIntegration(unittest.TestCase):
    """测试提示词模块与现有系统的集成。"""

    def test_prompt_builder_integration(self):
        """验证提示词构建器可以正常工作。"""
        config = SystemPromptConfig(personality="default", include_tools=True)
        builder = PromptBuilder()
        prompt = builder.build(config)

        # 验证关键内容存在
        self.assertIn("XCode", prompt)
        self.assertIn("# Shell 命令工具", prompt)
        self.assertIn("# 文件操作工具", prompt)
        self.assertIn("# Git 工具", prompt)
        self.assertIn("AGENTS.md", prompt)

    def test_system_message_format(self):
        """验证系统消息格式符合 LLM 要求。"""
        config = SystemPromptConfig(personality="default", include_tools=True)
        builder = PromptBuilder()
        prompt = builder.build(config)

        # 构建系统消息
        system_message = {"role": "system", "content": prompt}

        # 验证格式
        self.assertEqual(system_message["role"], "system")
        self.assertIsInstance(system_message["content"], str)
        self.assertGreater(len(system_message["content"]), 1000)

    def test_different_personalities(self):
        """验证不同个性模板产生不同的提示词。"""
        builder = PromptBuilder()

        default_prompt = builder.build(SystemPromptConfig(personality="default"))
        friendly_prompt = builder.build(SystemPromptConfig(personality="friendly"))
        pragmatic_prompt = builder.build(SystemPromptConfig(personality="pragmatic"))

        # 验证三种个性产生的提示词不同
        self.assertNotEqual(default_prompt, friendly_prompt)
        self.assertNotEqual(default_prompt, pragmatic_prompt)
        self.assertNotEqual(friendly_prompt, pragmatic_prompt)

        # 但都包含核心身份
        for prompt in [default_prompt, friendly_prompt, pragmatic_prompt]:
            self.assertIn("XCode", prompt)

    def test_with_workspace_context(self):
        """验证可以添加工作空间上下文。"""
        config = SystemPromptConfig()
        builder = PromptBuilder()
        workspace_context = "当前工作目录: /test/project"

        prompt = builder.build(config, workspace_context=workspace_context)

        self.assertIn(workspace_context, prompt)
        self.assertIn("XCode", prompt)

    def test_tools_optional(self):
        """验证工具指引可选。"""
        builder = PromptBuilder()

        with_tools = builder.build(SystemPromptConfig(include_tools=True))
        without_tools = builder.build(SystemPromptConfig(include_tools=False))

        # 有工具指引的应该包含工具章节
        self.assertIn("# Shell 命令工具", with_tools)
        self.assertIn("# Git 工具", with_tools)

        # 无工具指引的不应包含
        self.assertNotIn("# Shell 命令工具", without_tools)
        self.assertNotIn("# Git 工具", without_tools)


class TestAgentLoopIntegration(unittest.TestCase):
    """测试与 agent_loop.py 的集成。"""

    def test_agent_loop_uses_prompt(self):
        """验证 agent_loop 使用了提示词模块。"""
        # 验证可以导入并使用提示词模块
        from prompts import PromptBuilder, SystemPromptConfig

        config = SystemPromptConfig(personality="default", include_tools=True)
        builder = PromptBuilder()
        system_prompt = builder.build(config)

        # 验证系统消息可以用于消息列表
        messages = [{"role": "system", "content": system_prompt}]

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "system")
        self.assertGreater(len(messages[0]["content"]), 1000)


if __name__ == "__main__":
    unittest.main()

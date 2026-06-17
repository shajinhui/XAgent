"""测试 list_files 工具。"""
import unittest
from pathlib import Path
from tools.filesystem.list_files import run
from tools.core.runner import create_tool_context


class TestListFiles(unittest.TestCase):
    def _run_tool(self, **payload):
        ctx = create_tool_context(Path.cwd())
        return run(ctx, payload)

    def test_list_python_files(self):
        """测试列出 Python 文件"""
        result = self._run_tool(directory=".", pattern="*.py")
        self.assertIn("agent_loop.py", result)

    def test_list_all_python_files_recursive(self):
        """测试递归列出所有 Python 文件"""
        result = self._run_tool(directory=".", pattern="**/*.py")
        self.assertIn("agent_loop.py", result)
        # 应该包含子目录的文件
        self.assertTrue("/" in result or "\\" in result)

    def test_list_markdown_files(self):
        """测试列出 Markdown 文件"""
        result = self._run_tool(directory=".", pattern="**/*.md")
        self.assertIn("README.md", result)

    def test_directory_not_exists(self):
        """测试目录不存在"""
        result = self._run_tool(directory="nonexistent_dir")
        self.assertIn("错误", result)
        self.assertIn("不存在", result)

    def test_no_matching_files(self):
        """测试没有匹配的文件"""
        result = self._run_tool(directory=".", pattern="*.xyz")
        self.assertIn("未找到", result)

    def test_max_files_limit(self):
        """测试文件数量限制"""
        result = self._run_tool(directory=".", pattern="**/*", max_files=5)
        lines = result.split("\n")
        # 应该有限制提示或者实际文件数少于等于5
        if "还有" in result:
            self.assertIn("只显示前 5 个", result)
        else:
            # 如果总文件数少于5，则不会有限制提示
            self.assertLessEqual(len(lines), 5)


if __name__ == "__main__":
    unittest.main()

"""测试 file_search 工具。"""
import unittest
from pathlib import Path

from tools.core.runner import create_tool_context
from tools.search.file_search import run, _fuzzy_match_score


class TestFuzzyMatch(unittest.TestCase):
    """测试模糊匹配算法。"""

    def test_exact_match(self):
        """完全匹配应该返回最高分"""
        score = _fuzzy_match_score("test", "test")
        self.assertEqual(score, 100)

    def test_substring_match(self):
        """子串匹配应该返回高分"""
        score = _fuzzy_match_score("user", "user_model.py")
        self.assertGreater(score, 85)  # 开头匹配应该 >= 95

    def test_subsequence_match(self):
        """子序列匹配应该返回中等分数"""
        # "usmd" 匹配 "user_model"
        score = _fuzzy_match_score("usmd", "user_model")
        self.assertGreater(score, 50)
        self.assertLess(score, 80)

    def test_no_match(self):
        """不匹配应该返回 0"""
        score = _fuzzy_match_score("xyz", "user_model.py")
        self.assertEqual(score, 0)

    def test_case_insensitive(self):
        """应该忽略大小写"""
        score1 = _fuzzy_match_score("USER", "user_model.py")
        score2 = _fuzzy_match_score("user", "USER_MODEL.py")
        self.assertGreater(score1, 85)
        self.assertGreater(score2, 85)


class TestFileSearch(unittest.TestCase):
    """测试文件搜索功能。"""

    def _run_tool(self, **payload):
        ctx = create_tool_context(Path.cwd())
        return run(ctx, payload)

    def test_search_python_files(self):
        """搜索 Python 文件"""
        result = self._run_tool(query="agent", directory=".", max_results=10)
        self.assertIn("找到", result)
        # 应该能找到 agent_loop.py
        self.assertTrue("agent" in result.lower())

    def test_search_with_abbreviation(self):
        """测试缩写搜索"""
        result = self._run_tool(query="agloop", directory=".")
        # 应该能找到 agent_loop.py
        self.assertIn("agent", result.lower())

    def test_search_nonexistent_directory(self):
        """测试不存在的目录"""
        result = self._run_tool(query="test", directory="nonexistent")
        self.assertIn("错误", result)

    def test_max_results_limit(self):
        """测试结果数量限制"""
        result = self._run_tool(query="py", directory=".", max_results=5)
        lines = [l for l in result.split("\n") if l.strip() and "相关性" in l]
        # 返回的文件数应该不超过 5
        self.assertLessEqual(len(lines), 5)

    def test_no_match(self):
        """测试没有匹配结果"""
        result = self._run_tool(query="xyzabc123notexist")
        self.assertIn("未找到", result)


if __name__ == "__main__":
    unittest.main()

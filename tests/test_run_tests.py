"""测试 run_tests 工具。"""
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.macos_executor import CommandExecResult
from tools.core.runner import create_tool_context
from tools.testing.run_tests import run, _parse_unittest_output, _parse_pytest_output


class TestParseOutput(unittest.TestCase):
    """测试输出解析功能。"""

    def test_parse_unittest_success(self):
        """测试解析成功的 unittest 输出"""
        output = """
test_example (test_module.TestCase) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.123s

OK
"""
        result = _parse_unittest_output(output)
        self.assertEqual(result["passed"], 5)
        self.assertEqual(result["failed"], 0)
        self.assertTrue(result["success"])

    def test_parse_unittest_failures(self):
        """测试解析失败的 unittest 输出"""
        output = """
FAIL: test_example (test_module.TestCase)
ERROR: test_other (test_module.TestCase)

----------------------------------------------------------------------
Ran 5 tests in 0.123s

FAILED (failures=1, errors=1)
"""
        result = _parse_unittest_output(output)
        self.assertEqual(result["passed"], 3)
        self.assertEqual(result["failed"], 2)
        self.assertFalse(result["success"])
        self.assertEqual(len(result["errors"]), 2)

    def test_parse_pytest_success(self):
        """测试解析成功的 pytest 输出"""
        output = "5 passed in 1.23s"
        result = _parse_pytest_output(output)
        self.assertEqual(result["passed"], 5)
        self.assertEqual(result["failed"], 0)
        self.assertTrue(result["success"])

    def test_parse_pytest_failures(self):
        """测试解析失败的 pytest 输出"""
        output = """
FAILED tests/test_user.py::test_login - AssertionError: wrong
3 passed, 2 failed in 2.45s
"""
        result = _parse_pytest_output(output)
        self.assertEqual(result["passed"], 3)
        self.assertEqual(result["failed"], 2)
        self.assertFalse(result["success"])
        self.assertGreater(len(result["errors"]), 0)


class TestRunTests(unittest.TestCase):
    """测试运行测试功能。"""

    def _run_tool(self, output=None, exit_code=0, **payload):
        ctx = create_tool_context(Path.cwd())
        exec_result = CommandExecResult(
            ok=exit_code == 0,
            exit_code=exit_code,
            stdout=output or "Ran 1 test in 0.001s\n\nOK\n",
            stderr="",
        )
        with patch.object(ctx.command_executor, "run", return_value=exec_result) as run_mock:
            result = run(ctx, payload)
        return result, run_mock

    def test_run_existing_tests(self):
        """测试运行现有测试"""
        result, run_mock = self._run_tool(pattern="test_list_files.py")
        self.assertIn("测试框架", result)
        self.assertIn("命令", result)
        self.assertIn("结果", result)
        self.assertTrue(run_mock.called)
        command = (
            run_mock.call_args.kwargs["command"]
            if "command" in run_mock.call_args.kwargs
            else run_mock.call_args.args[0]
        )
        self.assertIn("test_list_files.py", command)

    def test_run_all_tests(self):
        """测试运行所有测试"""
        result, run_mock = self._run_tool()
        self.assertIn("测试框架", result)
        self.assertTrue(run_mock.called)

    def test_run_nonexistent_pattern(self):
        """测试运行不存在的测试模式"""
        result, _run_mock = self._run_tool(
            output="Ran 0 tests in 0.000s\n\nOK\n",
            pattern="test_nonexistent_xyz.py",
        )
        self.assertIn("通过: 0", result)


if __name__ == "__main__":
    unittest.main()

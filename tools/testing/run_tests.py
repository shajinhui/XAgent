"""运行测试工具 - 执行测试并解析结果（中文注释）。"""
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path
from pydantic import BaseModel, Field
from security.permissions import PermissionProfile
from tools.core.types import ToolExecutionContext, ToolMeta


META = ToolMeta(
    name="run_tests",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


class RunTestsArgs(BaseModel):
    """run_tests 工具入参。"""

    pattern: str = Field(
        default="",
        description="测试文件模式，如 test_*.py, tests/test_user.py。留空运行所有测试",
    )
    verbose: bool = Field(
        default=False,
        description="是否显示详细输出",
    )
    timeout: int = Field(
        default=60,
        description="超时时间（秒），默认 60 秒",
    )


def schema() -> dict:
    """返回供模型调用的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": META.name,
            "description": "在当前 workspace 运行项目测试并摘要结果",
            "parameters": RunTestsArgs.model_json_schema(),
        },
    }


def _parse_pytest_output(output: str) -> dict:
    """解析 pytest 输出。"""
    result = {
        "framework": "pytest",
        "passed": 0,
        "failed": 0,
        "errors": [],
        "success": False,
    }

    # 匹配 "5 passed, 2 failed in 1.23s"
    summary_match = re.search(
        r"(\d+)\s+passed(?:,\s+(\d+)\s+failed)?.*in\s+([\d.]+)s", output
    )
    if summary_match:
        result["passed"] = int(summary_match.group(1))
        result["failed"] = int(summary_match.group(2) or 0)
        result["duration"] = float(summary_match.group(3))

    # 提取失败的测试
    failed_tests = re.findall(r"FAILED\s+([\w/.:]+)\s+-\s+(.*?)(?=\n|$)", output)
    for test_name, reason in failed_tests[:5]:  # 最多返回 5 个
        result["errors"].append(f"{test_name}: {reason.strip()}")

    result["success"] = result["failed"] == 0 and result["passed"] > 0

    return result


def _parse_unittest_output(output: str) -> dict:
    """解析 unittest 输出。"""
    result = {
        "framework": "unittest",
        "passed": 0,
        "failed": 0,
        "errors": [],
        "success": False,
    }

    # 匹配 "Ran 5 tests in 0.123s"
    ran_match = re.search(r"Ran\s+(\d+)\s+test", output)
    if ran_match:
        total = int(ran_match.group(1))

        # 匹配 "FAILED (failures=2)"
        failed_match = re.search(r"failures?=(\d+)", output)
        errors_match = re.search(r"errors?=(\d+)", output)

        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(errors_match.group(1)) if errors_match else 0

        result["failed"] = failed + errors
        result["passed"] = total - result["failed"]

    # 提取错误信息
    error_lines = re.findall(r"(FAIL|ERROR):\s+(.*)", output)
    for error_type, test_name in error_lines[:5]:
        result["errors"].append(f"{error_type}: {test_name}")

    result["success"] = "OK" in output or (result["passed"] > 0 and result["failed"] == 0)

    return result


def _detect_test_framework(project_root: Path) -> str:
    """通过项目文件静态判断测试框架，避免探测阶段绕过沙箱起进程。"""

    for config_name in ("pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"):
        config = project_root / config_name
        if not config.exists() or not config.is_file():
            continue
        try:
            content = config.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        if "pytest" in content or "[tool.pytest" in content:
            return "pytest"

    for requirements in project_root.glob("requirements*.txt"):
        try:
            content = requirements.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        if "pytest" in content:
            return "pytest"

    return "unittest"


def _build_test_command(framework: str, args: RunTestsArgs) -> str:
    """构建最终交给 sandbox executor 的 shell 命令。"""

    if framework == "pytest":
        argv = [sys.executable, "-m", "pytest"]
        if args.pattern:
            argv.append(args.pattern)
        argv.append("-v" if args.verbose else "-q")
        argv.append("--tb=short")
        return shlex.join(argv)

    argv = [sys.executable, "-m", "unittest", "discover"]
    if args.pattern:
        argv.extend(["-p", args.pattern])
    if args.verbose:
        argv.append("-v")
    return shlex.join(argv)


def run(ctx: ToolExecutionContext, payload: dict) -> str:
    """
    运行测试并解析结果。

    Args:
        ctx: 工具执行上下文，提供当前工作目录
        payload: 模型传入的工具参数

    Returns:
        格式化的测试结果
    """
    args = RunTestsArgs(**payload)
    try:
        framework = _detect_test_framework(ctx.current_dir)
        command = _build_test_command(framework, args)

        # 测试会执行项目代码，必须继承当前 filesystem/network/sandbox 边界。
        result = ctx.command_executor.run(
            command,
            filesystem_policy=ctx.filesystem_policy,
            network_policy=ctx.network_policy,
            timeout_seconds=args.timeout,
            cwd=ctx.current_dir,
            sandbox_enabled=ctx.permission_profile != PermissionProfile.DANGER_NO_SANDBOX,
        )

        output = result.stdout + result.stderr

        # 解析结果
        if framework == "pytest":
            parsed = _parse_pytest_output(output)
        else:
            parsed = _parse_unittest_output(output)
        parsed["success"] = bool(parsed["success"] and result.ok)

        # 格式化输出
        lines = [
            f"测试框架: {parsed['framework']}",
            f"命令: {command}",
            f"结果: {'✓ 通过' if parsed['success'] else '✗ 失败'}",
            f"通过: {parsed['passed']}",
            f"失败: {parsed['failed']}",
            f"退出码: {result.exit_code}",
        ]

        if "duration" in parsed:
            lines.append(f"耗时: {parsed['duration']:.2f}s")

        if parsed["errors"]:
            lines.append("\n失败的测试:")
            for error in parsed["errors"]:
                lines.append(f"  • {error}")

        if args.verbose and output:
            lines.append(f"\n完整输出:\n{output}")

        return "\n".join(lines)

    except Exception as e:
        return f"错误: {str(e)}"

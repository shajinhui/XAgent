from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.macos_executor import SecureMacOSSandboxExecutor


class MacOSSandboxExecutorTests(unittest.TestCase):
    def test_run_invokes_sandbox_exec_with_profile(self) -> None:
        executor = SecureMacOSSandboxExecutor(Path.cwd())
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="ok\n",
            stderr="",
        )

        with (
            patch("sandbox.macos_executor.platform.system", return_value="Darwin"),
            patch("sandbox.macos_executor.shutil.which", return_value="/usr/bin/sandbox-exec"),
            patch("sandbox.macos_executor.subprocess.run", return_value=completed) as run_mock,
        ):
            result = executor.run("echo ok", timeout_seconds=3)

        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "ok\n")
        call_args = run_mock.call_args.args[0]
        self.assertEqual(call_args[0], "/usr/bin/sandbox-exec")
        self.assertEqual(call_args[1], "-p")
        self.assertIn("(deny default)", call_args[2])
        self.assertIn("(allow file-read*)", call_args[2])
        self.assertIn("(allow file-write*", call_args[2])
        self.assertIn("/bin/sh", call_args)

    def test_run_uses_workspace_internal_cwd_without_changing_profile_root(self) -> None:
        project_root = Path.cwd()
        command_cwd = project_root / "tests"
        executor = SecureMacOSSandboxExecutor(project_root)
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="ok\n",
            stderr="",
        )

        with (
            patch("sandbox.macos_executor.platform.system", return_value="Darwin"),
            patch("sandbox.macos_executor.shutil.which", return_value="/usr/bin/sandbox-exec"),
            patch("sandbox.macos_executor.subprocess.run", return_value=completed) as run_mock,
        ):
            result = executor.run("pwd", timeout_seconds=3, cwd=command_cwd)

        self.assertTrue(result.ok)
        call_args = run_mock.call_args.args[0]
        shell_command = call_args[-1]
        self.assertIn(project_root.as_posix(), call_args[2])
        self.assertIn(f"cd {command_cwd.as_posix()}", shell_command)
        self.assertEqual(run_mock.call_args.kwargs["cwd"], command_cwd.resolve())

    def test_run_rejects_cwd_outside_project_root(self) -> None:
        executor = SecureMacOSSandboxExecutor(Path.cwd())

        with (
            patch("sandbox.macos_executor.platform.system", return_value="Darwin"),
            patch("sandbox.macos_executor.shutil.which", return_value="/usr/bin/sandbox-exec"),
        ):
            result = executor.run("pwd", cwd=Path("/"))

        self.assertFalse(result.ok)
        self.assertIn("命令工作目录越界", result.stderr)

    def test_run_rejects_non_macos(self) -> None:
        executor = SecureMacOSSandboxExecutor(Path.cwd())

        with patch("sandbox.macos_executor.platform.system", return_value="Linux"):
            result = executor.run("echo ok")

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 127)
        self.assertIn("仅支持 Darwin/macOS", result.stderr)

    def test_run_explains_nested_sandbox_failure(self) -> None:
        executor = SecureMacOSSandboxExecutor(Path.cwd())
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=71,
            stdout="",
            stderr="sandbox-exec: sandbox_apply: Operation not permitted\n",
        )

        with (
            patch("sandbox.macos_executor.platform.system", return_value="Darwin"),
            patch("sandbox.macos_executor.shutil.which", return_value="/usr/bin/sandbox-exec"),
            patch("sandbox.macos_executor.subprocess.run", return_value=completed),
        ):
            result = executor.run("echo ok")

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 71)
        self.assertIn("无法再次应用 macOS Seatbelt profile", result.stderr)


if __name__ == "__main__":
    unittest.main()

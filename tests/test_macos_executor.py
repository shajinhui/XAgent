from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.macos_executor import SecureMacOSSandboxExecutor
from security import FileSystemPolicy, NetworkPolicy
from workspace import AdditionalRoot


class MacOSSandboxExecutorTests(unittest.TestCase):
    def test_run_invokes_sandbox_exec_with_profile(self) -> None:
        selected_root = Path.cwd()
        filesystem_policy = FileSystemPolicy.workspace_write(selected_root)
        executor = SecureMacOSSandboxExecutor(selected_root)
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
            result = executor.run(
                "echo ok",
                filesystem_policy=filesystem_policy,
                network_policy=NetworkPolicy.RESTRICTED,
                timeout_seconds=3,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "ok\n")
        call_args = run_mock.call_args.args[0]
        self.assertEqual(call_args[0], "/usr/bin/sandbox-exec")
        self.assertEqual(call_args[1], "-p")
        self.assertIn("(deny default)", call_args[2])
        self.assertIn("(allow file-read*)", call_args[2])
        self.assertIn("(allow file-write*", call_args[2])
        self.assertIn(f'(subpath "{selected_root.as_posix()}")', call_args[2])
        self.assertIn(f'(deny file-read* (literal "{selected_root.as_posix()}/.env"))', call_args[2])
        self.assertNotIn("(allow network*)", call_args[2])
        self.assertIn("/bin/sh", call_args)

    def test_profile_includes_additional_roots_from_filesystem_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected_root = Path(tmp) / "workspace"
            read_root = Path(tmp) / "read-only"
            write_root = Path(tmp) / "write-root"
            selected_root.mkdir()
            read_root.mkdir()
            write_root.mkdir()
            filesystem_policy = FileSystemPolicy.workspace_write(
                selected_root,
                additional_roots=[
                    AdditionalRoot(read_root, "read"),
                    AdditionalRoot(write_root, "write"),
                ],
            )
            executor = SecureMacOSSandboxExecutor(selected_root)
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
                result = executor.run(
                    "echo ok",
                    filesystem_policy=filesystem_policy,
                    network_policy=NetworkPolicy.ENABLED,
                    timeout_seconds=3,
                )

            self.assertTrue(result.ok)
            profile = run_mock.call_args.args[0][2]
            self.assertIn(f'(subpath "{write_root.resolve().as_posix()}")', profile)
            self.assertIn("(allow network*)", profile)

    @unittest.skipUnless(
        platform.system() == "Darwin" and shutil.which("sandbox-exec"),
        "需要 macOS sandbox-exec 做端到端验证",
    )
    def test_policy_profile_launches_command_and_blocks_protected_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected_root = Path(tmp)
            (selected_root / ".env").write_text("SECRET=1\n", encoding="utf-8")
            filesystem_policy = FileSystemPolicy.workspace_write(selected_root)
            executor = SecureMacOSSandboxExecutor(selected_root)

            list_result = executor.run(
                "pwd && ls -la",
                filesystem_policy=filesystem_policy,
                network_policy=NetworkPolicy.RESTRICTED,
                timeout_seconds=3,
            )
            env_result = executor.run(
                "cat .env",
                filesystem_policy=filesystem_policy,
                network_policy=NetworkPolicy.RESTRICTED,
                timeout_seconds=3,
            )

        self.assertTrue(list_result.ok, list_result.stderr)
        self.assertIn(selected_root.as_posix(), list_result.stdout)
        self.assertFalse(env_result.ok)
        self.assertIn("Operation not permitted", env_result.stderr)

    def test_run_uses_workspace_internal_cwd_without_changing_profile_root(self) -> None:
        project_root = Path.cwd()
        command_cwd = project_root / "tests"
        filesystem_policy = FileSystemPolicy.workspace_write(project_root)
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
            result = executor.run(
                "pwd",
                filesystem_policy=filesystem_policy,
                network_policy=NetworkPolicy.RESTRICTED,
                timeout_seconds=3,
                cwd=command_cwd,
            )

        self.assertTrue(result.ok)
        call_args = run_mock.call_args.args[0]
        shell_command = call_args[-1]
        self.assertIn(project_root.as_posix(), call_args[2])
        self.assertIn(f"cd {command_cwd.as_posix()}", shell_command)
        self.assertEqual(run_mock.call_args.kwargs["cwd"], command_cwd.resolve())

    def test_run_rejects_cwd_outside_project_root(self) -> None:
        selected_root = Path.cwd()
        filesystem_policy = FileSystemPolicy.workspace_write(selected_root)
        executor = SecureMacOSSandboxExecutor(selected_root)

        with (
            patch("sandbox.macos_executor.platform.system", return_value="Darwin"),
            patch("sandbox.macos_executor.shutil.which", return_value="/usr/bin/sandbox-exec"),
        ):
            result = executor.run(
                "pwd",
                filesystem_policy=filesystem_policy,
                network_policy=NetworkPolicy.RESTRICTED,
                cwd=Path("/"),
            )

        self.assertFalse(result.ok)
        self.assertIn("命令工作目录无效", result.stderr)

    def test_run_rejects_non_macos(self) -> None:
        executor = SecureMacOSSandboxExecutor(Path.cwd())
        filesystem_policy = FileSystemPolicy.workspace_write(Path.cwd())

        with patch("sandbox.macos_executor.platform.system", return_value="Linux"):
            result = executor.run(
                "echo ok",
                filesystem_policy=filesystem_policy,
                network_policy=NetworkPolicy.RESTRICTED,
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 127)
        self.assertIn("仅支持 Darwin/macOS", result.stderr)

    def test_run_without_sandbox_bypasses_sandbox_exec(self) -> None:
        selected_root = Path.cwd()
        filesystem_policy = FileSystemPolicy.danger_full_access(selected_root)
        executor = SecureMacOSSandboxExecutor(selected_root)
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="ok\n",
            stderr="",
        )

        with (
            patch("sandbox.macos_executor.shutil.which") as which_mock,
            patch("sandbox.macos_executor.subprocess.run", return_value=completed) as run_mock,
        ):
            result = executor.run(
                "echo ok",
                filesystem_policy=filesystem_policy,
                network_policy=NetworkPolicy.ENABLED,
                sandbox_enabled=False,
            )

        self.assertTrue(result.ok)
        self.assertFalse(which_mock.called)
        self.assertEqual(run_mock.call_args.args[0][:2], ["/bin/sh", "-lc"])

    def test_run_explains_nested_sandbox_failure(self) -> None:
        executor = SecureMacOSSandboxExecutor(Path.cwd())
        filesystem_policy = FileSystemPolicy.workspace_write(Path.cwd())
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
            result = executor.run(
                "echo ok",
                filesystem_policy=filesystem_policy,
                network_policy=NetworkPolicy.RESTRICTED,
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 71)
        self.assertIn("无法再次应用 macOS Seatbelt profile", result.stderr)


if __name__ == "__main__":
    unittest.main()

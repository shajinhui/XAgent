from __future__ import annotations

import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from processes import ManagedProcessError, ManagedProcessManager
from sandbox.macos_executor import SecureMacOSSandboxExecutor
from security import FileSystemPolicy, NetworkPolicy


class ManagedProcessManagerTests(unittest.TestCase):
    def test_start_and_stop_only_runtime_owned_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ManagedProcessManager(root)
            executor = SecureMacOSSandboxExecutor(root)
            result = manager.start(
                "python3 -c 'import time; time.sleep(30)'",
                cwd=root,
                executor=executor,
                filesystem_policy=FileSystemPolicy.danger_full_access(root),
                network_policy=NetworkPolicy.ENABLED,
                sandbox_enabled=False,
            )

            self.assertEqual(result["status"], "running")
            self.assertGreater(result["pid"], 0)
            self.assertEqual(len(manager.list()), 1)

            stopped = manager.stop(result["process_id"], timeout=2)

            self.assertEqual(stopped["status"], "stopped")
            self.assertIsNotNone(stopped["exit_code"])

    def test_unknown_process_id_is_never_treated_as_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ManagedProcessManager(Path(tmp))

            with self.assertRaises(ManagedProcessError) as raised:
                manager.stop("12345")

            self.assertEqual(raised.exception.metadata["error_type"], "unknown_managed_process")

    def test_occupied_port_blocks_start_without_touching_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ManagedProcessManager(root)
            manager.inspect_port = Mock(
                return_value=[
                    {
                        "pid": 999,
                        "command": "existing-service",
                        "address": "127.0.0.1:5173",
                        "managed_process_id": None,
                    }
                ]
            )
            executor = Mock()

            with self.assertRaises(ManagedProcessError) as raised:
                manager.start(
                    "python3 -m http.server 5173",
                    cwd=root,
                    executor=executor,
                    filesystem_policy=FileSystemPolicy.danger_full_access(root),
                    network_policy=NetworkPolicy.ENABLED,
                    sandbox_enabled=False,
                    expected_port=5173,
                )

            self.assertEqual(raised.exception.metadata["error_type"], "port_in_use")
            executor.start_managed.assert_not_called()

    @unittest.skipUnless(shutil.which("lsof"), "需要 lsof 验证 listener 归属")
    def test_expected_port_must_belong_to_started_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ManagedProcessManager(root)
            executor = SecureMacOSSandboxExecutor(root)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]

            result = manager.start(
                f"python3 -m http.server {port} --bind 127.0.0.1",
                cwd=root,
                executor=executor,
                filesystem_policy=FileSystemPolicy.danger_full_access(root),
                network_policy=NetworkPolicy.ENABLED,
                sandbox_enabled=False,
                expected_port=port,
                startup_timeout=5,
            )
            try:
                self.assertEqual(result["status"], "running")
                self.assertTrue(result["listeners"])
                self.assertTrue(
                    all(
                        item["managed_process_id"] == result["process_id"]
                        for item in result["listeners"]
                    )
                )
            finally:
                manager.stop(result["process_id"], timeout=2)


if __name__ == "__main__":
    unittest.main()

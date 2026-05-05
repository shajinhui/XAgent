from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.registry import ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def test_metadata_exposes_tool_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp))

            metadata = registry.metadata()

            self.assertTrue(metadata["read_file"]["is_read_only"])
            self.assertTrue(metadata["read_file"]["supports_parallel"])
            self.assertTrue(metadata["write_file"]["is_mutating"])
            self.assertTrue(metadata["run_command"]["requires_approval"])

    def test_unknown_tool_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp))

            result = registry.execute("missing", "{}")

            self.assertFalse(result.ok)
            self.assertIn("未知工具", result.content)

    def test_bad_json_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp))

            result = registry.execute("read_file", "{")

            self.assertFalse(result.ok)
            self.assertIn("工具参数不是合法 JSON", result.content)

    def test_run_command_unknown_command_requests_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp))

            result = registry.execute(
                "run_command",
                json.dumps({"command": "ruff check ."}),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["permission_action"], "ask")
            self.assertEqual(result.metadata["category"], "command_approval")

    def test_run_command_dangerous_command_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp))

            result = registry.execute(
                "run_command",
                json.dumps({"command": "rm -rf /"}),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["permission_action"], "deny")
            self.assertEqual(result.metadata["category"], "dangerous_shell")

    def test_run_command_allowed_command_still_requests_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp))

            result = registry.execute(
                "run_command",
                json.dumps({"command": "python -m unittest discover -s tests"}),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["permission_action"], "ask")
            self.assertEqual(result.metadata["category"], "command_approval")

    def test_mutating_file_tool_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ToolRegistry(root)

            result = registry.execute(
                "write_file",
                json.dumps({"path": "created.txt", "content": "hello"}),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["permission_action"], "ask")
            self.assertFalse((root / "created.txt").exists())

    def test_mutating_file_tool_runs_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ToolRegistry(root)

            result = registry.execute(
                "write_file",
                json.dumps({"path": "created.txt", "content": "hello"}),
                approved=True,
            )

            self.assertTrue(result.ok)
            self.assertEqual((root / "created.txt").read_text(encoding="utf-8"), "hello")


if __name__ == "__main__":
    unittest.main()

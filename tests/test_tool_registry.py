from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.macos_executor import CommandExecResult
from tools.core.catalog import build_default_registry, builtin_tools
from tools.core.protocol import FunctionTool
from tools.core.registry import ToolRegistry as CoreToolRegistry
from tools.core.router import ToolRouter
from tools.core.runner import ToolRunner, create_tool_context
from tools.core.types import ToolMeta, ToolPermissionError, ToolResult
from tools.registry import ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def test_catalog_contains_default_tool_groups(self) -> None:
        tool_names = {tool.meta.name for tool in builtin_tools()}

        self.assertEqual(
            tool_names,
            {
                "read_file",
                "ask_user",
                "write_file",
                "edit_file",
                "grep",
                "run_command",
                "web_fetch",
            },
        )

    def test_core_registry_rejects_duplicate_tool_names(self) -> None:
        meta = ToolMeta("sample", True, False, True)
        first = FunctionTool(meta, lambda: {"name": "sample"}, lambda _ctx, _payload: "ok")
        second = FunctionTool(meta, lambda: {"name": "sample"}, lambda _ctx, _payload: "ok")

        registry = CoreToolRegistry([first])

        with self.assertRaises(ValueError):
            registry.register_tool(second)

    def test_core_registry_exposes_schema_and_metadata_without_running_tools(self) -> None:
        registry = build_default_registry()

        metadata = registry.metadata()
        schemas = registry.schemas()

        self.assertTrue(registry.has_tool("read_file"))
        self.assertTrue(metadata["read_file"]["is_read_only"])
        self.assertIn("read_file", [schema["function"]["name"] for schema in schemas])

    def test_tool_router_builds_invocation_from_model_tool_call(self) -> None:
        invocation = ToolRouter.build_tool_invocation(
            {
                "id": "call-1",
                "function": {
                    "name": "read_file",
                    "arguments": "{\"path\":\"README.md\"}",
                },
            }
        )

        self.assertEqual(invocation.name, "read_file")
        self.assertEqual(invocation.call_id, "call-1")
        self.assertEqual(invocation.arguments, "{\"path\":\"README.md\"}")

    def test_metadata_exposes_tool_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp))

            metadata = registry.metadata()

            self.assertTrue(metadata["read_file"]["is_read_only"])
            self.assertTrue(metadata["read_file"]["supports_parallel"])
            self.assertTrue(metadata["ask_user"]["is_read_only"])
            self.assertFalse(metadata["ask_user"]["supports_parallel"])
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

    def test_runner_wraps_permission_exception(self) -> None:
        def run(_ctx, _payload):
            raise ToolPermissionError(
                "needs approval",
                metadata={"permission_action": "ask", "category": "sample"},
            )

        meta = ToolMeta("sample", True, False, False)
        tool = FunctionTool(meta, lambda: {}, run)
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(CoreToolRegistry([tool]), create_tool_context(Path(tmp)))

            result = runner.execute("sample", "{}")

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata["permission_action"], "ask")
        self.assertEqual(result.metadata["category"], "sample")

    def test_runner_wraps_runtime_exception(self) -> None:
        def run(_ctx, _payload):
            raise RuntimeError("boom")

        meta = ToolMeta("sample", True, False, False)
        tool = FunctionTool(meta, lambda: {}, run)
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(CoreToolRegistry([tool]), create_tool_context(Path(tmp)))

            result = runner.execute("sample", "{}")

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata["error_type"], "runtime_error")
        self.assertIn("boom", result.content)

    def test_runner_preserves_direct_tool_result(self) -> None:
        meta = ToolMeta("sample", True, False, False)
        tool = FunctionTool(
            meta,
            lambda: {},
            lambda _ctx, _payload: ToolResult(
                ok=False,
                content="needs user",
                metadata={"user_interaction_action": "ask"},
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(CoreToolRegistry([tool]), create_tool_context(Path(tmp)))

            result = runner.execute("sample", "{}")

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata["tool"], "sample")
        self.assertEqual(result.metadata["user_interaction_action"], "ask")

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

    def test_ask_user_returns_clarification_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp))

            result = registry.execute(
                "ask_user",
                json.dumps(
                    {
                        "question": "这次优先覆盖到什么范围？",
                        "options": [
                            {
                                "id": "core",
                                "label": "核心后端",
                                "description": "只覆盖 runtime 和 session",
                                "recommended": True,
                            }
                        ],
                    }
                ),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["user_interaction_action"], "ask")
            self.assertEqual(result.metadata["question"], "这次优先覆盖到什么范围？")
            self.assertEqual(result.metadata["options"][0]["id"], "core")

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

    def test_run_command_invalid_cwd_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(Path(tmp))

            result = registry.execute(
                "run_command",
                json.dumps({"command": "echo ok", "cwd": ".."}),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["permission_action"], "deny")
            self.assertEqual(result.metadata["category"], "command_cwd")

    def test_run_command_approved_passes_resolved_cwd_to_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "desktop"
            nested.mkdir()
            registry = ToolRegistry(root)

            with patch.object(
                registry.ctx.command_executor,
                "run",
                return_value=CommandExecResult(True, 0, "ok\n", ""),
            ) as run_mock:
                result = registry.execute(
                    "run_command",
                    json.dumps({"command": "echo ok", "cwd": "desktop"}),
                    approved=True,
                )

            self.assertTrue(result.ok)
            self.assertIn("cwd: desktop", result.content)
            self.assertEqual(run_mock.call_args.kwargs["cwd"], nested.resolve())

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

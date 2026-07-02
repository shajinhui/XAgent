from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patch import PatchStatus, PatchStore, build_file_change, build_patch_proposal
from sandbox.macos_executor import CommandExecResult
from security import ApprovalPolicy, ExecPolicy, PermissionProfile
from tools.core.catalog import build_default_registry, builtin_tools
from tools.core.protocol import FunctionTool
from tools.core.registry import ToolRegistry as CoreToolRegistry
from tools.core.router import ToolRouter
from tools.core.runner import ToolRunner, create_tool_context
from tools.core.types import ToolMeta, ToolPermissionError, ToolResult
from workspace import AdditionalRoot


def build_default_runner(root: Path, session_id: str = "default") -> tuple[CoreToolRegistry, ToolRunner]:
    registry = build_default_registry()
    runner = ToolRunner(registry, create_tool_context(root, session_id))
    return registry, runner


def build_runner_with_exec_policy(root: Path, exec_policy: ExecPolicy) -> tuple[CoreToolRegistry, ToolRunner]:
    registry = build_default_registry()
    runner = ToolRunner(registry, create_tool_context(root, exec_policy=exec_policy))
    return registry, runner


def run_git(root: Path, *args: str) -> None:
    """在测试仓库中执行 Git 命令。"""

    subprocess.run(
        ["git", "-C", root.as_posix(), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def init_git_repo(root: Path) -> None:
    """创建带初始提交的测试 Git 仓库。"""

    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    run_git(root, "config", "user.email", "codex-mini@example.test")
    run_git(root, "config", "user.name", "Codex Mini Test")


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
                "list_files",
                "file_search",
                "run_tests",
                "update_plan",
                "create_task_list",
                "apply_patch",
                "reject_patch",
                "rollback_patch",
                "git_status",
                "git_diff",
                "git_diff_file",
                "git_changed_files",
                "read_skill",
                "read_skill_resource",
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
            registry, _runner = build_default_runner(Path(tmp))

            metadata = registry.metadata()

            self.assertTrue(metadata["read_file"]["is_read_only"])
            self.assertTrue(metadata["read_file"]["supports_parallel"])
            self.assertTrue(metadata["ask_user"]["is_read_only"])
            self.assertFalse(metadata["ask_user"]["supports_parallel"])
            self.assertTrue(metadata["write_file"]["is_mutating"])
            self.assertTrue(metadata["run_command"]["requires_approval"])
            self.assertTrue(metadata["run_tests"]["is_mutating"])
            self.assertTrue(metadata["run_tests"]["requires_approval"])
            self.assertTrue(metadata["update_plan"]["is_mutating"])
            self.assertTrue(metadata["update_plan"]["requires_approval"])
            self.assertTrue(metadata["create_task_list"]["is_mutating"])
            self.assertTrue(metadata["create_task_list"]["requires_approval"])
            self.assertTrue(metadata["apply_patch"]["is_mutating"])
            self.assertTrue(metadata["apply_patch"]["requires_approval"])
            self.assertTrue(metadata["reject_patch"]["is_mutating"])
            self.assertTrue(metadata["reject_patch"]["requires_approval"])
            self.assertTrue(metadata["rollback_patch"]["is_mutating"])
            self.assertTrue(metadata["rollback_patch"]["requires_approval"])
            self.assertTrue(metadata["git_status"]["is_read_only"])
            self.assertFalse(metadata["git_status"]["requires_approval"])
            self.assertTrue(metadata["git_diff"]["is_read_only"])
            self.assertFalse(metadata["git_diff"]["requires_approval"])

    def test_unknown_tool_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _registry, runner = build_default_runner(Path(tmp))

            result = runner.execute("missing", "{}")

            self.assertFalse(result.ok)
            self.assertIn("未知工具", result.content)

    def test_bad_json_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _registry, runner = build_default_runner(Path(tmp))

            result = runner.execute("read_file", "{")

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
            _registry, runner = build_default_runner(Path(tmp))

            result = runner.execute(
                "run_command",
                json.dumps({"command": "ruff check ."}),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["permission_action"], "ask")
            self.assertEqual(result.metadata["category"], "command_approval")
            self.assertEqual(result.metadata["suggested_prefix_rule"], ["ruff", "check"])
            self.assertEqual(result.metadata["permission_profile"], "workspace_write")
            self.assertEqual(result.metadata["approval_policy"], "ask-before-mutating")
            self.assertEqual(result.metadata["network_policy"], "restricted")
            self.assertEqual(result.metadata["reason"], "命令不在白名单中，需要用户确认: ruff")
            self.assertTrue(result.metadata["sandbox_enabled"])
            self.assertFalse(result.metadata["network_enabled"])
            self.assertEqual(result.metadata["current_dir"], Path(tmp).resolve().as_posix())

    def test_ask_user_returns_clarification_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _registry, runner = build_default_runner(Path(tmp))

            result = runner.execute(
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
            _registry, runner = build_default_runner(Path(tmp))

            result = runner.execute(
                "run_command",
                json.dumps({"command": "rm -rf /"}),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["permission_action"], "deny")
            self.assertEqual(result.metadata["category"], "dangerous_shell")

    def test_run_command_allowed_command_still_requests_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _registry, runner = build_default_runner(Path(tmp))

            result = runner.execute(
                "run_command",
                json.dumps({"command": "python -m unittest discover -s tests"}),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["permission_action"], "ask")
            self.assertEqual(result.metadata["category"], "command_approval")

    def test_run_command_simple_read_only_command_skips_permission_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _registry, runner = build_default_runner(root)

            with patch.object(
                runner.ctx.command_executor,
                "run",
                return_value=CommandExecResult(True, 0, "ok\n", ""),
            ) as run_mock:
                result = runner.execute(
                    "run_command",
                    json.dumps({"command": "ls -la"}),
                )

            self.assertTrue(result.ok)
            self.assertTrue(run_mock.called)
            self.assertIn("exit_code: 0", result.content)

    def test_run_command_compound_read_only_command_still_requests_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _registry, runner = build_default_runner(Path(tmp))

            result = runner.execute(
                "run_command",
                json.dumps({"command": "pwd && ls -la"}),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["permission_action"], "ask")
            self.assertEqual(result.metadata["category"], "command_approval")

    def test_run_command_approval_policy_never_denies_permission_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(Path(tmp), approval_policy=ApprovalPolicy.NEVER),
            )

            result = runner.execute("run_command", json.dumps({"command": "echo ok"}))

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["permission_action"], "deny")
            self.assertEqual(result.metadata["category"], "approval_unavailable")
            self.assertTrue(result.metadata["sandbox_enabled"])
            self.assertFalse(result.metadata["network_enabled"])

    def test_edit_file_dry_run_returns_diff_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "edit_file",
                json.dumps(
                    {
                        "path": "sample.txt",
                        "start_line": 2,
                        "end_line": 2,
                        "replacement": "BETA",
                        "dry_run": True,
                    }
                ),
                approved=True,
            )

            self.assertTrue(result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "alpha\nbeta\ngamma\n")
            self.assertEqual(result.metadata["dry_run"], True)
            self.assertIn("patch_id", result.metadata)
            self.assertEqual(result.metadata["change_type"], "update")
            self.assertIn("-beta", result.content)
            self.assertIn("+BETA", result.content)
            proposal = PatchStore(root / ".codex-mini" / "patches").load(result.metadata["patch_id"])
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.PROPOSED)
            self.assertEqual(proposal.changed_paths, ["sample.txt"])

    def test_write_file_dry_run_returns_diff_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "write_file",
                json.dumps({"path": "sample.txt", "content": "new\n", "dry_run": True}),
                approved=True,
            )

            self.assertTrue(result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(result.metadata["dry_run"], True)
            self.assertIn("patch_id", result.metadata)
            self.assertEqual(result.metadata["change_type"], "update")
            self.assertIn("--- a/sample.txt", result.content)
            self.assertIn("+++ b/sample.txt", result.content)
            self.assertIn("-old", result.content)
            self.assertIn("+new", result.content)
            proposal = PatchStore(root / ".codex-mini" / "patches").load(result.metadata["patch_id"])
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.PROPOSED)
            self.assertEqual(proposal.changed_paths, ["sample.txt"])

    def test_write_file_dry_run_for_new_file_does_not_create_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "nested" / "created.txt"
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "write_file",
                json.dumps({"path": "nested/created.txt", "content": "hello\n", "dry_run": True}),
                approved=True,
            )

            self.assertTrue(result.ok)
            self.assertFalse(target.exists())
            self.assertFalse(target.parent.exists())
            self.assertEqual(result.metadata["change_type"], "add")
            self.assertIn("patch_id", result.metadata)
            self.assertIn("--- /dev/null", result.content)
            self.assertIn("+++ b/nested/created.txt", result.content)
            self.assertIn("+hello", result.content)

    def test_apply_patch_applies_saved_write_file_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")
            _registry, runner = build_default_runner(root)
            preview = runner.execute(
                "write_file",
                json.dumps({"path": "sample.txt", "content": "new\n", "dry_run": True}),
                approved=True,
            )

            with patch.object(runner.ctx.command_executor, "run") as run_mock:
                result = runner.execute(
                    "apply_patch",
                    json.dumps({"patch_id": preview.metadata["patch_id"]}),
                    approved=True,
                )

            self.assertTrue(result.ok)
            run_mock.assert_not_called()
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(result.metadata["patch_status"], PatchStatus.APPLIED.value)
            proposal = PatchStore(root / ".codex-mini" / "patches").load(preview.metadata["patch_id"])
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.APPLIED)

    def test_apply_patch_runs_explicit_test_command_after_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")
            _registry, runner = build_default_runner(root)
            preview = runner.execute(
                "write_file",
                json.dumps({"path": "sample.txt", "content": "new\n", "dry_run": True}),
                approved=True,
            )
            test_command = "python -m unittest discover -s tests"

            with patch.object(
                runner.ctx.command_executor,
                "run",
                return_value=CommandExecResult(True, 0, "Ran 1 test in 0.001s\n\nOK\n", ""),
            ) as run_mock:
                result = runner.execute(
                    "apply_patch",
                    json.dumps(
                        {
                            "patch_id": preview.metadata["patch_id"],
                            "test_command": test_command,
                            "test_timeout": 7,
                        }
                    ),
                    approved=True,
                )

            self.assertTrue(result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            run_mock.assert_called_once()
            self.assertEqual(run_mock.call_args.args[0], test_command)
            self.assertEqual(run_mock.call_args.kwargs["timeout_seconds"], 7)
            self.assertEqual(run_mock.call_args.kwargs["cwd"], root.resolve())
            self.assertIs(run_mock.call_args.kwargs["filesystem_policy"], runner.ctx.filesystem_policy)
            self.assertIs(run_mock.call_args.kwargs["network_policy"], runner.ctx.network_policy)
            self.assertTrue(run_mock.call_args.kwargs["sandbox_enabled"])
            self.assertTrue(result.metadata["test_ok"])
            self.assertEqual(result.metadata["test_command"], test_command)
            self.assertEqual(result.metadata["test_exit_code"], 0)
            self.assertEqual(result.metadata["test_result"]["changed_paths"], ["sample.txt"])
            proposal = PatchStore(root / ".codex-mini" / "patches").load(preview.metadata["patch_id"])
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.APPLIED)
            self.assertTrue(proposal.metadata["test_result"]["ok"])

    def test_apply_patch_uses_default_project_test_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")
            registry = build_default_registry()
            test_command = "python -m unittest discover -s tests"
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    default_test_command=test_command,
                    default_test_timeout=11,
                ),
            )
            preview = runner.execute(
                "write_file",
                json.dumps({"path": "sample.txt", "content": "new\n", "dry_run": True}),
                approved=True,
            )

            with patch.object(
                runner.ctx.command_executor,
                "run",
                return_value=CommandExecResult(True, 0, "OK\n", ""),
            ) as run_mock:
                result = runner.execute(
                    "apply_patch",
                    json.dumps({"patch_id": preview.metadata["patch_id"]}),
                    approved=True,
                )

            self.assertTrue(result.ok)
            run_mock.assert_called_once()
            self.assertEqual(run_mock.call_args.args[0], test_command)
            self.assertEqual(run_mock.call_args.kwargs["timeout_seconds"], 11)
            self.assertEqual(result.metadata["test_source"], "project_config")
            self.assertTrue(result.metadata["test_ok"])
            proposal = PatchStore(root / ".codex-mini" / "patches").load(preview.metadata["patch_id"])
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.metadata["test_result"]["source"], "project_config")

    def test_apply_patch_uses_agents_md_default_test_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")
            registry = build_default_registry()
            test_command = "pytest tests/test_patch_proposals.py"
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    default_test_command=test_command,
                    default_test_source="agents_md",
                    default_test_timeout=13,
                ),
            )
            preview = runner.execute(
                "write_file",
                json.dumps({"path": "sample.txt", "content": "new\n", "dry_run": True}),
                approved=True,
            )

            with patch.object(
                runner.ctx.command_executor,
                "run",
                return_value=CommandExecResult(True, 0, "OK\n", ""),
            ) as run_mock:
                result = runner.execute(
                    "apply_patch",
                    json.dumps({"patch_id": preview.metadata["patch_id"]}),
                    approved=True,
                )

            self.assertTrue(result.ok)
            run_mock.assert_called_once()
            self.assertEqual(run_mock.call_args.args[0], test_command)
            self.assertEqual(run_mock.call_args.kwargs["timeout_seconds"], 13)
            self.assertEqual(result.metadata["test_source"], "agents_md")
            proposal = PatchStore(root / ".codex-mini" / "patches").load(preview.metadata["patch_id"])
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.metadata["test_result"]["source"], "agents_md")

    def test_apply_patch_test_failure_keeps_patch_applied_with_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")
            _registry, runner = build_default_runner(root)
            preview = runner.execute(
                "write_file",
                json.dumps({"path": "sample.txt", "content": "new\n", "dry_run": True}),
                approved=True,
            )

            with patch.object(
                runner.ctx.command_executor,
                "run",
                return_value=CommandExecResult(False, 1, "", "FAILED sample test\n"),
            ):
                result = runner.execute(
                    "apply_patch",
                    json.dumps(
                        {
                            "patch_id": preview.metadata["patch_id"],
                            "test_command": "python -m unittest discover",
                        }
                    ),
                    approved=True,
                )

            self.assertTrue(result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(result.metadata["patch_status"], PatchStatus.APPLIED.value)
            self.assertFalse(result.metadata["test_ok"])
            self.assertEqual(result.metadata["test_exit_code"], 1)
            self.assertIn("FAILED sample test", result.metadata["test_output"])
            self.assertIn("测试:", result.content)
            proposal = PatchStore(root / ".codex-mini" / "patches").load(preview.metadata["patch_id"])
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.APPLIED)
            self.assertFalse(proposal.metadata["test_result"]["ok"])

    def test_apply_patch_blocks_dangerous_test_command_without_running_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")
            _registry, runner = build_default_runner(root)
            preview = runner.execute(
                "write_file",
                json.dumps({"path": "sample.txt", "content": "new\n", "dry_run": True}),
                approved=True,
            )

            with patch.object(runner.ctx.command_executor, "run") as run_mock:
                result = runner.execute(
                    "apply_patch",
                    json.dumps(
                        {
                            "patch_id": preview.metadata["patch_id"],
                            "test_command": "rm -rf /",
                        }
                    ),
                    approved=True,
                )

            self.assertTrue(result.ok)
            run_mock.assert_not_called()
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            self.assertFalse(result.metadata["test_ok"])
            self.assertTrue(result.metadata["test_result"]["blocked"])
            self.assertEqual(result.metadata["test_result"]["category"], "dangerous_shell")

    def test_apply_patch_selected_paths_keeps_remaining_proposal_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("old first\n", encoding="utf-8")
            second.write_text("old second\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="partial-apply",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "partial-apply", "selected_paths": ["first.txt"]}),
                approved=True,
            )

            self.assertTrue(result.ok)
            self.assertEqual(first.read_text(encoding="utf-8"), "new first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old second\n")
            self.assertTrue(result.metadata["partial_apply"])
            self.assertEqual(result.metadata["patch_status"], PatchStatus.PROPOSED.value)
            proposal = store.load("partial-apply")
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.PROPOSED)
            self.assertEqual(proposal.changed_paths, ["second.txt"])
            self.assertEqual(proposal.metadata["applied_paths"], ["first.txt"])
            self.assertEqual(proposal.metadata["remaining_paths"], ["second.txt"])

    def test_reject_patch_marks_proposal_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")
            _registry, runner = build_default_runner(root)
            preview = runner.execute(
                "write_file",
                json.dumps({"path": "sample.txt", "content": "new\n", "dry_run": True}),
                approved=True,
            )

            result = runner.execute(
                "reject_patch",
                json.dumps({"patch_id": preview.metadata["patch_id"], "reason": "不需要"}),
                approved=True,
            )

            self.assertTrue(result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(result.metadata["patch_status"], PatchStatus.REJECTED.value)
            proposal = PatchStore(root / ".codex-mini" / "patches").load(preview.metadata["patch_id"])
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.REJECTED)
            self.assertEqual(proposal.metadata["reason"], "不需要")

    def test_rollback_patch_restores_applied_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")
            _registry, runner = build_default_runner(root)
            preview = runner.execute(
                "write_file",
                json.dumps({"path": "sample.txt", "content": "new\n", "dry_run": True}),
                approved=True,
            )
            runner.execute(
                "apply_patch",
                json.dumps({"patch_id": preview.metadata["patch_id"]}),
                approved=True,
            )

            result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": preview.metadata["patch_id"]}),
                approved=True,
            )

            self.assertTrue(result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(result.metadata["patch_status"], PatchStatus.ROLLED_BACK.value)
            proposal = PatchStore(root / ".codex-mini" / "patches").load(preview.metadata["patch_id"])
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.ROLLED_BACK)
            self.assertTrue(proposal.metadata["rolled_back"])

    def test_rollback_patch_restores_partial_apply_to_full_pending_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("old first\n", encoding="utf-8")
            second.write_text("old second\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="partial-rollback",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            _registry, runner = build_default_runner(root)

            first_apply = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "partial-rollback", "selected_paths": ["first.txt"]}),
                approved=True,
            )
            self.assertTrue(first_apply.ok)
            self.assertEqual(first.read_text(encoding="utf-8"), "new first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old second\n")

            result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "partial-rollback"}),
                approved=True,
            )

            self.assertTrue(result.ok)
            self.assertEqual(first.read_text(encoding="utf-8"), "old first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old second\n")
            self.assertEqual(result.metadata["patch_status"], PatchStatus.PROPOSED.value)
            proposal = store.load("partial-rollback")
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.PROPOSED)
            self.assertEqual(proposal.changed_paths, ["first.txt", "second.txt"])
            self.assertTrue(proposal.metadata["rolled_back"])
            self.assertFalse(proposal.metadata["partial_apply"])

    def test_rollback_patch_after_multi_step_apply_restores_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("old first\n", encoding="utf-8")
            second.write_text("old second\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="multi-step-rollback",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            _registry, runner = build_default_runner(root)

            runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "multi-step-rollback", "selected_paths": ["first.txt"]}),
                approved=True,
            )
            apply_remaining = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "multi-step-rollback"}),
                approved=True,
            )
            self.assertTrue(apply_remaining.ok)
            applied = store.load("multi-step-rollback")
            self.assertIsNotNone(applied)
            assert applied is not None
            self.assertEqual(applied.status, PatchStatus.APPLIED)
            self.assertEqual(applied.changed_paths, ["first.txt", "second.txt"])

            result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "multi-step-rollback"}),
                approved=True,
            )

            self.assertTrue(result.ok)
            self.assertEqual(first.read_text(encoding="utf-8"), "old first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old second\n")
            rolled_back = store.load("multi-step-rollback")
            self.assertIsNotNone(rolled_back)
            assert rolled_back is not None
            self.assertEqual(rolled_back.status, PatchStatus.ROLLED_BACK)

    def test_rollback_patch_rechecks_workspace_write_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ".env"
            target.write_text("SECRET=new\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            proposal = build_patch_proposal(
                patch_id="unsafe-env-rollback",
                session_id="default",
                turn_id="turn",
                cwd=root,
                changes=[
                    build_file_change(
                        ".env",
                        "SECRET=old\n",
                        "SECRET=new\n",
                        existed_before=True,
                        exists_after=True,
                    )
                ],
            ).with_state(status=PatchStatus.APPLIED, applied_at=1.0)
            store.save(proposal)
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "unsafe-env-rollback"}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "SECRET=new\n")
            failed = store.load("unsafe-env-rollback")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.APPLIED)
            self.assertIn("rollback_failure", failed.metadata)

    def test_partial_apply_cross_root_move_can_rollback_to_full_pending_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external = temp_root / "external-write"
            root.mkdir()
            external.mkdir()
            source = root / "move-me.txt"
            second = root / "second.txt"
            target = external / "moved.txt"
            source.write_text("old move\n", encoding="utf-8")
            second.write_text("old second\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="partial-cross-root-move",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "move-me.txt",
                            "old move\n",
                            "new move\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=target.as_posix(),
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[AdditionalRoot(external, "write")],
                ),
            )

            apply_result = runner.execute(
                "apply_patch",
                json.dumps(
                    {
                        "patch_id": "partial-cross-root-move",
                        "selected_paths": [target.as_posix()],
                    }
                ),
                approved=True,
            )

            self.assertTrue(apply_result.ok)
            self.assertTrue(apply_result.metadata["partial_apply"])
            self.assertFalse(source.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "new move\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old second\n")
            partial = store.load("partial-cross-root-move")
            self.assertIsNotNone(partial)
            assert partial is not None
            self.assertEqual(partial.status, PatchStatus.PROPOSED)
            self.assertEqual(partial.changed_paths, ["second.txt"])

            rollback_result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "partial-cross-root-move"}),
                approved=True,
            )

            self.assertTrue(rollback_result.ok)
            self.assertEqual(rollback_result.metadata["patch_status"], PatchStatus.PROPOSED.value)
            self.assertEqual(source.read_text(encoding="utf-8"), "old move\n")
            self.assertFalse(target.exists())
            self.assertEqual(second.read_text(encoding="utf-8"), "old second\n")
            rolled_back = store.load("partial-cross-root-move")
            self.assertIsNotNone(rolled_back)
            assert rolled_back is not None
            self.assertEqual(rolled_back.status, PatchStatus.PROPOSED)
            self.assertEqual(rolled_back.changed_paths, ["move-me.txt", "second.txt"])
            self.assertFalse(rolled_back.metadata["partial_apply"])

    def test_multi_step_apply_with_cross_root_move_can_rollback_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external = temp_root / "external-write"
            root.mkdir()
            external.mkdir()
            source = root / "move-me.txt"
            second = root / "second.txt"
            target = external / "moved.txt"
            source.write_text("old move\n", encoding="utf-8")
            second.write_text("old second\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="multi-step-cross-root-move",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "move-me.txt",
                            "old move\n",
                            "new move\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=target.as_posix(),
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[AdditionalRoot(external, "write")],
                ),
            )

            first_apply = runner.execute(
                "apply_patch",
                json.dumps(
                    {
                        "patch_id": "multi-step-cross-root-move",
                        "selected_paths": [target.as_posix()],
                    }
                ),
                approved=True,
            )
            self.assertTrue(first_apply.ok)
            self.assertFalse(source.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "new move\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old second\n")

            second_apply = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "multi-step-cross-root-move"}),
                approved=True,
            )
            self.assertTrue(second_apply.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "new move\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "new second\n")
            applied = store.load("multi-step-cross-root-move")
            self.assertIsNotNone(applied)
            assert applied is not None
            self.assertEqual(applied.status, PatchStatus.APPLIED)

            rollback_result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "multi-step-cross-root-move"}),
                approved=True,
            )

            self.assertTrue(rollback_result.ok)
            self.assertEqual(source.read_text(encoding="utf-8"), "old move\n")
            self.assertFalse(target.exists())
            self.assertEqual(second.read_text(encoding="utf-8"), "old second\n")
            rolled_back = store.load("multi-step-cross-root-move")
            self.assertIsNotNone(rolled_back)
            assert rolled_back is not None
            self.assertEqual(rolled_back.status, PatchStatus.ROLLED_BACK)

    def test_apply_and_rollback_patch_support_multiple_cross_root_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external = temp_root / "external-write"
            root.mkdir()
            external.mkdir()
            first_source = root / "first.txt"
            second_source = root / "second.txt"
            first_target = external / "renamed-first.txt"
            second_target = external / "nested" / "renamed-second.txt"
            first_source.write_text("old first\n", encoding="utf-8")
            second_source.write_text("old second\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="multi-cross-root-move",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=first_target.as_posix(),
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=second_target.as_posix(),
                        ),
                    ],
                )
            )
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[AdditionalRoot(external, "write")],
                ),
            )

            apply_result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "multi-cross-root-move"}),
                approved=True,
            )

            self.assertTrue(apply_result.ok)
            self.assertFalse(first_source.exists())
            self.assertFalse(second_source.exists())
            self.assertEqual(first_target.read_text(encoding="utf-8"), "new first\n")
            self.assertEqual(second_target.read_text(encoding="utf-8"), "new second\n")

            rollback_result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "multi-cross-root-move"}),
                approved=True,
            )

            self.assertTrue(rollback_result.ok)
            self.assertEqual(first_source.read_text(encoding="utf-8"), "old first\n")
            self.assertEqual(second_source.read_text(encoding="utf-8"), "old second\n")
            self.assertFalse(first_target.exists())
            self.assertFalse(second_target.exists())
            proposal = store.load("multi-cross-root-move")
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.ROLLED_BACK)

    def test_apply_and_rollback_patch_support_moves_across_multiple_writable_additional_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external_one = temp_root / "external-write-one"
            external_two = temp_root / "external-write-two"
            root.mkdir()
            external_one.mkdir()
            external_two.mkdir()
            first_source = root / "first.txt"
            second_source = root / "second.txt"
            first_target = external_one / "moved-first.txt"
            second_target = external_two / "nested" / "moved-second.txt"
            first_source.write_text("old first\n", encoding="utf-8")
            second_source.write_text("old second\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="multi-additional-root-move",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=first_target.as_posix(),
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=second_target.as_posix(),
                        ),
                    ],
                )
            )
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[
                        AdditionalRoot(external_one, "write"),
                        AdditionalRoot(external_two, "write"),
                    ],
                ),
            )

            apply_result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "multi-additional-root-move"}),
                approved=True,
            )

            self.assertTrue(apply_result.ok)
            self.assertFalse(first_source.exists())
            self.assertFalse(second_source.exists())
            self.assertEqual(first_target.read_text(encoding="utf-8"), "new first\n")
            self.assertEqual(second_target.read_text(encoding="utf-8"), "new second\n")

            rollback_result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "multi-additional-root-move"}),
                approved=True,
            )

            self.assertTrue(rollback_result.ok)
            self.assertEqual(first_source.read_text(encoding="utf-8"), "old first\n")
            self.assertEqual(second_source.read_text(encoding="utf-8"), "old second\n")
            self.assertFalse(first_target.exists())
            self.assertFalse(second_target.exists())
            proposal = store.load("multi-additional-root-move")
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.ROLLED_BACK)

    def test_partial_apply_across_multiple_writable_additional_roots_can_rollback_to_full_pending_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external_one = temp_root / "external-write-one"
            external_two = temp_root / "external-write-two"
            root.mkdir()
            external_one.mkdir()
            external_two.mkdir()
            first_source = root / "first.txt"
            second_source = root / "second.txt"
            first_target = external_one / "moved-first.txt"
            second_target = external_two / "nested" / "moved-second.txt"
            first_source.write_text("old first\n", encoding="utf-8")
            second_source.write_text("old second\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="partial-multi-additional-root-move",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=first_target.as_posix(),
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=second_target.as_posix(),
                        ),
                    ],
                )
            )
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[
                        AdditionalRoot(external_one, "write"),
                        AdditionalRoot(external_two, "write"),
                    ],
                ),
            )

            apply_result = runner.execute(
                "apply_patch",
                json.dumps(
                    {
                        "patch_id": "partial-multi-additional-root-move",
                        "selected_paths": [second_target.as_posix()],
                    }
                ),
                approved=True,
            )

            self.assertTrue(apply_result.ok)
            self.assertTrue(apply_result.metadata["partial_apply"])
            self.assertEqual(first_source.read_text(encoding="utf-8"), "old first\n")
            self.assertFalse(second_source.exists())
            self.assertFalse(first_target.exists())
            self.assertEqual(second_target.read_text(encoding="utf-8"), "new second\n")
            partial = store.load("partial-multi-additional-root-move")
            self.assertIsNotNone(partial)
            assert partial is not None
            self.assertEqual(partial.status, PatchStatus.PROPOSED)
            self.assertEqual(partial.changed_paths, ["first.txt"])

            rollback_result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "partial-multi-additional-root-move"}),
                approved=True,
            )

            self.assertTrue(rollback_result.ok)
            self.assertEqual(rollback_result.metadata["patch_status"], PatchStatus.PROPOSED.value)
            self.assertEqual(first_source.read_text(encoding="utf-8"), "old first\n")
            self.assertEqual(second_source.read_text(encoding="utf-8"), "old second\n")
            self.assertFalse(first_target.exists())
            self.assertFalse(second_target.exists())
            rolled_back = store.load("partial-multi-additional-root-move")
            self.assertIsNotNone(rolled_back)
            assert rolled_back is not None
            self.assertEqual(rolled_back.status, PatchStatus.PROPOSED)
            self.assertEqual(rolled_back.changed_paths, ["first.txt", "second.txt"])
            self.assertFalse(rolled_back.metadata["partial_apply"])

    def test_git_review_tools_report_status_diff_and_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            tracked = root / "tracked.txt"
            tracked.write_text("old\n", encoding="utf-8")
            run_git(root, "add", "tracked.txt")
            run_git(root, "commit", "-m", "initial")
            tracked.write_text("new\n", encoding="utf-8")
            (root / "untracked.txt").write_text("draft\n", encoding="utf-8")
            _registry, runner = build_default_runner(root)

            status = runner.execute("git_status", json.dumps({}))
            diff = runner.execute("git_diff_file", json.dumps({"path": "tracked.txt"}))
            changed = runner.execute("git_changed_files", json.dumps({}))

            self.assertTrue(status.ok)
            self.assertIn("tracked.txt", status.content)
            self.assertIn("untracked.txt", status.content)
            self.assertTrue(diff.ok)
            self.assertIn("-old", diff.content)
            self.assertIn("+new", diff.content)
            self.assertTrue(changed.ok)
            paths = {item["path"] for item in changed.metadata["changed_files"]}
            self.assertEqual(paths, {"tracked.txt", "untracked.txt"})

    def test_git_diff_is_limited_to_selected_root_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_git_repo(repo)
            selected = repo / "pkg"
            selected.mkdir()
            inside = selected / "inside.txt"
            outside = repo / "outside.txt"
            inside.write_text("old in\n", encoding="utf-8")
            outside.write_text("old out\n", encoding="utf-8")
            run_git(repo, "add", "pkg/inside.txt", "outside.txt")
            run_git(repo, "commit", "-m", "initial")
            inside.write_text("new in\n", encoding="utf-8")
            outside.write_text("new out\n", encoding="utf-8")
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    selected,
                    project_root=repo,
                    current_dir=selected,
                ),
            )

            result = runner.execute("git_diff", json.dumps({}))

            self.assertTrue(result.ok)
            self.assertIn("pkg/inside.txt", result.content)
            self.assertNotIn("outside.txt", result.content)

    def test_git_tools_fall_back_to_selected_root_when_current_dir_is_additional_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            extra = Path(tmp) / "extra"
            repo.mkdir()
            extra.mkdir()
            init_git_repo(repo)
            tracked = repo / "tracked.txt"
            tracked.write_text("old\n", encoding="utf-8")
            run_git(repo, "add", "tracked.txt")
            run_git(repo, "commit", "-m", "initial")
            tracked.write_text("new\n", encoding="utf-8")
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    repo,
                    project_root=repo,
                    current_dir=extra,
                    additional_roots=[AdditionalRoot(extra, "read")],
                ),
            )

            status = runner.execute("git_status", json.dumps({}))
            diff = runner.execute("git_diff", json.dumps({}))
            changed = runner.execute("git_changed_files", json.dumps({}))

            self.assertTrue(status.ok)
            self.assertIn("tracked.txt", status.content)
            self.assertTrue(diff.ok)
            self.assertIn("+new", diff.content)
            self.assertTrue(changed.ok)
            self.assertEqual(changed.metadata["scope"], repo.resolve().as_posix())

    def test_git_diff_file_rejects_path_outside_workspace_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_git_repo(repo)
            selected = repo / "pkg"
            selected.mkdir()
            outside = repo / "outside.txt"
            outside.write_text("old\n", encoding="utf-8")
            run_git(repo, "add", "outside.txt")
            run_git(repo, "commit", "-m", "initial")
            outside.write_text("new\n", encoding="utf-8")
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    selected,
                    project_root=repo,
                    current_dir=selected,
                ),
            )

            result = runner.execute(
                "git_diff_file",
                json.dumps({"path": outside.as_posix()}),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["error_type"], "permission_denied")

    def test_git_status_fails_cleanly_outside_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _registry, runner = build_default_runner(Path(tmp))

            result = runner.execute("git_status", json.dumps({}))

            self.assertFalse(result.ok)
            self.assertIn("当前目录不在 Git 仓库中", result.content)

    def test_apply_patch_rechecks_workspace_write_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = PatchStore(root / ".codex-mini" / "patches")
            proposal = build_patch_proposal(
                patch_id="unsafe-env",
                session_id="default",
                turn_id="turn",
                cwd=root,
                changes=[build_file_change(".env", "", "SECRET=1\n", existed_before=False)],
            )
            store.save(proposal)
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "unsafe-env"}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertFalse((root / ".env").exists())
            failed = store.load("unsafe-env")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.FAILED)

    def test_apply_patch_git_preflight_blocks_stale_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")
            run_git(root, "add", "sample.txt")
            run_git(root, "commit", "-m", "initial")
            _registry, runner = build_default_runner(root)
            preview = runner.execute(
                "write_file",
                json.dumps({"path": "sample.txt", "content": "new\n", "dry_run": True}),
                approved=True,
            )
            target.write_text("drifted\n", encoding="utf-8")

            result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": preview.metadata["patch_id"]}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "drifted\n")
            self.assertEqual(result.metadata["failure_stage"], "preflight")
            failed = PatchStore(root / ".codex-mini" / "patches").load(preview.metadata["patch_id"])
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.FAILED)
            self.assertEqual(failed.metadata["failure_stage"], "preflight")

    def test_rollback_patch_git_preflight_blocks_drifted_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            target = root / "sample.txt"
            target.write_text("old\n", encoding="utf-8")
            run_git(root, "add", "sample.txt")
            run_git(root, "commit", "-m", "initial")
            _registry, runner = build_default_runner(root)
            preview = runner.execute(
                "write_file",
                json.dumps({"path": "sample.txt", "content": "new\n", "dry_run": True}),
                approved=True,
            )
            apply_result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": preview.metadata["patch_id"]}),
                approved=True,
            )
            self.assertTrue(apply_result.ok)
            target.write_text("drifted\n", encoding="utf-8")

            result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": preview.metadata["patch_id"]}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "drifted\n")
            self.assertEqual(result.metadata["failure_stage"], "preflight")
            failed = PatchStore(root / ".codex-mini" / "patches").load(preview.metadata["patch_id"])
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.APPLIED)
            self.assertEqual(failed.metadata["rollback_failure_stage"], "preflight")

    def test_apply_patch_persists_partially_written_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.txt"
            second = root / "second.txt"
            third = root / "third.txt"
            first.write_text("old first\n", encoding="utf-8")
            second.write_text("old second\n", encoding="utf-8")
            third.write_text("old third\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="write-failure-diagnostics",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                        build_file_change(
                            "third.txt",
                            "old third\n",
                            "new third\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            _registry, runner = build_default_runner(root)

            from tools.patching import patch_review

            original_write_change = patch_review._write_change
            call_count = {"value": 0}

            def flaky_write_change(*args, **kwargs):
                call_count["value"] += 1
                original_write_change(*args, **kwargs)
                if call_count["value"] == 2:
                    raise OSError("simulated write failure")

            with patch("tools.patching.patch_review._write_change", side_effect=flaky_write_change):
                result = runner.execute(
                    "apply_patch",
                    json.dumps({"patch_id": "write-failure-diagnostics"}),
                    approved=True,
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "write")
            self.assertEqual(result.metadata["written_paths"], ["first.txt"])
            self.assertEqual(result.metadata["failed_path"], "second.txt")
            self.assertEqual(result.metadata["remaining_paths"], ["third.txt"])
            self.assertTrue(result.metadata["partially_written"])
            self.assertEqual(first.read_text(encoding="utf-8"), "new first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "new second\n")
            self.assertEqual(third.read_text(encoding="utf-8"), "old third\n")
            failed = store.load("write-failure-diagnostics")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.FAILED)
            self.assertEqual(failed.metadata["written_paths"], ["first.txt"])
            self.assertEqual(failed.metadata["failed_path"], "second.txt")
            self.assertEqual(failed.metadata["remaining_paths"], ["third.txt"])
            self.assertTrue(failed.metadata["partially_written"])

    def test_rollback_patch_persists_partially_written_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.txt"
            second = root / "second.txt"
            third = root / "third.txt"
            first.write_text("old first\n", encoding="utf-8")
            second.write_text("old second\n", encoding="utf-8")
            third.write_text("old third\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="rollback-failure-diagnostics",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                        build_file_change(
                            "third.txt",
                            "old third\n",
                            "new third\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            _registry, runner = build_default_runner(root)
            apply_result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "rollback-failure-diagnostics"}),
                approved=True,
            )
            self.assertTrue(apply_result.ok)

            from tools.patching import patch_review

            original_rollback_change = patch_review._rollback_change
            call_count = {"value": 0}

            def flaky_rollback_change(*args, **kwargs):
                call_count["value"] += 1
                original_rollback_change(*args, **kwargs)
                if call_count["value"] == 2:
                    raise OSError("simulated rollback failure")

            with patch(
                "tools.patching.patch_review._rollback_change",
                side_effect=flaky_rollback_change,
            ):
                result = runner.execute(
                    "rollback_patch",
                    json.dumps({"patch_id": "rollback-failure-diagnostics"}),
                    approved=True,
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "write")
            self.assertEqual(result.metadata["rolled_back_paths"], ["first.txt"])
            self.assertEqual(result.metadata["failed_path"], "second.txt")
            self.assertEqual(result.metadata["remaining_paths"], ["third.txt"])
            self.assertTrue(result.metadata["partially_written"])
            self.assertEqual(first.read_text(encoding="utf-8"), "old first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old second\n")
            self.assertEqual(third.read_text(encoding="utf-8"), "new third\n")
            failed = store.load("rollback-failure-diagnostics")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.APPLIED)
            self.assertEqual(failed.metadata["rollback_paths"], ["first.txt"])
            self.assertEqual(failed.metadata["rollback_failed_path"], "second.txt")
            self.assertEqual(failed.metadata["rollback_remaining_paths"], ["third.txt"])
            self.assertTrue(failed.metadata["rollback_partially_written"])

    def test_partial_apply_rollback_failure_reopens_completed_changes_and_allows_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.txt"
            second = root / "second.txt"
            third = root / "third.txt"
            first.write_text("old first\n", encoding="utf-8")
            second.write_text("old second\n", encoding="utf-8")
            third.write_text("old third\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="partial-rollback-failure-retry",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                        build_file_change(
                            "third.txt",
                            "old third\n",
                            "new third\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            _registry, runner = build_default_runner(root)

            apply_result = runner.execute(
                "apply_patch",
                json.dumps(
                    {
                        "patch_id": "partial-rollback-failure-retry",
                        "selected_paths": ["first.txt", "second.txt"],
                    }
                ),
                approved=True,
            )
            self.assertTrue(apply_result.ok)
            self.assertEqual(first.read_text(encoding="utf-8"), "new first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "new second\n")
            self.assertEqual(third.read_text(encoding="utf-8"), "old third\n")

            from tools.patching import patch_review

            original_rollback_change = patch_review._rollback_change
            call_count = {"value": 0}

            def flaky_rollback_change(*args, **kwargs):
                call_count["value"] += 1
                if call_count["value"] == 2:
                    raise OSError("simulated rollback failure before write")
                return original_rollback_change(*args, **kwargs)

            with patch(
                "tools.patching.patch_review._rollback_change",
                side_effect=flaky_rollback_change,
            ):
                result = runner.execute(
                    "rollback_patch",
                    json.dumps({"patch_id": "partial-rollback-failure-retry"}),
                    approved=True,
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "write")
            self.assertEqual(result.metadata["rolled_back_paths"], ["first.txt"])
            self.assertEqual(result.metadata["failed_path"], "second.txt")
            self.assertEqual(result.metadata["remaining_paths"], [])
            self.assertTrue(result.metadata["partially_written"])
            self.assertEqual(first.read_text(encoding="utf-8"), "old first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "new second\n")
            self.assertEqual(third.read_text(encoding="utf-8"), "old third\n")

            failed = store.load("partial-rollback-failure-retry")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.PROPOSED)
            self.assertEqual(failed.changed_paths, ["first.txt", "third.txt"])
            self.assertTrue(failed.metadata["partial_apply"])
            self.assertEqual(failed.metadata["applied_paths"], ["second.txt"])
            remaining_applied = failed.metadata["applied_changes"]
            self.assertEqual(len(remaining_applied), 1)
            self.assertEqual(remaining_applied[0]["path"], "second.txt")
            self.assertEqual(failed.metadata["rollback_paths"], ["first.txt"])
            self.assertEqual(failed.metadata["rollback_failed_path"], "second.txt")

            retry_result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "partial-rollback-failure-retry"}),
                approved=True,
            )

            self.assertTrue(retry_result.ok)
            self.assertEqual(retry_result.metadata["patch_status"], PatchStatus.PROPOSED.value)
            self.assertEqual(first.read_text(encoding="utf-8"), "old first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old second\n")
            self.assertEqual(third.read_text(encoding="utf-8"), "old third\n")
            retried = store.load("partial-rollback-failure-retry")
            self.assertIsNotNone(retried)
            assert retried is not None
            self.assertEqual(retried.status, PatchStatus.PROPOSED)
            self.assertEqual(retried.changed_paths, ["first.txt", "second.txt", "third.txt"])
            self.assertFalse(retried.metadata["partial_apply"])
            self.assertEqual(retried.metadata["applied_changes"], [])

    def test_apply_patch_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real.txt"
            real.write_text("old\n", encoding="utf-8")
            link = root / "link.txt"
            link.symlink_to(real)
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="symlink-target",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "link.txt",
                            "old\n",
                            "new\n",
                            existed_before=True,
                            exists_after=True,
                        )
                    ],
                )
            )
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "symlink-target"}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "validate")
            self.assertEqual(real.read_text(encoding="utf-8"), "old\n")
            failed = store.load("symlink-target")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.FAILED)

    def test_apply_patch_rejects_binary_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "data.bin"
            target.write_bytes(b"\x00\x01\x02")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="binary-target",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "data.bin",
                            "",
                            "text\n",
                            existed_before=True,
                            exists_after=True,
                        )
                    ],
                )
            )
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "binary-target"}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "validate")
            self.assertEqual(target.read_bytes(), b"\x00\x01\x02")
            failed = store.load("binary-target")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.FAILED)

    def test_rollback_patch_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real.txt"
            real.write_text("new\n", encoding="utf-8")
            link = root / "link.txt"
            link.symlink_to(real)
            store = PatchStore(root / ".codex-mini" / "patches")
            proposal = build_patch_proposal(
                patch_id="rollback-symlink-target",
                session_id="default",
                turn_id="turn",
                cwd=root,
                changes=[
                    build_file_change(
                        "link.txt",
                        "old\n",
                        "new\n",
                        existed_before=True,
                        exists_after=True,
                    )
                ],
            ).with_state(status=PatchStatus.APPLIED, applied_at=1.0)
            store.save(proposal)
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "rollback-symlink-target"}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "rollback_validate")
            self.assertEqual(real.read_text(encoding="utf-8"), "new\n")
            failed = store.load("rollback-symlink-target")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.APPLIED)
            self.assertEqual(failed.metadata["rollback_failure_stage"], "rollback_validate")

    def test_rollback_patch_rejects_binary_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "data.bin"
            target.write_bytes(b"\x00\x01\x02")
            store = PatchStore(root / ".codex-mini" / "patches")
            proposal = build_patch_proposal(
                patch_id="rollback-binary-target",
                session_id="default",
                turn_id="turn",
                cwd=root,
                changes=[
                    build_file_change(
                        "data.bin",
                        "old\n",
                        "new\n",
                        existed_before=True,
                        exists_after=True,
                    )
                ],
            ).with_state(status=PatchStatus.APPLIED, applied_at=1.0)
            store.save(proposal)
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "rollback-binary-target"}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "rollback_validate")
            self.assertEqual(target.read_bytes(), b"\x00\x01\x02")
            failed = store.load("rollback-binary-target")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.APPLIED)
            self.assertEqual(failed.metadata["rollback_failure_stage"], "rollback_validate")

    def test_apply_patch_rejects_git_metadata_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            target = root / ".git" / "HEAD"
            original = target.read_text(encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="git-metadata-apply",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            ".git/HEAD",
                            original,
                            "ref: refs/heads/other\n",
                            existed_before=True,
                            exists_after=True,
                        )
                    ],
                )
            )
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "git-metadata-apply"}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "write")
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            failed = store.load("git-metadata-apply")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.FAILED)
            self.assertEqual(failed.metadata["failed_paths"], [".git/HEAD"])

    def test_rollback_patch_rejects_git_metadata_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            target = root / ".git" / "HEAD"
            original = target.read_text(encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            proposal = build_patch_proposal(
                patch_id="git-metadata-rollback",
                session_id="default",
                turn_id="turn",
                cwd=root,
                changes=[
                    build_file_change(
                        ".git/HEAD",
                        original,
                        "ref: refs/heads/other\n",
                        existed_before=True,
                        exists_after=True,
                    )
                ],
            ).with_state(status=PatchStatus.APPLIED, applied_at=1.0)
            store.save(proposal)
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "git-metadata-rollback"}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "write")
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            failed = store.load("git-metadata-rollback")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.APPLIED)
            self.assertEqual(failed.metadata["rollback_failure_stage"], "write")

    def test_apply_patch_rejects_remaining_protected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [
                (".codex-mini/runtime.json", "old\n"),
                (".venv/pyvenv.cfg", "home = /tmp/python\n"),
                ("pkg/__pycache__/module.pyc", "compiled\n"),
            ]

            for index, (relative_path, original) in enumerate(cases, start=1):
                with self.subTest(path=relative_path):
                    target = root / relative_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(original, encoding="utf-8")
                    store = PatchStore(root / ".codex-mini" / "patches")
                    patch_id = f"protected-apply-{index}"
                    store.save(
                        build_patch_proposal(
                            patch_id=patch_id,
                            session_id="default",
                            turn_id="turn",
                            cwd=root,
                            changes=[
                                build_file_change(
                                    relative_path,
                                    original,
                                    "new\n",
                                    existed_before=True,
                                    exists_after=True,
                                )
                            ],
                        )
                    )
                    _registry, runner = build_default_runner(root)

                    result = runner.execute(
                        "apply_patch",
                        json.dumps({"patch_id": patch_id}),
                        approved=True,
                    )

                    self.assertFalse(result.ok)
                    self.assertEqual(result.metadata["failure_stage"], "write")
                    self.assertEqual(result.metadata["remaining_paths"], [relative_path])
                    self.assertEqual(target.read_text(encoding="utf-8"), original)
                    failed = store.load(patch_id)
                    self.assertIsNotNone(failed)
                    assert failed is not None
                    self.assertEqual(failed.status, PatchStatus.FAILED)
                    self.assertEqual(failed.metadata["failed_paths"], [relative_path])
                    self.assertEqual(failed.metadata["remaining_paths"], [relative_path])

    def test_rollback_patch_rejects_remaining_protected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [
                (".codex-mini/runtime.json", "new\n", "old\n"),
                (".venv/pyvenv.cfg", "new\n", "old\n"),
                ("pkg/__pycache__/module.pyc", "new\n", "old\n"),
            ]

            for index, (relative_path, current_content, previous_content) in enumerate(cases, start=1):
                with self.subTest(path=relative_path):
                    target = root / relative_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(current_content, encoding="utf-8")
                    store = PatchStore(root / ".codex-mini" / "patches")
                    patch_id = f"protected-rollback-{index}"
                    proposal = build_patch_proposal(
                        patch_id=patch_id,
                        session_id="default",
                        turn_id="turn",
                        cwd=root,
                        changes=[
                            build_file_change(
                                relative_path,
                                previous_content,
                                current_content,
                                existed_before=True,
                                exists_after=True,
                            )
                        ],
                    ).with_state(status=PatchStatus.APPLIED, applied_at=1.0)
                    store.save(proposal)
                    _registry, runner = build_default_runner(root)

                    result = runner.execute(
                        "rollback_patch",
                        json.dumps({"patch_id": patch_id}),
                        approved=True,
                    )

                    self.assertFalse(result.ok)
                    self.assertEqual(result.metadata["failure_stage"], "write")
                    self.assertEqual(result.metadata["remaining_paths"], [relative_path])
                    self.assertEqual(target.read_text(encoding="utf-8"), current_content)
                    failed = store.load(patch_id)
                    self.assertIsNotNone(failed)
                    assert failed is not None
                    self.assertEqual(failed.status, PatchStatus.APPLIED)
                    self.assertEqual(failed.metadata["rollback_failure_stage"], "write")
                    self.assertEqual(failed.metadata["rollback_remaining_paths"], [relative_path])

    def test_apply_patch_rejects_move_path_with_protected_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [
                ("plain.txt", ".codex-mini/plain.txt"),
                (".venv/plain.txt", "plain.txt"),
            ]

            for index, (source_path, target_path) in enumerate(cases, start=1):
                with self.subTest(source=source_path, target=target_path):
                    source = root / source_path
                    if source.parent != root:
                        source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_text("old\n", encoding="utf-8")
                    store = PatchStore(root / ".codex-mini" / "patches")
                    patch_id = f"move-protected-apply-{index}"
                    store.save(
                        build_patch_proposal(
                            patch_id=patch_id,
                            session_id="default",
                            turn_id="turn",
                            cwd=root,
                            changes=[
                                build_file_change(
                                    source_path,
                                    "old\n",
                                    "new\n",
                                    existed_before=True,
                                    exists_after=True,
                                    move_path=target_path,
                                )
                            ],
                        )
                    )
                    _registry, runner = build_default_runner(root)

                    result = runner.execute(
                        "apply_patch",
                        json.dumps({"patch_id": patch_id}),
                        approved=True,
                    )

                    self.assertFalse(result.ok)
                    self.assertEqual(result.metadata["failure_stage"], "write")
                    self.assertEqual(result.metadata["remaining_paths"], [source_path])
                    self.assertEqual(source.read_text(encoding="utf-8"), "old\n")
                    failed = store.load(patch_id)
                    self.assertIsNotNone(failed)
                    assert failed is not None
                    self.assertEqual(failed.status, PatchStatus.FAILED)
                    self.assertEqual(failed.metadata["failed_paths"], [source_path])

    def test_rollback_patch_rejects_move_path_with_protected_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [
                ("plain.txt", ".codex-mini/plain.txt"),
                (".venv/plain.txt", "plain.txt"),
            ]

            for index, (source_path, target_path) in enumerate(cases, start=1):
                with self.subTest(source=source_path, target=target_path):
                    current = root / target_path
                    if current.parent != root:
                        current.parent.mkdir(parents=True, exist_ok=True)
                    current.write_text("new\n", encoding="utf-8")
                    store = PatchStore(root / ".codex-mini" / "patches")
                    patch_id = f"move-protected-rollback-{index}"
                    proposal = build_patch_proposal(
                        patch_id=patch_id,
                        session_id="default",
                        turn_id="turn",
                        cwd=root,
                        changes=[
                            build_file_change(
                                source_path,
                                "old\n",
                                "new\n",
                                existed_before=True,
                                exists_after=True,
                                move_path=target_path,
                            )
                        ],
                    ).with_state(status=PatchStatus.APPLIED, applied_at=1.0)
                    store.save(proposal)
                    _registry, runner = build_default_runner(root)

                    result = runner.execute(
                        "rollback_patch",
                        json.dumps({"patch_id": patch_id}),
                        approved=True,
                    )

                    self.assertFalse(result.ok)
                    self.assertEqual(result.metadata["failure_stage"], "write")
                    self.assertEqual(result.metadata["remaining_paths"], [target_path])
                    self.assertEqual(current.read_text(encoding="utf-8"), "new\n")
                    failed = store.load(patch_id)
                    self.assertIsNotNone(failed)
                    assert failed is not None
                    self.assertEqual(failed.status, PatchStatus.APPLIED)
                    self.assertEqual(failed.metadata["rollback_failure_stage"], "write")
                    self.assertEqual(failed.metadata["rollback_remaining_paths"], [target_path])

    def test_apply_patch_rejects_move_path_when_target_file_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("old source\n", encoding="utf-8")
            target.write_text("existing target\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="move-target-conflict-apply",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "source.txt",
                            "old source\n",
                            "new source\n",
                            existed_before=True,
                            exists_after=True,
                            move_path="target.txt",
                        )
                    ],
                )
            )
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "move-target-conflict-apply"}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "validate")
            self.assertEqual(source.read_text(encoding="utf-8"), "old source\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "existing target\n")
            failed = store.load("move-target-conflict-apply")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.FAILED)
            self.assertEqual(failed.metadata["failure_stage"], "validate")

    def test_rollback_patch_rejects_move_path_when_restore_file_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            restore = root / "source.txt"
            current = root / "target.txt"
            restore.write_text("conflicting restore\n", encoding="utf-8")
            current.write_text("new source\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            proposal = build_patch_proposal(
                patch_id="move-target-conflict-rollback",
                session_id="default",
                turn_id="turn",
                cwd=root,
                changes=[
                    build_file_change(
                        "source.txt",
                        "old source\n",
                        "new source\n",
                        existed_before=True,
                        exists_after=True,
                        move_path="target.txt",
                    )
                ],
            ).with_state(status=PatchStatus.APPLIED, applied_at=1.0)
            store.save(proposal)
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "move-target-conflict-rollback"}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "rollback_validate")
            self.assertEqual(restore.read_text(encoding="utf-8"), "conflicting restore\n")
            self.assertEqual(current.read_text(encoding="utf-8"), "new source\n")
            failed = store.load("move-target-conflict-rollback")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.APPLIED)
            self.assertEqual(failed.metadata["rollback_failure_stage"], "rollback_validate")

    def test_apply_and_rollback_patch_support_writable_additional_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external = temp_root / "external-write"
            root.mkdir()
            external.mkdir()
            target = external / "notes.txt"
            target.write_text("old\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="additional-root-write",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            target.as_posix(),
                            "old\n",
                            "new\n",
                            existed_before=True,
                            exists_after=True,
                        )
                    ],
                )
            )
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[AdditionalRoot(external, "write")],
                ),
            )

            apply_result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "additional-root-write"}),
                approved=True,
            )

            self.assertTrue(apply_result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")

            rollback_result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "additional-root-write"}),
                approved=True,
            )

            self.assertTrue(rollback_result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            proposal = store.load("additional-root-write")
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.ROLLED_BACK)

    def test_patch_rejects_read_only_additional_root_for_apply_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external = temp_root / "external-read"
            root.mkdir()
            external.mkdir()
            target = external / "notes.txt"
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[AdditionalRoot(external, "read")],
                ),
            )

            target.write_text("old\n", encoding="utf-8")
            apply_store = PatchStore(root / ".codex-mini" / "patches")
            apply_store.save(
                build_patch_proposal(
                    patch_id="additional-root-read-apply",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            target.as_posix(),
                            "old\n",
                            "new\n",
                            existed_before=True,
                            exists_after=True,
                        )
                    ],
                )
            )

            apply_result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "additional-root-read-apply"}),
                approved=True,
            )

            self.assertFalse(apply_result.ok)
            self.assertEqual(apply_result.metadata["failure_stage"], "write")
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            failed_apply = apply_store.load("additional-root-read-apply")
            self.assertIsNotNone(failed_apply)
            assert failed_apply is not None
            self.assertEqual(failed_apply.status, PatchStatus.FAILED)

            target.write_text("new\n", encoding="utf-8")
            rollback_store = PatchStore(root / ".codex-mini" / "patches")
            rollback_store.save(
                build_patch_proposal(
                    patch_id="additional-root-read-rollback",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            target.as_posix(),
                            "old\n",
                            "new\n",
                            existed_before=True,
                            exists_after=True,
                        )
                    ],
                ).with_state(status=PatchStatus.APPLIED, applied_at=1.0)
            )

            rollback_result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "additional-root-read-rollback"}),
                approved=True,
            )

            self.assertFalse(rollback_result.ok)
            self.assertEqual(rollback_result.metadata["failure_stage"], "write")
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            failed_rollback = rollback_store.load("additional-root-read-rollback")
            self.assertIsNotNone(failed_rollback)
            assert failed_rollback is not None
            self.assertEqual(failed_rollback.status, PatchStatus.APPLIED)
            self.assertEqual(failed_rollback.metadata["rollback_failure_stage"], "write")

    def test_apply_and_rollback_patch_support_cross_root_move_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external = temp_root / "external-write"
            root.mkdir()
            external.mkdir()
            source = root / "notes.txt"
            target = external / "moved.txt"
            source.write_text("old\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="cross-root-move",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "notes.txt",
                            "old\n",
                            "new\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=target.as_posix(),
                        )
                    ],
                )
            )
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[AdditionalRoot(external, "write")],
                ),
            )

            apply_result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "cross-root-move"}),
                approved=True,
            )

            self.assertTrue(apply_result.ok)
            self.assertFalse(source.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")

            rollback_result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "cross-root-move"}),
                approved=True,
            )

            self.assertTrue(rollback_result.ok)
            self.assertEqual(source.read_text(encoding="utf-8"), "old\n")
            self.assertFalse(target.exists())
            proposal = store.load("cross-root-move")
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.status, PatchStatus.ROLLED_BACK)

    def test_apply_and_rollback_patch_reject_symlink_in_writable_additional_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external = temp_root / "external-write"
            root.mkdir()
            external.mkdir()
            real = external / "real.txt"
            real.write_text("old\n", encoding="utf-8")
            link = external / "link.txt"
            link.symlink_to(real)
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[AdditionalRoot(external, "write")],
                ),
            )

            apply_store = PatchStore(root / ".codex-mini" / "patches")
            apply_store.save(
                build_patch_proposal(
                    patch_id="additional-root-symlink-apply",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            link.as_posix(),
                            "old\n",
                            "new\n",
                            existed_before=True,
                            exists_after=True,
                        )
                    ],
                )
            )

            apply_result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "additional-root-symlink-apply"}),
                approved=True,
            )

            self.assertFalse(apply_result.ok)
            self.assertEqual(apply_result.metadata["failure_stage"], "validate")
            self.assertEqual(real.read_text(encoding="utf-8"), "old\n")

            rollback_store = PatchStore(root / ".codex-mini" / "patches")
            rollback_store.save(
                build_patch_proposal(
                    patch_id="additional-root-symlink-rollback",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            link.as_posix(),
                            "old\n",
                            "new\n",
                            existed_before=True,
                            exists_after=True,
                        )
                    ],
                ).with_state(status=PatchStatus.APPLIED, applied_at=1.0)
            )

            rollback_result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "additional-root-symlink-rollback"}),
                approved=True,
            )

            self.assertFalse(rollback_result.ok)
            self.assertEqual(rollback_result.metadata["failure_stage"], "rollback_validate")
            self.assertEqual(real.read_text(encoding="utf-8"), "old\n")

    def test_apply_and_rollback_patch_reject_binary_in_writable_additional_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external = temp_root / "external-write"
            root.mkdir()
            external.mkdir()
            target = external / "data.bin"
            target.write_bytes(b"\x00\x01\x02")
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[AdditionalRoot(external, "write")],
                ),
            )

            apply_store = PatchStore(root / ".codex-mini" / "patches")
            apply_store.save(
                build_patch_proposal(
                    patch_id="additional-root-binary-apply",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            target.as_posix(),
                            "",
                            "text\n",
                            existed_before=True,
                            exists_after=True,
                        )
                    ],
                )
            )

            apply_result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "additional-root-binary-apply"}),
                approved=True,
            )

            self.assertFalse(apply_result.ok)
            self.assertEqual(apply_result.metadata["failure_stage"], "validate")
            self.assertEqual(target.read_bytes(), b"\x00\x01\x02")

            rollback_store = PatchStore(root / ".codex-mini" / "patches")
            rollback_store.save(
                build_patch_proposal(
                    patch_id="additional-root-binary-rollback",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            target.as_posix(),
                            "old\n",
                            "new\n",
                            existed_before=True,
                            exists_after=True,
                        )
                    ],
                ).with_state(status=PatchStatus.APPLIED, applied_at=1.0)
            )

            rollback_result = runner.execute(
                "rollback_patch",
                json.dumps({"patch_id": "additional-root-binary-rollback"}),
                approved=True,
            )

            self.assertFalse(rollback_result.ok)
            self.assertEqual(rollback_result.metadata["failure_stage"], "rollback_validate")
            self.assertEqual(target.read_bytes(), b"\x00\x01\x02")

    def test_apply_patch_persists_move_failure_diagnostics_for_cross_root_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external = temp_root / "external-write"
            root.mkdir()
            external.mkdir()
            first_source = root / "first.txt"
            second_source = root / "second.txt"
            third_source = root / "third.txt"
            first_target = external / "first-moved.txt"
            second_target = external / "second-moved.txt"
            first_source.write_text("old first\n", encoding="utf-8")
            second_source.write_text("old second\n", encoding="utf-8")
            third_source.write_text("old third\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="move-write-failure-diagnostics",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=first_target.as_posix(),
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=second_target.as_posix(),
                        ),
                        build_file_change(
                            "third.txt",
                            "old third\n",
                            "new third\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[AdditionalRoot(external, "write")],
                ),
            )

            from tools.patching import patch_review

            original_write_change = patch_review._write_change
            call_count = {"value": 0}

            def flaky_write_change(*args, **kwargs):
                call_count["value"] += 1
                original_write_change(*args, **kwargs)
                if call_count["value"] == 2:
                    raise OSError("simulated move write failure")

            with patch("tools.patching.patch_review._write_change", side_effect=flaky_write_change):
                result = runner.execute(
                    "apply_patch",
                    json.dumps({"patch_id": "move-write-failure-diagnostics"}),
                    approved=True,
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "write")
            self.assertEqual(result.metadata["written_paths"], [first_target.as_posix()])
            self.assertEqual(result.metadata["failed_path"], second_target.as_posix())
            self.assertEqual(result.metadata["remaining_paths"], ["third.txt"])
            self.assertTrue(result.metadata["partially_written"])
            self.assertFalse(first_source.exists())
            self.assertFalse(second_source.exists())
            self.assertEqual(first_target.read_text(encoding="utf-8"), "new first\n")
            self.assertEqual(second_target.read_text(encoding="utf-8"), "new second\n")
            self.assertEqual(third_source.read_text(encoding="utf-8"), "old third\n")
            failed = store.load("move-write-failure-diagnostics")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.FAILED)
            self.assertEqual(failed.metadata["written_paths"], [first_target.as_posix()])
            self.assertEqual(failed.metadata["failed_path"], second_target.as_posix())
            self.assertEqual(failed.metadata["remaining_paths"], ["third.txt"])
            self.assertTrue(failed.metadata["partially_written"])

    def test_rollback_patch_persists_move_failure_diagnostics_for_cross_root_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external = temp_root / "external-write"
            root.mkdir()
            external.mkdir()
            first_source = root / "first.txt"
            second_source = root / "second.txt"
            third_source = root / "third.txt"
            first_target = external / "first-moved.txt"
            second_target = external / "second-moved.txt"
            first_source.write_text("old first\n", encoding="utf-8")
            second_source.write_text("old second\n", encoding="utf-8")
            third_source.write_text("old third\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="move-rollback-failure-diagnostics",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=first_target.as_posix(),
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=second_target.as_posix(),
                        ),
                        build_file_change(
                            "third.txt",
                            "old third\n",
                            "new third\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[AdditionalRoot(external, "write")],
                ),
            )

            apply_result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "move-rollback-failure-diagnostics"}),
                approved=True,
            )
            self.assertTrue(apply_result.ok)

            from tools.patching import patch_review

            original_rollback_change = patch_review._rollback_change
            call_count = {"value": 0}

            def flaky_rollback_change(*args, **kwargs):
                call_count["value"] += 1
                original_rollback_change(*args, **kwargs)
                if call_count["value"] == 2:
                    raise OSError("simulated move rollback failure")

            with patch(
                "tools.patching.patch_review._rollback_change",
                side_effect=flaky_rollback_change,
            ):
                result = runner.execute(
                    "rollback_patch",
                    json.dumps({"patch_id": "move-rollback-failure-diagnostics"}),
                    approved=True,
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "write")
            self.assertEqual(result.metadata["rolled_back_paths"], [first_target.as_posix()])
            self.assertEqual(result.metadata["failed_path"], second_target.as_posix())
            self.assertEqual(result.metadata["remaining_paths"], ["third.txt"])
            self.assertTrue(result.metadata["partially_written"])
            self.assertEqual(first_source.read_text(encoding="utf-8"), "old first\n")
            self.assertEqual(second_source.read_text(encoding="utf-8"), "old second\n")
            self.assertFalse(first_target.exists())
            self.assertFalse(second_target.exists())
            self.assertEqual(third_source.read_text(encoding="utf-8"), "new third\n")
            failed = store.load("move-rollback-failure-diagnostics")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.status, PatchStatus.APPLIED)
            self.assertEqual(failed.metadata["rollback_paths"], [first_target.as_posix()])
            self.assertEqual(failed.metadata["rollback_failed_path"], second_target.as_posix())
            self.assertEqual(failed.metadata["rollback_remaining_paths"], ["third.txt"])
            self.assertTrue(failed.metadata["rollback_partially_written"])

    def test_apply_patch_persists_move_failure_diagnostics_across_multiple_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external_one = temp_root / "external-write-one"
            external_two = temp_root / "external-write-two"
            root.mkdir()
            external_one.mkdir()
            external_two.mkdir()
            first_source = root / "first.txt"
            second_source = root / "second.txt"
            third_source = root / "third.txt"
            first_target = external_one / "first-moved.txt"
            second_target = external_two / "second-moved.txt"
            first_source.write_text("old first\n", encoding="utf-8")
            second_source.write_text("old second\n", encoding="utf-8")
            third_source.write_text("old third\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="multi-root-move-write-failure",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=first_target.as_posix(),
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=second_target.as_posix(),
                        ),
                        build_file_change(
                            "third.txt",
                            "old third\n",
                            "new third\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[
                        AdditionalRoot(external_one, "write"),
                        AdditionalRoot(external_two, "write"),
                    ],
                ),
            )

            from tools.patching import patch_review

            original_write_change = patch_review._write_change
            call_count = {"value": 0}

            def flaky_write_change(*args, **kwargs):
                call_count["value"] += 1
                original_write_change(*args, **kwargs)
                if call_count["value"] == 2:
                    raise OSError("simulated multi-root move write failure")

            with patch("tools.patching.patch_review._write_change", side_effect=flaky_write_change):
                result = runner.execute(
                    "apply_patch",
                    json.dumps({"patch_id": "multi-root-move-write-failure"}),
                    approved=True,
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "write")
            self.assertEqual(result.metadata["written_paths"], [first_target.as_posix()])
            self.assertEqual(result.metadata["failed_path"], second_target.as_posix())
            self.assertEqual(result.metadata["remaining_paths"], ["third.txt"])
            failed = store.load("multi-root-move-write-failure")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.metadata["written_paths"], [first_target.as_posix()])
            self.assertEqual(failed.metadata["failed_path"], second_target.as_posix())

    def test_rollback_patch_persists_move_failure_diagnostics_across_multiple_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            root = temp_root / "project"
            external_one = temp_root / "external-write-one"
            external_two = temp_root / "external-write-two"
            root.mkdir()
            external_one.mkdir()
            external_two.mkdir()
            first_source = root / "first.txt"
            second_source = root / "second.txt"
            third_source = root / "third.txt"
            first_target = external_one / "first-moved.txt"
            second_target = external_two / "second-moved.txt"
            first_source.write_text("old first\n", encoding="utf-8")
            second_source.write_text("old second\n", encoding="utf-8")
            third_source.write_text("old third\n", encoding="utf-8")
            store = PatchStore(root / ".codex-mini" / "patches")
            store.save(
                build_patch_proposal(
                    patch_id="multi-root-move-rollback-failure",
                    session_id="default",
                    turn_id="turn",
                    cwd=root,
                    changes=[
                        build_file_change(
                            "first.txt",
                            "old first\n",
                            "new first\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=first_target.as_posix(),
                        ),
                        build_file_change(
                            "second.txt",
                            "old second\n",
                            "new second\n",
                            existed_before=True,
                            exists_after=True,
                            move_path=second_target.as_posix(),
                        ),
                        build_file_change(
                            "third.txt",
                            "old third\n",
                            "new third\n",
                            existed_before=True,
                            exists_after=True,
                        ),
                    ],
                )
            )
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    root,
                    additional_roots=[
                        AdditionalRoot(external_one, "write"),
                        AdditionalRoot(external_two, "write"),
                    ],
                ),
            )

            apply_result = runner.execute(
                "apply_patch",
                json.dumps({"patch_id": "multi-root-move-rollback-failure"}),
                approved=True,
            )
            self.assertTrue(apply_result.ok)

            from tools.patching import patch_review

            original_rollback_change = patch_review._rollback_change
            call_count = {"value": 0}

            def flaky_rollback_change(*args, **kwargs):
                call_count["value"] += 1
                original_rollback_change(*args, **kwargs)
                if call_count["value"] == 2:
                    raise OSError("simulated multi-root move rollback failure")

            with patch(
                "tools.patching.patch_review._rollback_change",
                side_effect=flaky_rollback_change,
            ):
                result = runner.execute(
                    "rollback_patch",
                    json.dumps({"patch_id": "multi-root-move-rollback-failure"}),
                    approved=True,
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["failure_stage"], "write")
            self.assertEqual(result.metadata["rolled_back_paths"], [first_target.as_posix()])
            self.assertEqual(result.metadata["failed_path"], second_target.as_posix())
            self.assertEqual(result.metadata["remaining_paths"], ["third.txt"])
            failed = store.load("multi-root-move-rollback-failure")
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed.metadata["rollback_paths"], [first_target.as_posix()])
            self.assertEqual(failed.metadata["rollback_failed_path"], second_target.as_posix())

    def test_run_command_invalid_cwd_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _registry, runner = build_default_runner(Path(tmp))

            result = runner.execute(
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
            _registry, runner = build_default_runner(root)

            with patch.object(
                runner.ctx.command_executor,
                "run",
                return_value=CommandExecResult(True, 0, "ok\n", ""),
            ) as run_mock:
                result = runner.execute(
                    "run_command",
                    json.dumps({"command": "echo ok", "cwd": "desktop"}),
                    approved=True,
                )

            self.assertTrue(result.ok)
            self.assertIn("cwd: desktop", result.content)
            self.assertEqual(run_mock.call_args.kwargs["cwd"], nested.resolve())
            self.assertIs(run_mock.call_args.kwargs["filesystem_policy"], runner.ctx.filesystem_policy)
            self.assertEqual(run_mock.call_args.kwargs["network_policy"], runner.ctx.network_policy)

    def test_run_command_session_allow_prefix_skips_permission_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _registry, runner = build_runner_with_exec_policy(
                root,
                ExecPolicy.with_session_allow([("npm", "run", "test")]),
            )

            with patch.object(
                runner.ctx.command_executor,
                "run",
                return_value=CommandExecResult(True, 0, "ok\n", ""),
            ) as run_mock:
                result = runner.execute(
                    "run_command",
                    json.dumps({"command": "npm run test -- --watch=false"}),
                )

            self.assertTrue(result.ok)
            self.assertTrue(run_mock.called)

    def test_mutating_file_tool_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "write_file",
                json.dumps({"path": "created.txt", "content": "hello"}),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["permission_action"], "ask")
            self.assertEqual(result.metadata["permission_profile"], "workspace_write")
            self.assertEqual(result.metadata["current_dir"], root.resolve().as_posix())
            self.assertFalse((root / "created.txt").exists())

    def test_runtime_state_tools_require_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _registry, runner = build_default_runner(root)

            run_tests_result = runner.execute("run_tests", json.dumps({}))
            update_plan_result = runner.execute(
                "update_plan",
                json.dumps({"plan": [{"step": "检查", "status": "pending"}]}),
            )

            self.assertFalse(run_tests_result.ok)
            self.assertEqual(run_tests_result.metadata["permission_action"], "ask")
            self.assertFalse(update_plan_result.ok)
            self.assertEqual(update_plan_result.metadata["permission_action"], "ask")
            self.assertFalse((root / ".codex-mini" / "tasks.json").exists())

    def test_mutating_file_tool_runs_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "write_file",
                json.dumps({"path": "created.txt", "content": "hello"}),
                approved=True,
            )

            self.assertTrue(result.ok)
            self.assertEqual((root / "created.txt").read_text(encoding="utf-8"), "hello")

    def test_auto_approval_policy_runs_mutating_file_tool_without_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(root, approval_policy=ApprovalPolicy.AUTO),
            )

            result = runner.execute(
                "write_file",
                json.dumps({"path": "created.txt", "content": "hello"}),
            )

            self.assertTrue(result.ok)
            self.assertEqual((root / "created.txt").read_text(encoding="utf-8"), "hello")

    def test_full_access_profile_allows_file_tool_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            outside = Path(tmp) / "outside"
            workspace.mkdir()
            outside.mkdir()
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    workspace,
                    approval_policy=ApprovalPolicy.AUTO,
                    permission_profile=PermissionProfile.DANGER_NO_SANDBOX,
                ),
            )

            target = outside / "created.txt"
            result = runner.execute(
                "write_file",
                json.dumps({"path": target.as_posix(), "content": "hello"}),
            )

            self.assertTrue(result.ok)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello")

    def test_read_file_denies_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("API_KEY=secret", encoding="utf-8")
            _registry, runner = build_default_runner(root)

            result = runner.execute("read_file", json.dumps({"path": ".env"}))

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["error_type"], "permission_denied")
            self.assertIn("禁止读取", result.content)

    def test_write_file_denies_codex_mini_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _registry, runner = build_default_runner(root)

            result = runner.execute(
                "write_file",
                json.dumps({"path": ".codex-mini/sessions/index.sqlite", "content": "oops"}),
                approved=True,
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.metadata["error_type"], "permission_denied")
            self.assertFalse((root / ".codex-mini" / "sessions" / "index.sqlite").exists())


if __name__ == "__main__":
    unittest.main()

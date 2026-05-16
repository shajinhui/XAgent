from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from context import EnvironmentContext, ModelContext, PermissionsContext, UserContext
from context_manager import ContextManager
from server.runtime.model_config import ModelRequestConfig
from session.turn_context import TurnContext
from tools.core.catalog import build_default_registry
from tools.core.router import ToolRouter
from tools.core.runner import ToolRunner, create_tool_context
from workspace import WorkspaceManager


class ContextModuleTests(unittest.TestCase):
    def test_context_fragments_snapshot_workspace_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceManager(Path(tmp)).open()
            model_config = ModelRequestConfig("openai/gpt-4o-mini", "off")

            environment = EnvironmentContext.from_workspace(workspace)
            permissions = PermissionsContext.from_workspace(workspace)
            model = ModelContext.from_request_config(model_config)
            user = UserContext("system", "hello")

            self.assertEqual(environment.as_dict()["root"], workspace.root.as_posix())
            self.assertEqual(permissions.as_dict()["command_sandbox"], "macos-seatbelt")
            self.assertEqual(model.as_dict()["model"], "openai/gpt-4o-mini")
            self.assertEqual(user.render_fragment(), "hello")

    def test_context_manager_tracks_model_visible_history(self) -> None:
        history = ContextManager.with_system_prompt("system")
        history.append_user_message("hello")
        history.append_assistant_message({"role": "assistant", "content": "ok"})
        history.messages[-1]["reasoning_content"] = "hidden"

        history.clear_historical_reasoning_content()

        self.assertEqual([message["role"] for message in history.messages], ["system", "user", "assistant"])
        self.assertNotIn("reasoning_content", history.messages[-1])

    def test_tool_invocation_carries_turn_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceManager(Path(tmp)).open()
            registry = build_default_registry()
            runner = ToolRunner(registry, create_tool_context(workspace.root, "session-1"))
            history = ContextManager.with_system_prompt("system")
            turn = TurnContext.from_runtime(
                session_id="session-1",
                turn_id="turn-1",
                workspace=workspace,
                session_state=object(),
                registry=registry,
                runner=runner,
                history=history,
                system_prompt="system",
                user_input="write",
                model_config=ModelRequestConfig("openai/gpt-4o-mini", "off"),
            )

            invocation = ToolRouter.build_tool_invocation(
                {
                    "id": "call-1",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": "created.txt", "content": "hello"}),
                    },
                },
                turn,
            )
            result = runner.execute_invocation(invocation.with_approval(True))

            self.assertTrue(result.ok)
            self.assertEqual(invocation.session_id, "session-1")
            self.assertEqual(invocation.turn_id, "turn-1")
            self.assertEqual(invocation.workspace_root, workspace.root)
            self.assertEqual(invocation.diff_tracker.touched_paths, [(workspace.root / "created.txt").resolve().as_posix()])


if __name__ == "__main__":
    unittest.main()
